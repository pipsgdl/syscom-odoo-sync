import json, collections, statistics as st
P = "/Users/ingfelipe/LICITABOT/data/nojomo_productos.jsonl"
C = "/Users/ingfelipe/LICITABOT/data/nojomo_compat.jsonl"
prods = [json.loads(l) for l in open(P)]
print("=== PRODUCTOS:", len(prods), "===")
cat = collections.Counter(p["categoria"] for p in prods)
print("por categoria:", dict(cat))
conprecio = sum(1 for p in prods if p.get("precio", 0) > 0)
sinprecio = [p["sku"] for p in prods if not p.get("precio", 0)]
print("con precio >0: %d/%d (%d%%) · sin precio: %d" % (conprecio, len(prods), 100*conprecio//len(prods), len(sinprecio)))
if sinprecio[:8]: print("  skus sin precio (muestra):", sinprecio[:8])
constock = sum(1 for p in prods if p.get("stock") == "Disponible")
conspecs = sum(1 for p in prods if p.get("specs"))
conimg = sum(1 for p in prods if p.get("imagenes"))
print("stock Disponible: %d · con specs: %d · con imagenes: %d" % (constock, conspecs, conimg))
precios = [p["precio"] for p in prods if p.get("precio", 0) > 0]
print("precio min/mediana/max: $%.0f / $%.0f / $%.0f" % (min(precios), st.median(precios), max(precios)))
print("--- muestra por categoria ---")
seen = set()
for p in prods:
    c = p["categoria"]
    if c not in seen:
        seen.add(c)
        print("  [%s] %s $%.0f %s | %s" % (c, p["sku"], p["precio"], p.get("stock", ""), p["nombre"][:45]))
compat = [json.loads(l) for l in open(C)]
ccat = collections.Counter(x["categoria"] for x in compat)
nmod = len(set(x["modelo_laptop"] for x in compat))
print("=== COMPATIBILIDADES: %d · modelos laptop unicos: %d · por cat: %s ===" % (len(compat), nmod, dict(ccat)))
print("ejemplo compat:", compat[0])
# SKUs en compat que NO estan en productos (huerfanos)
skus_prod = set(p["sku"] for p in prods)
skus_compat = set(x["sku"] for x in compat)
huerf = skus_compat - skus_prod
print("SKUs en compat sin ficha de producto:", len(huerf), list(huerf)[:6])
