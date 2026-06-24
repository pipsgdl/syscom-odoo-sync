#!/usr/bin/env python3
# Backfill de precio/stock/nombre/specs para baterias y cargadores Nojomo,
# que viven en la pagina de RESULTADOS (no en producto_X.php). 1 fetch por SKU sin precio.
import os, re, json, time, html, urllib.parse, requests
DATA = "/Users/ingfelipe/LICITABOT/data"
P = DATA + "/nojomo_productos.jsonl"
C = DATA + "/nojomo_compat.jsonl"
RES_TPL = {"bat": "bateria_marca_resultados.php?sku_lap=", "ac": "ac_marca_resultados.php?sku_lap="}

env = {}
for l in open("/Users/ingfelipe/syscom-odoo-sync/.env", encoding="utf-8"):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); env[k] = v
B = env["NOJOMO_BASE"]
s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0"})
s.get(B+"/login.php", timeout=20)
s.post(B+"/login.php", files={"usuario": (None, env["NOJOMO_USER"]), "password": (None, env["NOJOMO_PASS"]), "IniciarSesion": (None, "Iniciar Sesion")}, timeout=25)

prods = [json.loads(l) for l in open(P)]
by_sku = {p["sku"]: p for p in prods}
# sku -> un (modelo, cat) cualquiera, desde compat
sku_model = {}
for l in open(C):
    x = json.loads(l)
    if x["categoria"] in RES_TPL and x["sku"] not in sku_model:
        sku_model[x["sku"]] = (x["modelo_laptop"], x["categoria"])

faltan = [sk for sk, p in by_sku.items() if not p.get("precio", 0) and sk in sku_model]
print("SKUs a backfillear:", len(faltan))

def get(path):
    for _ in range(3):
        try: return s.get(B+"/"+path, timeout=30).text
        except Exception: time.sleep(2)
    return ""

def parse_res(txt, sku):
    m = re.search(r"Producto:\s*" + re.escape(sku) + r"\s*\$?\s*([\d,]+)", txt)
    if not m: return None
    precio = float(m.group(1).replace(",", ""))
    after = txt[m.end():m.end()+50]
    stock = "Disponible" if "Disponible" in after else ("Agotado" if "gotad" in after else "")
    before = txt[max(0, m.start()-320):m.start()]
    nm = re.search(r"(Para .+?)(?=\s+Celdas:|\s+Color:|\s+Voltaje:|\s+Watts:|\s+Tipo:|\s+Conector:)", before)
    nombre = nm.group(1).strip() if nm else ""
    specs = {}
    for k in ["Celdas", "Color", "Voltaje", "Watts", "Amperaje", "Tipo", "Conector", "Material"]:
        mm = re.search(k + r":\s*([^:]+?)(?=\s+[A-Z][a-zá]+:|\s+Producto:|$)", before)
        if mm: specs[k] = mm.group(1).strip()
    return precio, stock, nombre, specs

fixed = 0
for i, sku in enumerate(faltan):
    modelo, cat = sku_model[sku]
    d = get(RES_TPL[cat] + urllib.parse.quote(modelo))
    txt = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", d)))
    r = parse_res(txt, sku)
    if r:
        precio, stock, nombre, specs = r
        p = by_sku[sku]
        p["precio"] = precio; p["stock"] = stock
        if nombre: p["nombre"] = nombre
        if specs: p["specs"] = specs
        p["imagenes"] = ["Img/%s/%s-01_Big.jpg" % ("Baterias" if cat == "bat" else "Cargadores", sku)]
        if precio > 0: fixed += 1
    if i % 100 == 0: print("  %d/%d · fixed=%d" % (i, len(faltan), fixed))
    time.sleep(0.12)

# escribir atomico
tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    for p in prods:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
os.replace(tmp, P)
conprecio = sum(1 for p in prods if p.get("precio", 0) > 0)
print("LISTO · backfilled=%d · con precio ahora: %d/%d (%d%%)" % (fixed, conprecio, len(prods), 100*conprecio//len(prods)))
