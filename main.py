import os, re, time, sqlite3, threading, schedule, logging
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ─── CONFIG ───────────────────────────────────────────────────────────────────
RUC      = os.getenv("SERCOP_RUC",     "1000973329001")
USUARIO  = os.getenv("SERCOP_USUARIO", "CARLINADAVILA")
CLAVE    = os.getenv("SERCOP_CLAVE",   "Cdavila973329*")
DB_FILE  = os.getenv("DB_FILE",        "sercop.db")
PORT     = int(os.getenv("PORT",       "8080"))
INTERVAL = int(os.getenv("INTERVAL_H", "4"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sercop")
app = Flask(__name__, static_folder="static")

# ─── DB ───────────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS ordenes (
            ce TEXT PRIMARY KEY, producto TEXT, modelo TEXT, categoria TEXT,
            cantidad INTEGER, entidad TEXT, finalizacion TEXT, canal TEXT,
            precio REAL, marca TEXT, primera_vez TEXT, ultima_vez TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inicio TEXT, fin TEXT, nuevas INTEGER, actualizadas INTEGER, error TEXT
        );
    """)
    con.commit(); con.close()

def get_db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con

# ─── CLASIFICAR ───────────────────────────────────────────────────────────────
def clasificar(prod):
    p = prod.upper()
    if "IMPRESORA" in p or "PLOTTER" in p: return "IMPRESORAS"
    if "SCANNER" in p or "ESCANER" in p:   return "OTROS"
    if "TODO EN UNO" in p or "ALL IN ONE" in p: tipo = "AIO"
    elif "PORTÁTIL" in p or "PORTATIL" in p:    tipo = "LAPTOP"
    elif "ESCRITORIO" in p:                      tipo = "ESCRITORIO"
    else: return "OTROS"
    gen = "GEN 13" if ("GENERACIÓN 13" in p or "GENERACION 13" in p) else "GEN 12"
    return f"{tipo} {gen}"

def extraer_modelo(prod):
    m = re.search(r"MODELO\s+(\d+)", prod.upper())
    return f"MODELO {m.group(1)}" if m else "SIN MODELO"

# ─── DRIVER — igual que el bot original pero headless ─────────────────────────
def iniciar_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")

    # Usar SOLO chromium y chromedriver de apt — misma versión garantizada
    import shutil

    chrome_bin  = shutil.which("chromium") or shutil.which("chromium-browser")
    driver_bin  = shutil.which("chromedriver")

    log.info(f"chromium={chrome_bin}  chromedriver={driver_bin}")

    if chrome_bin:
        opts.binary_location = chrome_bin

    if driver_bin:
        return webdriver.Chrome(service=Service(driver_bin), options=opts)

    raise RuntimeError("No se encontró chromium/chromedriver. Verificar Dockerfile.")

# ─── SCRAPING — lógica IDÉNTICA al bot original ───────────────────────────────
def iniciar_sesion(driver):
    driver.get("https://catalogoelectronico.compraspublicas.gob.ec/")
    WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.LINK_TEXT, "Iniciar sesión"))
    ).click()
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "ruc"))
    ).send_keys(RUC)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "username"))
    ).send_keys(USUARIO)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "password"))
    ).send_keys(CLAVE)
    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Entrar')]"))
    ).click()
    log.info("Login OK")
    driver.get("https://catalogoelectronico.compraspublicas.gob.ec/pendientes")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "body_table_listas"))
    )

def scrape_pendientes(driver):
    filas = driver.find_elements(By.CSS_SELECTOR, "#body_table_listas tr")
    log.info(f"Filas encontradas: {len(filas)}")
    registros = []
    for fila in reversed(filas):
        cols = fila.find_elements(By.TAG_NAME, "td")
        if len(cols) < 4: continue
        producto = cols[0].text.strip().replace('\n', ' ')
        ce_match = re.search(r"CE-\d+", producto)
        if not ce_match: continue
        try:   qty = int(float(re.sub(r"[^\d.]", "", cols[1].text.strip())))
        except: qty = 0
        registros.append({
            "ce": ce_match.group(0), "producto": producto,
            "modelo": extraer_modelo(producto),
            "categoria": clasificar(producto),
            "cantidad": qty,
            "entidad": cols[2].text.strip(),
            "finalizacion": cols[3].text.strip(),
        })
    return registros

def scrape_asignadas(driver):
    asignadas = {}
    try:
        driver.get("https://catalogoelectronico.compraspublicas.gob.ec/asignadas")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "body_table_listas"))
        )
        filas = driver.find_elements(By.CSS_SELECTOR, "#body_table_listas tr")
        for fila in filas:
            cols = fila.find_elements(By.TAG_NAME, "td")
            if len(cols) < 4: continue
            ce_match = re.search(r"CE-\d+", cols[0].text)
            if not ce_match: continue
            ce    = ce_match.group(0)
            canal = cols[4].text.strip() if len(cols) > 4 else ""
            try:   precio = float(re.sub(r"[^\d.]", "", cols[5].text)) if len(cols) > 5 else None
            except: precio = None
            if canal: asignadas[ce] = {"canal": canal, "precio": precio}
        log.info(f"Asignadas: {len(asignadas)}")
    except Exception as e:
        log.warning(f"scrape_asignadas: {e}")
    return asignadas

# ─── SYNC ─────────────────────────────────────────────────────────────────────
def sync():
    inicio = datetime.now().isoformat(timespec="seconds")
    nuevas = actualizadas = 0
    error_msg = None
    driver = None
    try:
        log.info("=== Iniciando sync ===")
        driver = iniciar_driver()
        iniciar_sesion(driver)
        pendientes = scrape_pendientes(driver)
        asignadas  = scrape_asignadas(driver)
        ahora = datetime.now().isoformat(timespec="seconds")
        con = get_db(); cur = con.cursor()
        for r in pendientes:
            extra  = asignadas.get(r["ce"], {})
            canal  = extra.get("canal", "POR CONFIRMAR")
            precio = extra.get("precio")
            if cur.execute("SELECT ce FROM ordenes WHERE ce=?", (r["ce"],)).fetchone():
                cur.execute("""UPDATE ordenes SET cantidad=?,entidad=?,finalizacion=?,
                    canal=?,precio=?,ultima_vez=? WHERE ce=?""",
                    (r["cantidad"],r["entidad"],r["finalizacion"],canal,precio,ahora,r["ce"]))
                actualizadas += 1
            else:
                cur.execute("""INSERT INTO ordenes
                    (ce,producto,modelo,categoria,cantidad,entidad,finalizacion,
                     canal,precio,marca,primera_vez,ultima_vez)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["ce"],r["producto"],r["modelo"],r["categoria"],r["cantidad"],
                     r["entidad"],r["finalizacion"],canal,precio,"",ahora,ahora))
                nuevas += 1
        for ce, info in asignadas.items():
            cur.execute("UPDATE ordenes SET canal=?,precio=?,ultima_vez=? WHERE ce=?",
                        (info["canal"],info.get("precio"),ahora,ce))
        con.commit(); con.close()
        log.info(f"Sync OK — nuevas:{nuevas} actualizadas:{actualizadas}")
    except Exception as e:
        error_msg = str(e)
        log.error(f"Error sync: {e}")
    finally:
        if driver:
            try: driver.quit()
            except: pass
    fin = datetime.now().isoformat(timespec="seconds")
    try:
        con = get_db()
        con.execute("INSERT INTO sync_log (inicio,fin,nuevas,actualizadas,error) VALUES (?,?,?,?,?)",
                    (inicio,fin,nuevas,actualizadas,error_msg))
        con.commit(); con.close()
    except: pass

# ─── API ──────────────────────────────────────────────────────────────────────
@app.route("/api/resumen")
def api_resumen():
    con = get_db()
    total = con.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0]
    unid  = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM ordenes").fetchone()[0]
    asig  = con.execute("SELECT COUNT(*) FROM ordenes WHERE canal NOT IN ('POR CONFIRMAR','')").fetchone()[0]
    cats  = [dict(r) for r in con.execute("""SELECT categoria, COUNT(*) as ordenes,
        COALESCE(SUM(cantidad),0) as unidades FROM ordenes GROUP BY categoria ORDER BY unidades DESC""").fetchall()]
    provs = [dict(r) for r in con.execute("""SELECT canal, COUNT(*) as ordenes,
        COALESCE(SUM(cantidad),0) as unidades FROM ordenes
        WHERE canal NOT IN ('POR CONFIRMAR','') GROUP BY canal ORDER BY unidades DESC LIMIT 12""").fetchall()]
    marcas= [dict(r) for r in con.execute("""SELECT marca, COUNT(*) as ordenes,
        COALESCE(SUM(cantidad),0) as unidades FROM ordenes
        WHERE marca!='' AND marca IS NOT NULL GROUP BY marca ORDER BY unidades DESC LIMIT 10""").fetchall()]
    sync_row = con.execute("SELECT fin,nuevas,actualizadas,error FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return jsonify({
        "resumen": {"total_ordenes":total,"total_unidades":unid,"confirmadas":asig,"sin_confirmar":total-asig},
        "categorias":cats,"proveedores":provs,"marcas":marcas,
        "ultimo_sync": dict(sync_row) if sync_row else {}
    })

@app.route("/api/ordenes")
def api_ordenes():
    cat=request.args.get("categoria",""); estado=request.args.get("estado","")
    q=request.args.get("q",""); page=int(request.args.get("page",1)); limit=100
    where,params=[],[]
    if cat: where.append("categoria=?"); params.append(cat)
    if estado=="asignada":   where.append("canal NOT IN ('POR CONFIRMAR','')")
    elif estado=="pendiente": where.append("canal IN ('POR CONFIRMAR','')")
    if q:
        where.append("(entidad LIKE ? OR ce LIKE ? OR canal LIKE ?)")
        params+=[f"%{q}%",f"%{q}%",f"%{q}%"]
    wh=("WHERE "+" AND ".join(where)) if where else ""
    con=get_db()
    total=con.execute(f"SELECT COUNT(*) FROM ordenes {wh}",params).fetchone()[0]
    rows=[dict(r) for r in con.execute(
        f"SELECT ce,categoria,modelo,cantidad,entidad,finalizacion,canal,precio,marca,ultima_vez "
        f"FROM ordenes {wh} ORDER BY ultima_vez DESC LIMIT ? OFFSET ?",
        params+[limit,(page-1)*limit]).fetchall()]
    con.close()
    return jsonify({"total":total,"page":page,"data":rows})

@app.route("/api/sync", methods=["POST"])
def api_sync_manual():
    threading.Thread(target=sync, daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/sync-log")
def api_sync_log():
    con=get_db()
    rows=[dict(r) for r in con.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 20").fetchall()]
    con.close(); return jsonify(rows)

@app.route("/api/diagnostico")
def api_diagnostico():
    import glob
    hits = glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome")
    hits2 = glob.glob("/ms-playwright/chromium-*/chrome-linux/chromedriver")
    sync_row=None
    try:
        con=get_db()
        r=con.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        if r: sync_row=dict(r)
        con.close()
    except: pass
    return jsonify({
        "chromium_paths": hits,
        "chromedriver_paths": hits2,
        "db_exists": os.path.exists(DB_FILE),
        "db_size_kb": round(os.path.getsize(DB_FILE)/1024,1) if os.path.exists(DB_FILE) else 0,
        "ultimo_sync": sync_row,
    })

@app.route("/")
def index():
    return send_from_directory("static","index.html")

def run_scheduler():
    schedule.every(INTERVAL).hours.do(sync)
    while True: schedule.run_pending(); time.sleep(30)

if __name__=="__main__":
    init_db()
    threading.Thread(target=sync, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
