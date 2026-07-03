#!/usr/bin/env python3
"""Parchea ocean-cc-tools.py agregando las tools de CT Internacional (idempotente).
Uso: python3 ct_tools_patch.py /ruta/ocean-cc-tools.py
Inserta el bloque antes del arranque del server (mcp.run / if __name__)."""
import ast, sys, time

BLOQUE = '''

# ============================================================
# CT Internacional (mayorista de cómputo) — vía servicio ct-connect en Oracle 1
# El API de CT sólo responde desde la IP de Oracle 1; este hub lo consume por
# Tailscale (100.105.9.127:11130). Token en .ct-svc-token junto a este archivo.
# ============================================================
import json as _ct_json
import urllib.request as _ct_url

_CT_SVC_BASE = os.environ.get("CT_SVC_BASE", "http://100.105.9.127:11130")

def _ct_svc(path: str) -> dict:
    try:
        tokf = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ct-svc-token")
        tok = os.environ.get("CT_SVC_TOKEN") or (open(tokf).read().strip() if os.path.exists(tokf) else "")
        req = _ct_url.Request(_CT_SVC_BASE + path)
        req.add_header("X-Token", tok)
        with _ct_url.urlopen(req, timeout=30) as r:
            return _ct_json.loads(r.read())
    except Exception as e:
        return {"error": f"ct-connect no disponible: {str(e)[:120]}",
                "sugerencia": "verificar servicio ct-connect en Oracle 1 (systemctl status ct-connect)"}

@mcp.tool()
def ct_buscar(consulta: str, limite: int = 8) -> dict:
    """Busca productos en el catálogo de CT Internacional (mayorista de cómputo, ~5,800 productos:
    laptops, PCs, servidores, redes, impresión, accesorios). Precios de DISTRIBUIDOR en MXN (costo
    Ocean, IVA no incluido) — para precio a cliente aplicar margen. consulta: texto libre
    (ej. 'laptop acer i5 16gb', 'switch 24 puertos'). Devuelve clave CT, numParte, precio y
    existencia (corte 15 min). Para stock EN VIVO por almacén usa ct_precio con la clave."""
    q = _ct_url.quote(consulta)
    return _ct_svc(f"/buscar?q={q}&limit={min(int(limite), 20)}")

@mcp.tool()
def ct_precio(codigo: str) -> dict:
    """Precio de distribuidor y existencia EN VIVO de un producto de CT Internacional.
    codigo: clave CT (ej. COMACR9460), número de parte/VPN (ej. NX.B17AL.006) o modelo.
    Devuelve: precio distribuidor MXN (costo Ocean, para cliente aplicar margen), stock en vivo
    por almacén (API CT Connect), promoción vigente si existe, y tipo de cambio CT.
    Si no conoces la clave exacta usa primero ct_buscar."""
    return _ct_svc(f"/precio/{_ct_url.quote(str(codigo))}")

@mcp.tool()
def ct_promociones(limite: int = 20) -> dict:
    """Promociones vigentes de CT Internacional (precio oferta de distribuidor, en vivo del
    API CT Connect). Útil para armar ofertas de e-commerce o cotizaciones agresivas."""
    return _ct_svc(f"/promociones?limit={min(int(limite), 100)}")

@mcp.tool()
def ct_tipo_cambio() -> dict:
    """Tipo de cambio USD/MXN que CT Internacional aplica hoy en sus precios."""
    return _ct_svc("/tipocambio")
'''

def main():
    p = sys.argv[1]
    s = open(p).read()
    if "def ct_precio(" in s:
        print("ya parcheado — sin cambios")
        return
    # punto de inserción: antes del bloque de arranque
    for anchor in ('if __name__ == "__main__"', "if __name__ == '__main__'", "mcp.run("):
        i = s.rfind(anchor)
        if i != -1:
            break
    assert i != -1, "no encontré el punto de arranque del server"
    ts = time.strftime("%Y%m%d-%H%M%S")
    open(f"{p}.bak-ct-{ts}", "w").write(s)
    nuevo = s[:i] + BLOQUE + "\n\n" + s[i:]
    ast.parse(nuevo)
    open(p, "w").write(nuevo)
    print(f"parcheado OK (backup .bak-ct-{ts})")

if __name__ == "__main__":
    main()
