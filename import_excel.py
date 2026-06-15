"""
import_excel.py — Importa SERCOP_EQUIPOS1.xlsx a sercop.db
Uso: python import_excel.py SERCOP_EQUIPOS1.xlsx
"""
import sys
import re
import sqlite3
import pandas as pd
from datetime import datetime

HOJAS = [
    'AIO GEN 12', 'AIO GEN 13',
    'ESCRITORIO GEN 12', 'ESCRITORIO GEN 13',
    'LAPTOP GEN 13', 'LAPTOP GEN 12',
    'OTROS', 'IMPRESORAS'
]

DB_FILE = "sercop.db"

def init_db(con):
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
    con.commit()

def clasificar(prod):
    p = prod.upper()
    if 'IMPRESORA' in p or 'PLOTTER' in p:
        return 'IMPRESORAS'
    if 'SCANNER' in p or 'ESCANER' in p:
        return 'OTROS'
    if 'TODO EN UNO' in p or 'ALL IN ONE' in p:
        tipo = 'AIO'
    elif 'PORTÁTIL' in p or 'PORTATIL' in p:
        tipo = 'LAPTOP'
    elif 'ESCRITORIO' in p:
        tipo = 'ESCRITORIO'
    else:
        return 'OTROS'
    gen = 'GEN 13' if ('GENERACIÓN 13' in p or 'GENERACION 13' in p) else 'GEN 12'
    return f'{tipo} {gen}'

def extraer_modelo(prod):
    m = re.search(r'MODELO\s+(\d+)', prod.upper())
    return f'MODELO {m.group(1)}' if m else 'SIN MODELO'

def importar(excel_path):
    print(f"Leyendo: {excel_path}")
    ahora = datetime.now().isoformat(timespec='seconds')
    registros = []

    for hoja in HOJAS:
        try:
            df = pd.read_excel(excel_path, sheet_name=hoja, header=0)
        except Exception as e:
            print(f"  ⚠ Hoja '{hoja}' no encontrada: {e}")
            continue

        for _, row in df.iterrows():
            prod = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ''
            if not prod or prod in ['nan', 'Producto', 'None', 'Cant']:
                continue
            ce_match = re.search(r'CE-\d+', prod)
            ce = ce_match.group(0) if ce_match else (str(row.iloc[1]) if pd.notna(row.iloc[1]) else '')
            if not ce or not ce.startswith('CE-'):
                continue

            try:
                qty = int(float(row.iloc[2])) if pd.notna(row.iloc[2]) else 0
            except Exception:
                qty = 0

            entidad = str(row.iloc[3]) if len(row) > 3 and pd.notna(row.iloc[3]) else ''
            fin     = str(row.iloc[4]) if len(row) > 4 and pd.notna(row.iloc[4]) else ''
            canal   = str(row.iloc[6]) if len(row) > 6 and pd.notna(row.iloc[6]) else 'POR CONFIRMAR'
            if canal in ('nan', ''):
                canal = 'POR CONFIRMAR'

            precio = None
            if len(row) > 7 and pd.notna(row.iloc[7]):
                try:
                    precio = float(row.iloc[7])
                except Exception:
                    pass

            marca = ''
            for idx in [10, 9, 8]:
                v = str(row.iloc[idx]) if len(row) > idx and pd.notna(row.iloc[idx]) else ''
                if v and v not in ('nan', 'POR CONFIRMAR', ''):
                    marca = v
                    break

            registros.append((
                ce, prod[:150], extraer_modelo(prod), hoja,
                qty, entidad[:100], fin[:19],
                canal[:80], precio, marca[:30],
                ahora, ahora
            ))

    print(f"Registros leídos: {len(registros)}")

    con = sqlite3.connect(DB_FILE)
    init_db(con)

    nuevas = actualizadas = 0
    for r in registros:
        existe = con.execute("SELECT ce FROM ordenes WHERE ce=?", (r[0],)).fetchone()
        if existe:
            con.execute("""UPDATE ordenes SET
                cantidad=?, entidad=?, finalizacion=?, canal=?,
                precio=?, marca=?, ultima_vez=?
                WHERE ce=?""",
                (r[4], r[5], r[6], r[7], r[8], r[9], r[11], r[0]))
            actualizadas += 1
        else:
            con.execute("INSERT OR IGNORE INTO ordenes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r)
            nuevas += 1

    con.execute(
        "INSERT INTO sync_log (inicio,fin,nuevas,actualizadas,error) VALUES (?,?,?,?,?)",
        (ahora, ahora, nuevas, actualizadas, None)
    )
    con.commit()

    total    = con.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0]
    unidades = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM ordenes").fetchone()[0]
    asig     = con.execute("SELECT COUNT(*) FROM ordenes WHERE canal != 'POR CONFIRMAR'").fetchone()[0]
    con.close()

    print(f"✅ Importación completa:")
    print(f"   Nuevas: {nuevas}  |  Actualizadas: {actualizadas}")
    print(f"   Total DB: {total} órdenes  |  {unidades} unidades  |  {asig} asignadas")

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "SERCOP_EQUIPOS1.xlsx"
    importar(path)
