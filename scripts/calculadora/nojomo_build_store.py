#!/usr/bin/env python3
# Genera los CSV de carga del almacén de la calculadora desde los JSONL scrapeados,
# aplicando el markup VARIABLE por categoría (config aprobado por Felipe).
import json, csv
DATA = "/Users/ingfelipe/LICITABOT/data"
MKF = "/Users/ingfelipe/syscom-odoo-sync/config/nojomo_markup.json"
mk = json.load(open(MKF))
MK, DEF = mk["por_categoria"], mk["default"]

raw = [json.loads(l) for l in open(DATA + "/nojomo_productos.jsonl")]
# dedup por SKU (el relanzamiento del crawl pudo repetir filas); preferir la que tiene precio
bysku = {}
for p in raw:
    sku = p["sku"]
    if sku not in bysku or (p.get("precio", 0) > 0 and not bysku[sku].get("precio", 0)):
        bysku[sku] = p
prods = list(bysku.values())
con_precio = 0
fpub = open("/tmp/nojomo_catalogo_pub.csv", "w", newline="", encoding="utf-8")  # SIN costo — para la instancia pública
wpub = csv.writer(fpub)
with open("/tmp/nojomo_catalogo.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    for p in prods:
        cat = p["categoria"]; costo = p.get("precio", 0) or 0
        mkup = MK.get(cat, DEF)
        venta = round(costo * mkup) if costo > 0 else ""
        if costo > 0: con_precio += 1
        disp = "true" if p.get("stock") == "Disponible" else "false"
        nombre = (p.get("nombre") or "").strip()
        specs = json.dumps(p.get("specs", {}), ensure_ascii=False)
        imgs = json.dumps(p.get("imagenes", []), ensure_ascii=False)
        envio = json.dumps(p.get("envio", {}), ensure_ascii=False)
        w.writerow([p["sku"], cat, nombre, costo if costo > 0 else "", mkup if costo > 0 else "",
                    venta, p.get("stock") or "", disp, specs, imgs, envio, p.get("url") or ""])
        if costo > 0:  # publica: solo refacciones con precio, SIN costo/markup
            wpub.writerow([p["sku"], cat, nombre, venta, disp, specs, imgs, envio])
fpub.close()

seen = set(); ncompat = 0
with open("/tmp/nojomo_compat.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    for l in open(DATA + "/nojomo_compat.jsonl"):
        x = json.loads(l); k = (x["modelo_laptop"], x["sku"])
        if k in seen: continue
        seen.add(k); ncompat += 1
        w.writerow([x["modelo_laptop"], x.get("marca", ""), x["sku"], x["categoria"]])

print("catalogo: %d filas (%d con precio_venta) · compat: %d filas (dedup)" % (len(prods), con_precio, ncompat))
print("markup por categoria:", MK)
