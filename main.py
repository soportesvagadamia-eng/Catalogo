import os
import re
import time
import sqlite3
import threading
import schedule
import logging
from datetime import datetime
from flask import Flask, jsonify, send_from_directory
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────
RUC      = os.getenv("SERCOP_RUC",      "1000973329001")
USUARIO  = os.getenv("SERCOP_USUARIO",  "CARLINADAVILA")
CLAVE    = os.getenv("SERCOP_CLAVE",    "Cdavila973329*")
DB_FILE  = os.getenv("DB_FILE",         "sercop.db")
PORT     = int(os.getenv("PORT",        "8080"))
INTERVAL = int(os.getenv("INTERVAL_H",  "4"))          # horas entre ejecuciones

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sercop")

app = Flask(__name__, static_folder="static")

# ─── BASE DE DATOS ─────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS ordenes (
            ce          TEXT PRIMARY KEY,
            producto    TEXT,
            modelo      TEXT,
            categoria   TEXT,
            cantidad    INTEGER,
            entidad     TEXT,
            finalizacion TEXT,
            canal       TEXT,
            precio      REAL,
            marca       TEXT,
            primera_vez TEXT,
            ultima_vez  TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            inicio      TEXT,
            fin         TEXT,
            nuevas      INTEGER,
            actualizadas INTEGER,
            error       TEXT
        );
    """)
    con.commit()
    con.close()

def get_db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con

# ─── CLASIFICADOR ──────────────────────────────────────────────────────────────
def clasificar(producto: str) -> str:
    p = producto.upper()
    if "IMPRESORA" in p or "PLOTTER" in p:
        return "IMPRESORAS"
    if "SCANNER" in p or "ESCANER" in p:
        return "OTROS"
    if "TODO EN UNO" in p or "ALL IN ONE" in p:
        tipo = "AIO"
    elif "PORTÁTIL" in p or "PORTATIL" in p:
        tipo = "LAPTOP"
    elif "ESCRITORIO" in p:
        tipo = "ESCRITORIO"
    else:
        return "OTROS"

    if "GENERACIÓN 13" in p or "GENERACION 13" in p or "GEN 13" in p:
        gen = "GEN 13"
    elif "GENERACIÓN 12" in p or "GENERACION 12" in p or "GEN 12" in p:
        gen = "GEN 12"
    else:
        gen = "GEN 12"   # default para catálogos sin mención explícita

    return f"{tipo} {gen}"

def extraer_modelo(producto: str) -> str:
    m = re.search(r"MODELO\s+(\d+)", producto.upper())
    return f"MODELO {m.group(1)}" if m else "SIN MODELO"

# ─── SCRAPING ─────────────────────────────────────────────────────────────────
def init_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=opts)

def login(driver):
    driver.get("https://catalogoelectronico.compraspublicas.gob.ec/")
    WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.LINK_TEXT, "Iniciar sesión"))
    ).click()
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "ruc"))
    ).send_keys(RUC)
    driver.find_element(By.ID, "username").send_keys(USUARIO)
    driver.find_element(By.ID, "password").send_keys(CLAVE)
    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Entrar')]"))
    ).click()
    log.info("Login OK")

def scrape_pendientes(driver):
    driver.get("https://catalogoelectronico.compraspublicas.gob.ec/pendientes")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "body_table_listas"))
    )
    filas = driver.find_elements(By.CSS_SELECTOR, "#body_table_listas tr")
    registros = []
    for fila in filas:
        cols = fila.find_elements(By.TAG_NAME, "td")
        if len(cols) < 4:
            continue
        producto    = cols[0].text.strip().replace("\n", " ")
        cantidad    = cols[1].text.strip()
        entidad     = cols[2].text.strip()
        finalizacion = cols[3].text.strip()

        ce_match = re.search(r"CE-\d+", producto)
        ce = ce_match.group(0) if ce_match else ""
        if not ce:
            continue

        try:
            qty = int(float(re.sub(r"[^\d.]", "", cantidad))) if cantidad else 0
        except Exception:
            qty = 0

        registros.append({
            "ce":           ce,
            "producto":     producto,
            "modelo":       extraer_modelo(producto),
            "categoria":    clasificar(producto),
            "cantidad":     qty,
            "entidad":      entidad,
            "finalizacion": finalizacion,
        })
    return registros

def scrape_asignadas(driver):
    """
    Intenta obtener órdenes ya asignadas (con proveedor ganador).
    Ajusta la URL/selector según la página real de SERCOP.
    """
    try:
        driver.get("https://catalogoelectronico.compraspublicas.gob.ec/asignadas")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "body_table_listas"))
        )
        filas = driver.find_elements(By.CSS_SELECTOR, "#body_table_listas tr")
        asignadas = {}
        for fila in filas:
            cols = fila.find_elements(By.TAG_NAME, "td")
            if len(cols) < 6:
                continue
            ce_match = re.search(r"CE-\d+", cols[0].text)
            if not ce_match:
                continue
            ce     = ce_match.group(0)
            canal  = cols[4].text.strip() if len(cols) > 4 else ""
            precio_txt = cols[5].text.strip() if len(cols) > 5 else ""
            try:
                precio = float(re.sub(r"[^\d.]", "", precio_txt)) if precio_txt else None
            except Exception:
                precio = None
            if canal:
                asignadas[ce] = {"canal": canal, "precio": precio}
        return asignadas
    except Exception as e:
        log.warning(f"No se pudo scrape asignadas: {e}")
        return {}

# ─── SINCRONIZACIÓN ────────────────────────────────────────────────────────────
def sync():
    inicio = datetime.now().isoformat(timespec="seconds")
    nuevas = actualizadas = 0
    error_msg = None
    driver = None
    try:
        log.info("=== Iniciando sincronización ===")
        driver = init_driver()
        login(driver)

        pendientes = scrape_pendientes(driver)
        log.info(f"Scraped {len(pendientes)} órdenes pendientes")

        asignadas = scrape_asignadas(driver)
        log.info(f"Scraped {len(asignadas)} órdenes asignadas")

        ahora = datetime.now().isoformat(timespec="seconds")
        con = get_db()
        cur = con.cursor()

        for r in pendientes:
            ce = r["ce"]
            extra = asignadas.get(ce, {})
            canal  = extra.get("canal", "POR CONFIRMAR")
            precio = extra.get("precio")

            existing = cur.execute("SELECT ce FROM ordenes WHERE ce=?", (ce,)).fetchone()
            if existing:
                cur.execute("""
                    UPDATE ordenes SET
                        cantidad=?, entidad=?, finalizacion=?,
                        canal=?, precio=?, ultima_vez=?
                    WHERE ce=?
                """, (r["cantidad"], r["entidad"], r["finalizacion"],
                      canal, precio, ahora, ce))
                actualizadas += 1
            else:
                cur.execute("""
                    INSERT INTO ordenes
                        (ce, producto, modelo, categoria, cantidad, entidad,
                         finalizacion, canal, precio, marca, primera_vez, ultima_vez)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (ce, r["producto"], r["modelo"], r["categoria"],
                      r["cantidad"], r["entidad"], r["finalizacion"],
                      canal, precio, "", ahora, ahora))
                nuevas += 1

        # Actualizar marca desde tabla asignadas si existe
        for ce, info in asignadas.items():
            cur.execute("UPDATE ordenes SET canal=?, precio=?, ultima_vez=? WHERE ce=?",
                        (info["canal"], info.get("precio"), ahora, ce))

        con.commit()
        con.close()
        log.info(f"Sync OK — nuevas: {nuevas}, actualizadas: {actualizadas}")

    except Exception as e:
        error_msg = str(e)
        log.error(f"Error en sync: {e}")
    finally:
        if driver:
            driver.quit()

    fin = datetime.now().isoformat(timespec="seconds")
    try:
        con = get_db()
        con.execute(
            "INSERT INTO sync_log (inicio,fin,nuevas,actualizadas,error) VALUES (?,?,?,?,?)",
            (inicio, fin, nuevas, actualizadas, error_msg)
        )
        con.commit()
        con.close()
    except Exception:
        pass

# ─── API ───────────────────────────────────────────────────────────────────────
@app.route("/api/resumen")
def api_resumen():
    con = get_db()

    total_ord  = con.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0]
    total_uni  = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM ordenes").fetchone()[0]
    confirmadas = con.execute(
        "SELECT COUNT(*) FROM ordenes WHERE canal != 'POR CONFIRMAR' AND canal != ''"
    ).fetchone()[0]
    sin_conf   = total_ord - confirmadas

    categorias = [dict(r) for r in con.execute("""
        SELECT categoria,
               COUNT(*) as ordenes,
               COALESCE(SUM(cantidad),0) as unidades
        FROM ordenes GROUP BY categoria ORDER BY unidades DESC
    """).fetchall()]

    proveedores = [dict(r) for r in con.execute("""
        SELECT canal,
               COUNT(*) as ordenes,
               COALESCE(SUM(cantidad),0) as unidades
        FROM ordenes
        WHERE canal != 'POR CONFIRMAR' AND canal != ''
        GROUP BY canal ORDER BY unidades DESC LIMIT 12
    """).fetchall()]

    marcas = [dict(r) for r in con.execute("""
        SELECT marca,
               COUNT(*) as ordenes,
               COALESCE(SUM(cantidad),0) as unidades
        FROM ordenes
        WHERE marca != '' AND marca IS NOT NULL
        GROUP BY marca ORDER BY unidades DESC LIMIT 10
    """).fetchall()]

    ultimo_sync = con.execute(
        "SELECT fin, nuevas, actualizadas FROM sync_log ORDER BY id DESC LIMIT 1"
    ).fetchone()

    con.close()

    return jsonify({
        "resumen": {
            "total_ordenes":  total_ord,
            "total_unidades": total_uni,
            "confirmadas":    confirmadas,
            "sin_confirmar":  sin_conf,
        },
        "categorias":   categorias,
        "proveedores":  proveedores,
        "marcas":       marcas,
        "ultimo_sync":  dict(ultimo_sync) if ultimo_sync else {},
    })

@app.route("/api/ordenes")
def api_ordenes():
    from flask import request
    cat    = request.args.get("categoria", "")
    estado = request.args.get("estado", "")
    q      = request.args.get("q", "")
    page   = int(request.args.get("page", 1))
    limit  = 100

    where = []
    params = []
    if cat:
        where.append("categoria = ?"); params.append(cat)
    if estado == "asignada":
        where.append("canal != 'POR CONFIRMAR' AND canal != ''")
    elif estado == "pendiente":
        where.append("(canal = 'POR CONFIRMAR' OR canal = '')")
    if q:
        where.append("(entidad LIKE ? OR ce LIKE ? OR canal LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]

    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * limit

    con = get_db()
    total = con.execute(f"SELECT COUNT(*) FROM ordenes {sql_where}", params).fetchone()[0]
    rows  = [dict(r) for r in con.execute(
        f"""SELECT ce, categoria, modelo, cantidad, entidad,
                   finalizacion, canal, precio, marca, primera_vez, ultima_vez
            FROM ordenes {sql_where}
            ORDER BY ultima_vez DESC LIMIT ? OFFSET ?""",
        params + [limit, offset]
    ).fetchall()]
    con.close()

    return jsonify({"total": total, "page": page, "data": rows})

@app.route("/api/sync", methods=["POST"])
def api_sync_manual():
    thread = threading.Thread(target=sync, daemon=True)
    thread.start()
    return jsonify({"ok": True, "message": "Sincronización iniciada"})

@app.route("/api/sync-log")
def api_sync_log():
    con = get_db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM sync_log ORDER BY id DESC LIMIT 20"
    ).fetchall()]
    con.close()
    return jsonify(rows)

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
def run_scheduler():
    schedule.every(INTERVAL).hours.do(sync)
    log.info(f"Scheduler activo — sync cada {INTERVAL} horas")
    while True:
        schedule.run_pending()
        time.sleep(30)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    # Primera sync al arrancar
    t_sync = threading.Thread(target=sync, daemon=True)
    t_sync.start()
    # Scheduler en background
    t_sched = threading.Thread(target=run_scheduler, daemon=True)
    t_sched.start()
    # Servidor web
    app.run(host="0.0.0.0", port=PORT, debug=False)
