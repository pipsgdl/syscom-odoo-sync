#!/usr/bin/env python3
"""
nojomo_scraper.py — Scraper del catálogo Nojomo / Una Laptop (UNLAPTOP).
Estructura: categoría → (marca → modelo_laptop) → producto_*.php?sku=. Guarda:
  - productos (sku, categoria, marca, nombre, precio, envio, stock, specs, imagenes) -> nojomo_productos.jsonl
  - compatibilidad (modelo_laptop -> sku, categoria, marca) -> nojomo_compat.jsonl   <- alimenta la CALCULADORA
Resumable (state por marca/listado ya hecho), idempotente (sku visto), por bloques, tolerante a fallo.

Uso:
  python3 nojomo_scraper.py --cat fan,psc        # categorías planas (rápido)
  python3 nojomo_scraper.py --cat bat            # baterías (marca->modelo->sku, grande)
  python3 nojomo_scraper.py --cat ac,toner,drum,lcd
  python3 nojomo_scraper.py --status
"""
import os, re, sys, json, time, html as _html, urllib.parse
from datetime import datetime, timezone
import requests

ENVF = os.path.expanduser("~/syscom-odoo-sync/.env")
OUT = os.path.expanduser("~/LICITABOT/data") if os.path.isdir(os.path.expanduser("~/LICITABOT")) else "/tmp"
os.makedirs(OUT, exist_ok=True)
PROD_F  = OUT + "/nojomo_productos.jsonl"
COMPAT_F= OUT + "/nojomo_compat.jsonl"
STATE_F = OUT + "/nojomo_state.json"
LOG = os.path.expanduser("~/Library/Logs/nojomo_scraper.log")

# categoría -> (página marca-listado, prefijo detalle, prefijo resultados-por-modelo)
MARCA_CATS = {
  "bat":   ("home_bat.php",   "bateria_marca.php?marca=",  "bateria_marca_resultados.php?sku_lap=",  "producto_bat.php"),
  "ac":    ("home_ac.php",    "ac_marca.php?marca=",       "ac_marca_resultados.php?sku_lap=",       "producto_ac.php"),
  "toner": ("home_toner.php", "toner_marca.php?marca=",    "toner_marca_resultados.php?sku_lap=",    "producto_toner.php"),
  "drum":  ("home_drum.php",  "drum_marca.php?marca=",     "drum_marca_resultados.php?sku_lap=",     "producto_drum.php"),
}
FLAT_CATS = {"fan": "fan_all_sku.php", "psc": "psc_all_sku.php"}

def log(m):
    line=f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}"; print(line,flush=True)
    try: open(LOG,"a").write(line+"\n")
    except: pass

def env():
    d={}
    for l in open(ENVF,encoding="utf-8"):
        l=l.strip()
        if l and not l.startswith("#") and "=" in l: k,v=l.split("=",1); d[k]=v
    return d
E=env(); B=E["NOJOMO_BASE"]

def login():
    s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    s.get(B+"/login.php",timeout=25)
    s.post(B+"/login.php", files={"usuario":(None,E["NOJOMO_USER"]),"password":(None,E["NOJOMO_PASS"]),"IniciarSesion":(None,"Iniciar Sesion")}, timeout=30)
    return s

def get(s,path):
    for _ in range(3):
        try: return s.get(B+"/"+path, timeout=35).text
        except Exception: time.sleep(2)
    return ""

def load_state():
    try: return json.load(open(STATE_F))
    except: return {"done_listados": [], "skus": []}
def save_state(st): json.dump(st, open(STATE_F,"w"))

def parse_producto(d, sku, cat):
    txt = re.sub(r"<[^>]+>"," ", d); txt=_html.unescape(re.sub(r"\s+"," ",txt))
    i = d.find("prod_") ; blk = d[i:i+2600] if i>=0 else d
    btxt = _html.unescape(re.sub(r"\s+"," ", re.sub(r"<[^>]+>"," ", blk))).strip()
    pre = re.search(r"\$\s?([\d,]+)(?:\.\d{2})?", btxt)
    precio = float(pre.group(1).replace(",","")) if pre else 0.0
    stock = "Disponible" if re.search(r"Producto Disponible", btxt, re.I) else ("Agotado" if re.search(r"agotad|sin existencia", btxt, re.I) else "")
    envios = dict(re.findall(r"(Paquetexpress|Estafeta(?: Terrestre)?)\s*\d+\s*Kg\s*\$([\d,]+)", btxt))
    nm = re.search(r"<title>([^<]+)</title>", d)
    nombre = _html.unescape(nm.group(1)).replace(" - UnaLaptop","").strip() if nm else ""
    specs={}
    for k in ["Tipo","Material","Voltaje","Tipo Voltaje","Amperaje","Watts","Color","Dimensiones","Capacidad","Celdas","Resolucion","Tamano","Conector"]:
        m=re.search(re.escape(k)+r":\s*([^A-Z][^:]{0,40}?)(?=[A-Z][a-z]+:|$)", btxt)
        if m: specs[k]=m.group(1).strip()
    imgs=re.findall(r'src="(Img/[^"]+)"', d)
    return {"sku":sku,"categoria":cat,"nombre":nombre,"precio":precio,"stock":stock,
            "envio":envios,"specs":specs,"imagenes":imgs[:6],
            "url":f"{B}/producto_{cat}.php?sku={sku}","scraped_at":datetime.now(timezone.utc).isoformat()}

def scrape_producto(s, sku, cat, seen, extra=""):
    if sku in seen: return False
    d=get(s, f"producto_{cat}.php?sku={sku}{extra}")
    if not d: return False
    p=parse_producto(d, sku, cat)
    open(PROD_F,"a").write(json.dumps(p,ensure_ascii=False)+"\n")
    seen.add(sku); return True

def crawl_lcd(s, st, seen):
    # 1) pantallas genericas por TAMANO (listado plano: ~30 directas)
    h=get(s,"lcd_tamanos.php")
    skus_t=sorted(set(re.findall(r"producto_lcd\.php\?sku=([A-Z0-9-]+)", h)))
    for sku in skus_t: scrape_producto(s,sku,"lcd",seen,extra=f"&NumParte={sku}"); time.sleep(0.15)
    log(f"[lcd] tamanos: {len(skus_t)} productos")
    # 2) por NUMERO DE PARTE (resultados -> producto). Mapea numparte->sku (compatibilidad pantallas)
    hn=get(s,"lcd_numparte.php")
    partes=sorted(set(re.findall(r"lcd_numparte_resultados\.php\?numparte=([^\"'&]+)", hn)))
    log(f"[lcd] numpartes: {len(partes)}")
    for i,np in enumerate(partes):
        key=f"lcd:np:{np}"
        if key in st["done_listados"]: continue
        hr=get(s, "lcd_numparte_resultados.php?numparte="+urllib.parse.quote(np))
        for sku in set(re.findall(r"producto_lcd\.php\?sku=([A-Z0-9-]+)", hr)):
            open(COMPAT_F,"a").write(json.dumps({"modelo_laptop":np,"sku":sku,"categoria":"lcd","marca":"numparte"},ensure_ascii=False)+"\n")
            scrape_producto(s,sku,"lcd",seen,extra=f"&NumParte={sku}")
        st["done_listados"].append(key)
        if i%50==0: save_state(st); log(f"[lcd] numparte {i}/{len(partes)} · {len(seen)} skus")
        time.sleep(0.12)
    save_state(st)

def crawl_flat(s, cat, page, st, seen):
    h=get(s,page)
    skus=sorted(set(re.findall(r"producto_[a-z]+\.php\?sku=([A-Z0-9-]+)", h)))
    log(f"[{cat}] listado {page}: {len(skus)} productos")
    n=0
    for sku in skus:
        if scrape_producto(s,sku,cat,seen): n+=1
        time.sleep(0.2)
    st["done_listados"].append(page); save_state(st)
    log(f"[{cat}] +{n} productos")

def parse_resultado(hr, sku, cat):
    # Para bat/ac el precio+stock+nombre+specs viven en la pagina de RESULTADOS (no en producto_X.php)
    txt = _html.unescape(re.sub(r"\s+"," ", re.sub(r"<[^>]+>"," ", hr)))
    m = re.search(r"Producto:\s*"+re.escape(sku)+r"\s*\$?\s*([\d,]+)", txt)
    precio = float(m.group(1).replace(",","")) if m else 0.0
    pos = m.start() if m else 0
    after = txt[m.end():m.end()+50] if m else ""
    stock = "Disponible" if "Disponible" in after else ("Agotado" if "gotad" in after else "")
    before = txt[max(0,pos-320):pos]
    nm = re.search(r"(Para .+?)(?=\s+Celdas:|\s+Color:|\s+Voltaje:|\s+Watts:|\s+Tipo:|\s+Conector:)", before)
    nombre = nm.group(1).strip() if nm else ""
    specs = {}
    for k in ["Celdas","Color","Voltaje","Watts","Amperaje","Tipo","Conector","Material"]:
        mm = re.search(k+r":\s*([^:]+?)(?=\s+[A-Z][a-zá]+:|\s+Producto:|$)", before)
        if mm: specs[k]=mm.group(1).strip()
    img = "Img/%s/%s-01_Big.jpg" % ("Baterias" if cat=="bat" else ("Cargadores" if cat=="ac" else cat), sku)
    return {"sku":sku,"categoria":cat,"nombre":nombre,"precio":precio,"stock":stock,
            "envio":{}, "specs":specs,"imagenes":[img],
            "url":f"{B}/producto_{cat}.php?sku={sku}","scraped_at":datetime.now(timezone.utc).isoformat()}

def crawl_marca(s, cat, st, seen):
    home, marca_tpl, res_tpl, _ = MARCA_CATS[cat]
    h=get(s,home)
    marcas=sorted(set(re.findall(re.escape(marca_tpl)+r"([^\"'&]+)", h)))
    log(f"[{cat}] marcas: {len(marcas)}")
    for marca in marcas:
        key=f"{cat}:{marca}"
        if key in st["done_listados"]: continue
        hm=get(s, marca_tpl+urllib.parse.quote(marca))
        modelos=sorted(set(re.findall(re.escape(res_tpl)+r"([^\"'&]+)", hm)))
        log(f"[{cat}] {marca}: {len(modelos)} modelos")
        ncomp=0
        for modelo in modelos:
            hr=get(s, res_tpl+urllib.parse.quote(modelo))
            skus=set(re.findall(r"producto_[a-z]+\.php\?sku=([A-Z0-9-]+)", hr))
            for sku in skus:
                open(COMPAT_F,"a").write(json.dumps({"modelo_laptop":modelo,"sku":sku,"categoria":cat,"marca":marca},ensure_ascii=False)+"\n")
                ncomp+=1
                if sku not in seen:   # precio/specs DESDE resultados (producto_X.php sale vacio para bat/ac)
                    open(PROD_F,"a").write(json.dumps(parse_resultado(hr,sku,cat),ensure_ascii=False)+"\n")
                    seen.add(sku)
            time.sleep(0.15)
        st["done_listados"].append(key); save_state(st)
        log(f"[{cat}] {marca} done · {ncomp} compat · {len(seen)} skus totales")

def status():
    try: np=sum(1 for _ in open(PROD_F))
    except: np=0
    try: nc=sum(1 for _ in open(COMPAT_F))
    except: nc=0
    st=load_state()
    log(f"STATUS · productos:{np} · compatibilidades:{nc} · listados hechos:{len(st['done_listados'])}")

def main():
    if "--status" in sys.argv: status(); return
    cats = []
    if "--cat" in sys.argv: cats = sys.argv[sys.argv.index("--cat")+1].split(",")
    else: cats = list(FLAT_CATS)+list(MARCA_CATS)
    s=login(); st=load_state(); seen=set(st.get("skus",[]))
    for cat in cats:
        try:
            if cat in FLAT_CATS: crawl_flat(s,cat,FLAT_CATS[cat],st,seen)
            elif cat in MARCA_CATS: crawl_marca(s,cat,st,seen)
            elif cat=="lcd": crawl_lcd(s,st,seen)
            else: log(f"cat desconocida: {cat}")
        except Exception as e: log(f"[{cat}] ERROR {str(e)[:80]}")
        st["skus"]=sorted(seen); save_state(st)
    status()

if __name__=="__main__": main()
