#!/usr/bin/env python3
"""
ct-connect — Servicio proxy de CT Internacional para Ocean Tech (corre en Oracle 1).
Combina: catálogo FTP local (/opt/ct-cache/ct-catalog.json, cron 15 min, 12,942 items con
precio distribuidor MXN) + API CT Connect en vivo (existencia por almacén, promociones,
tipo de cambio). El API sólo responde desde la IP de Oracle 1 (160.34.217.156) y el email
del token es CASE-SENSITIVE (MAYÚSCULAS) — por eso este servicio vive aquí y los hubs
mcpo (Roger/Oracle2) lo consumen vía Tailscale (100.105.9.127:11130).

Endpoints (auth: header X-Token o ?token=, excepto /salud):
  GET /salud                → estado (catálogo edad/items, token CT ok)
  GET /precio/<codigo>      → ficha: precio + existencia EN VIVO + promo + t.c.
                              (match por clave CT, numParte/VPN o modelo)
  GET /buscar?q=texto       → busca en el catálogo (clave/parte/modelo/nombre/marca), top 8
  GET /promociones          → promociones vigentes del API (cache 10 min)
  GET /tipocambio           → tipo de cambio CT (cache 1 h)

Red de seguridad: stdlib puro (sin pips), token de servicio, cache de token CT con
renovación (24h exp / reintento en 401), recarga del catálogo por mtime, timeouts,
threading server, systemd Restart=always.
"""
import json, os, re, ssl, time, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

ENVF = "/opt/ct-connect/.env"
ENV = {}
for _l in open(ENVF, encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1); ENV[k] = v

CT_BASE   = ENV.get("CT_BASE", "http://connect.ctonline.mx:3001")
CT_EMAIL  = ENV["CT_EMAIL"]; CT_CLIENTE = ENV["CT_CLIENTE"]; CT_RFC = ENV["CT_RFC"]
SVC_TOKEN = ENV["SVC_TOKEN"]
CATALOG   = ENV.get("CATALOG_PATH", "/opt/ct-cache/ct-catalog.json")
BIND      = ENV.get("BIND", "127.0.0.1"); PORT = int(ENV.get("PORT", "11130"))

_lock = threading.Lock()
_ct_token = {"tok": None, "ts": 0}
_cat = {"mtime": 0, "items": [], "by_clave": {}, "by_parte": {}}
_cache = {}  # key -> (ts, data)
START = time.time()

def log(m): print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}", flush=True)

def ct_get_token(force=False):
    with _lock:
        if not force and _ct_token["tok"] and time.time() - _ct_token["ts"] < 20 * 3600:
            return _ct_token["tok"]
        body = json.dumps({"email": CT_EMAIL, "cliente": CT_CLIENTE, "rfc": CT_RFC}).encode()
        req = urllib.request.Request(CT_BASE + "/cliente/token", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.loads(r.read()).get("token")
        if not tok:
            raise RuntimeError("CT no devolvió token")
        _ct_token.update(tok=tok, ts=time.time())
        log("token CT renovado")
        return tok

def ct_api(path):
    """GET al API CT con token; reintenta 1 vez si el token expiró (401)."""
    for intento in (1, 2):
        tok = ct_get_token(force=(intento == 2))
        req = urllib.request.Request(CT_BASE + path)
        req.add_header("x-auth", tok)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 401 and intento == 1:
                continue
            raise
    return None

def load_catalog():
    try:
        mt = os.path.getmtime(CATALOG)
    except OSError:
        return
    if mt == _cat["mtime"]:
        return
    with _lock:
        if mt == _cat["mtime"]:
            return
        d = json.load(open(CATALOG))
        items = list(d.values()) if isinstance(d, dict) else d
        # dedup: el builder indexa el mismo producto bajo varias llaves (clave/numParte/modelo)
        vistos, unicos = set(), []
        for i in items:
            k = i.get("idProducto") or i.get("clave")
            if k in vistos:
                continue
            vistos.add(k); unicos.append(i)
        items = unicos
        _cat["items"] = items
        _cat["by_clave"] = {str(i.get("clave", "")).upper(): i for i in items}
        _cat["by_parte"] = {str(i.get("numParte", "")).upper(): i for i in items if i.get("numParte")}
        _cat["mtime"] = mt
        log(f"catálogo recargado: {len(items)} items")

def cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    data = fn()
    _cache[key] = (now, data)
    return data

def existencia_viva(clave):
    try:
        d = ct_api(f"/existencia/{clave}")
        if not isinstance(d, dict) or "errorCode" in d:
            return None, {}
        por = {k: v.get("existencia", 0) for k, v in d.items() if isinstance(v, dict)}
        return sum(por.values()), {k: v for k, v in por.items() if v > 0}
    except Exception as e:
        log(f"existencia_viva({clave}) err: {e}")
        return None, {}

def promos():
    def fetch():
        d = ct_api("/existencia/promociones")
        return {p["codigo"]: p for p in d} if isinstance(d, list) else {}
    return cached("promos", 600, fetch)

def tipo_cambio():
    def fetch():
        d = ct_api("/pedido/tipoCambio")
        return d.get("tipoCambio") if isinstance(d, dict) else None
    return cached("tc", 3600, fetch)

def item_resumen(i):
    return {"clave": i.get("clave"), "numParte": i.get("numParte"), "modelo": i.get("modelo"),
            "nombre": i.get("nombre"), "marca": i.get("marca"), "categoria": i.get("categoria"),
            "precio": i.get("precio"), "moneda": i.get("moneda"),
            "existencia_catalogo": sum((i.get("existencia") or {}).values()) if isinstance(i.get("existencia"), dict) else i.get("existencia")}

def h_precio(codigo):
    load_catalog()
    c = codigo.upper()
    it = _cat["by_clave"].get(c) or _cat["by_parte"].get(c)
    if not it:  # último intento: por modelo exacto
        it = next((x for x in _cat["items"] if str(x.get("modelo", "")).upper() == c), None)
    if not it:
        return {"encontrado": False, "codigo": codigo,
                "sugerencia": "usa /buscar?q= para encontrar la clave CT o numParte"}
    total, por_alm = existencia_viva(it.get("clave"))
    promo = promos().get(it.get("clave"))
    out = item_resumen(it)
    out.update({"encontrado": True, "descripcion": (it.get("descripcion") or "")[:300],
                "existencia_viva_total": total, "existencia_viva_por_almacen": por_alm,
                "promocion": ({"precio": promo.get("precio"), "moneda": promo.get("moneda")} if promo else None),
                "tipo_cambio_ct": tipo_cambio(),
                "fuente": "catalogo FTP (15min) + existencia/promos EN VIVO (CT Connect API)"})
    return out

def h_buscar(q, limit=8):
    load_catalog()
    toks = [t for t in re.split(r"\s+", q.upper().strip()) if t]
    if not toks:
        return {"resultados": []}
    res = []
    for i in _cat["items"]:
        hay = " ".join(str(i.get(f, "")) for f in
                       ("clave", "numParte", "modelo", "nombre", "marca", "descripcion")).upper()
        if all(t in hay for t in toks):
            score = sum(3 for t in toks if str(i.get("clave", "")).upper().startswith(t)
                        or str(i.get("numParte", "")).upper().startswith(t)
                        or str(i.get("modelo", "")).upper().startswith(t))
            res.append((score, i))
    res.sort(key=lambda x: (-x[0], -(x[1].get("precio") or 0)))
    return {"consulta": q, "total_matches": len(res),
            "resultados": [item_resumen(i) for _, i in res[:limit]],
            "nota": "existencia_catalogo = corte FTP 15 min; usa /precio/<clave> para stock EN VIVO"}

def h_promociones(limit=25):
    ps = list(promos().values())[:limit]
    return {"total": len(promos()), "promociones": ps}

def h_salud():
    load_catalog()
    edad = int((time.time() - _cat["mtime"]) / 60) if _cat["mtime"] else None
    tok_ok = True
    try:
        ct_get_token()
    except Exception:
        tok_ok = False
    return {"ok": tok_ok and bool(_cat["items"]) and (edad is not None and edad < 120),
            "catalogo_items": len(_cat["items"]), "catalogo_edad_min": edad,
            "token_ct_ok": tok_ok, "uptime_s": int(time.time() - START)}

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a): log(f"{self.client_address[0]} {fmt % a}")
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        try:
            u = urlparse(self.path); qs = parse_qs(u.query)
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            if parts == ["salud"]:
                return self._send(200, h_salud())
            tok = self.headers.get("X-Token") or (qs.get("token") or [""])[0]
            if tok != SVC_TOKEN:
                return self._send(401, {"error": "token de servicio inválido"})
            if len(parts) == 2 and parts[0] == "precio":
                return self._send(200, h_precio(parts[1]))
            if parts == ["buscar"]:
                lim = min(int((qs.get("limit") or ["8"])[0]), 20)
                return self._send(200, h_buscar((qs.get("q") or [""])[0], lim))
            if parts == ["promociones"]:
                lim = min(int((qs.get("limit") or ["25"])[0]), 100)
                return self._send(200, h_promociones(lim))
            if parts == ["tipocambio"]:
                return self._send(200, {"tipoCambio": tipo_cambio(), "fuente": "CT Connect /pedido/tipoCambio"})
            return self._send(404, {"error": "ruta no encontrada",
                                    "rutas": ["/salud", "/precio/<codigo>", "/buscar?q=", "/promociones", "/tipocambio"]})
        except Exception as e:
            log(f"ERROR {self.path}: {e}")
            return self._send(500, {"error": str(e)[:200]})

if __name__ == "__main__":
    load_catalog()
    log(f"ct-connect escuchando en {BIND}:{PORT} · catálogo {len(_cat['items'])} items")
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
