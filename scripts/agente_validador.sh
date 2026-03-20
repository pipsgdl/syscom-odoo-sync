#!/bin/zsh
# THEMIS — Diosa de la justicia. Valida completitud de productos en Odoo

while true; do
  clear
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║       ⚖️  THEMIS — Validadora de Productos             ║"
  echo "╠══════════════════════════════════════════════════════╣"

  python3 << 'PYEOF'
import urllib.request, json

odoo = "https://ocean-tech-0326.odoo.com/jsonrpc"
db, uid, pwd = "ocean-tech-0326", 2, ${ODOO_PASSWORD}

def q(model, method, args, kwargs={}):
    p = {"jsonrpc":"2.0","method":"call","id":1,"params":{
        "service":"object","method":"execute_kw",
        "args":[db, uid, pwd, model, method, args], "kwargs": kwargs}}
    req = urllib.request.Request(odoo, data=json.dumps(p).encode(),
        headers={"Content-Type":"application/json"}, method='POST')
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get('result', 0)

total        = q("product.template", "search_count", [[["active","=",True]]])
con_precio   = q("product.template", "search_count", [[["list_price",">",0],["active","=",True]]])
con_imagen   = q("product.template", "search_count", [[["image_1920","!=",False],["active","=",True]]])
con_desc     = q("product.template", "search_count", [[["description_sale","!=",False],["active","=",True]]])
sin_ref      = q("product.template", "search_count", [[["default_code","=",False],["active","=",True]]])

pct_precio = round(con_precio/total*100) if total else 0
pct_img    = round(con_imagen/total*100) if total else 0
pct_desc   = round(con_desc/total*100) if total else 0

bar = lambda p: "█"*int(p/5) + "░"*(20-int(p/5))

print(f"║  📊 Total productos activos:  {total:>6,}")
print(f"║")
print(f"║  💰 Con precio:   {con_precio:>5,}  [{bar(pct_precio)}] {pct_precio}%")
print(f"║  🖼️  Con imagen:   {con_imagen:>5,}  [{bar(pct_img)}] {pct_img}%")
print(f"║  📝 Con desc.:    {con_desc:>5,}  [{bar(pct_desc)}] {pct_desc}%")
print(f"║  ⚠️  Sin ref.:    {sin_ref:>5,}")
print(f"║")

# Completitud general
completos = q("product.template", "search_count", [[
    ["list_price",">",0],["image_1920","!=",False],
    ["default_code","!=",False],["active","=",True]]])
pct_comp = round(completos/total*100) if total else 0
print(f"║  ✅ Productos completos: {completos:,} / {total:,}  ({pct_comp}%)")
PYEOF

  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  🕐 $(date '+%Y-%m-%d %H:%M:%S')  (refresca c/60s)       ║"
  echo "╚══════════════════════════════════════════════════════╝"
  sleep 60
done
