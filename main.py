import os, re, time, sqlite3, threading, schedule, logging
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request

RUC      = os.getenv("SERCOP_RUC",     "1000973329001")
USUARIO  = os.getenv("SERCOP_USUARIO", "CARLINADAVILA")
CLAVE    = os.getenv("SERCOP_CLAVE",   "Cdavila973329*")
DB_FILE  = os.getenv("DB_FILE",        "sercop.db")
PORT     = int(os.getenv("PORT",       "8080"))
INTERVAL = int(os.getenv("INTERVAL_H", "4"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sercop")
app = Flask(__name__, static_folder="static")

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

def scrape_con_playwright():
    from playwright.sync_api import sync_playwright
    pendientes = []
    asignadas  = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

        BASE = "https://catalogoelectronico.compraspublicas.gob.ec"

        # LOGIN directo vía formulario
        log.info("Cargando /entrar ...")
        page.goto(f"{BASE}/entrar", timeout=30000, wait_until="networkidle")
        page.wait_for_selector("#ruc", timeout=15000)
        page.fill("#ruc", RUC)
        page.fill("#username", USUARIO)
        page.fill("#password", CLAVE)
        # El botón usa onclick="placeOrder()" — ejecutamos directo
        page.evaluate("placeOrder()")
        page.wait_for_load_state("networkidle", timeout=20000)
        log.info(f"Login OK — URL: {page.url}")

        def extraer_filas(url):
            page.goto(url, timeout=30000, wait_until="networkidle")
            page.wait_for_selector("#body_table_listas", timeout=20000)
            return page.query_selector_all("#body_table_listas tr")

        # PENDIENTES
        filas = extraer_filas(f"{BASE}/pendientes")
        log.info(f"Filas pendientes: {len(filas)}")
        for fila in filas:
            cols = fila.query_selector_all("td")
            if len(cols) < 4: continue
            producto = (cols[0].inner_text() or "").strip().replace("\n", " ")
            ce_match = re.search(r"CE-\d+", producto)
            if not ce_match: continue
            try:   qty = int(float(re.sub(r"[^\d.]", "", cols[1].inner_text().strip())))
            except: qty = 0
            pendientes.append({
                "ce": ce_match.group(0), "producto": producto,
                "modelo": extraer_modelo(producto),
                "categoria": clasificar(producto),
                "cantidad": qty,
                "entidad": (cols[2].inner_text() or "").strip(),
                "finalizacion": (cols[3].inner_text() or "").strip(),
            })
        log.info(f"Pendientes extraídos: {len(pendientes)}")

        # ASIGNADAS
        try:
            filas2 = extraer_filas(f"{BASE}/asignadas")
            for fila in filas2:
                cols = fila.query_selector_all("td")
                if len(cols) < 4: continue
                ce_match = re.search(r"CE-\d+", cols[0].inner_text())
                if not ce_match: continue
                ce = ce_match.group(0)
                canal = cols[4].inner_text().strip() if len(cols) > 4 else ""
                try:   precio = float(re.sub(r"[^\d.]", "", cols[5].inner_text())) if len(cols) > 5 else None
                except: precio = None
                if canal: asignadas[ce] = {"canal": canal, "precio": precio}
            log.info(f"Asignadas: {len(asignadas)}")
        except Exception as e:
            log.warning(f"scrape_asignadas: {e}")

        context.close()
        browser.close()

    return pendientes, asignadas

def sync():
    inicio = datetime.now().isoformat(timespec="seconds")
    nuevas = actualizadas = 0
    error_msg = None
    try:
        log.info("=== Iniciando sync ===")
        pendientes, asignadas = scrape_con_playwright()
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
    fin = datetime.now().isoformat(timespec="seconds")
    try:
        con = get_db()
        con.execute("INSERT INTO sync_log (inicio,fin,nuevas,actualizadas,error) VALUES (?,?,?,?,?)",
                    (inicio,fin,nuevas,actualizadas,error_msg))
        con.commit(); con.close()
    except: pass

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
    sync_row=None
    try:
        con=get_db()
        r=con.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        if r: sync_row=dict(r)
        con.close()
    except: pass
    pw_ok=False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b=p.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage"])
            b.close()
        pw_ok=True
    except: pass
    return jsonify({
        "playwright_ok":pw_ok,
        "db_exists":os.path.exists(DB_FILE),
        "db_size_kb":round(os.path.getsize(DB_FILE)/1024,1) if os.path.exists(DB_FILE) else 0,
        "ultimo_sync":sync_row,
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
