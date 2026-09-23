#!/usr/bin/env python3
"""Pruebas de /vivo/<codigo> de ct-connect (ocean-work spec 001, tarea T8b.5 · criterio V1).

POR QUÉ EXISTE. Felipe, 23-sep: el precio y la existencia de un distribuidor solo
confirman con respuesta EN VIVO de su API y la hora de esa llamada; el catálogo FTP
solo sirve para encontrar la clave. `/precio` no sirve para eso: su precio sale del
catálogo. `/vivo` descubre la clave en el catálogo y pregunta a CT en el momento.

Tres trampas medidas el 23-sep que esta prueba vigila:
  · la API no busca por número de parte, solo por clave CT;
  · una clave inexistente contesta 200 con `precio: 0` — se reporta tal cual, quien
    consume decide (y no la da por precio);
  · hay números de parte con varias claves — se consultan todas, no se elige.

Todo simulado: ni red ni credenciales. Levanta el servidor HTTP de verdad en un
puerto local para probar la llave y que `/salud` y `/precio` no cambian.

    python3 scripts/ct_connect/test_vivo.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ct_vivo_"))
PROMO = {"precio": 3.15, "porciento": 0,
         "vigente": {"ini": "2026-09-01T00:00:00.000Z", "fin": "2026-09-30T23:59:59.000Z"}}
CATALOGO = [
    {"idProducto": 1, "clave": "ACCPVS220", "numParte": "PR-12V1A-A", "modelo": "PR-12V1A-A",
     "nombre": "Fuente de Poder PR-12V1A-A", "marca": "PROVISION-ISR", "precio": 3.29,
     "moneda": "USD", "existencia": {"GDL": 104, "MTY": 20}},
    {"idProducto": 2, "clave": "MOUACC020", "numParte": "EMR213", "modelo": "EMR213",
     "nombre": "Mouse A", "marca": "X", "precio": 248.89, "moneda": "MXN", "existencia": {"GDL": 3}},
    {"idProducto": 3, "clave": "MOUACC030", "numParte": "EMR213", "modelo": "EMR213-B",
     "nombre": "Mouse B", "marca": "X", "precio": 248.89, "moneda": "MXN", "existencia": {"MTY": 30}},
    {"idProducto": 4, "clave": "CAMBOL030", "numParte": "BN8029AVAIRAI/NDAA", "modelo": "BN8029",
     "nombre": "Cámara bala", "marca": "Y", "precio": 348, "moneda": "USD", "existencia": {"MTY": 2}},
    {"idProducto": 5, "clave": "ACCCER000", "numParte": "SINPRECIO-1", "modelo": "SP1",
     "nombre": "Ficha sin precio vivo", "marca": "Z", "precio": 10, "moneda": "MXN", "existencia": {"GDL": 1}},
] + [
    {"idProducto": 100 + n, "clave": f"REPE{n:03d}", "numParte": "REPETIDO-7", "modelo": f"R{n}",
     "nombre": f"Repetido {n}", "marca": "R", "precio": 1, "moneda": "MXN", "existencia": {"GDL": 1}}
    for n in range(7)
]
(TMP / "catalogo.json").write_text(json.dumps(CATALOGO), encoding="utf-8")
(TMP / ".env").write_text(
    # CT_BASE a un puerto local cerrado: si algo se escapara del simulador, falla
    # rápido aquí en vez de salir a internet.
    "CT_BASE=http://127.0.0.1:9\nCT_EMAIL=PRUEBA@EJEMPLO.MX\nCT_CLIENTE=GDL0000\nCT_RFC=XAXX010101000\n"
    f"SVC_TOKEN=llave-de-prueba\nCATALOG_PATH={TMP / 'catalogo.json'}\n", encoding="utf-8")
os.environ["CT_CONNECT_ENV"] = str(TMP / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ct_svc as S  # noqa: E402

RESPUESTAS = {
    "/existencia/promociones/ACCPVS220": {
        "codigo": "ACCPVS220", "precio": 3.31, "moneda": "USD",
        "almacenes": [{"13A": 104, "promocion": PROMO}, {"24A": 20, "promocion": PROMO}]},
    "/existencia/promociones/MOUACC020": {
        "codigo": "MOUACC020", "precio": 248.89, "moneda": "MXN", "almacenes": [{"13A": 3}]},
    "/existencia/promociones/MOUACC030": {
        "codigo": "MOUACC030", "precio": 248.89, "moneda": "MXN", "almacenes": [{"24A": 30}]},
    "/existencia/promociones/CAMBOL030": {
        "codigo": "CAMBOL030", "precio": 348, "moneda": "USD", "almacenes": [{"24A": 2}]},
    # La trampa: así contesta CT una clave que no le conoce precio.
    "/existencia/promociones/ACCCER000": {
        "codigo": "ACCCER000", "precio": 0, "moneda": "MXN", "almacenes": []},
    # Lo que usa /precio, para la prueba de que no cambia.
    "/existencia/ACCPVS220": {"13A": {"existencia": 104}, "24A": {"existencia": 20}},
    "/existencia/promociones": [{"codigo": "ACCPVS220", "precio": 3.15, "moneda": "USD"}],
    "/pedido/tipoCambio": {"tipoCambio": 17.74},
}
for n in range(7):
    RESPUESTAS[f"/existencia/promociones/REPE{n:03d}"] = {
        "codigo": f"REPE{n:03d}", "precio": 1, "moneda": "MXN", "almacenes": [{"13A": 1}]}
FALLAS = {}
LLAMADAS = []


def ct_api_falso(ruta):
    LLAMADAS.append(ruta)
    if ruta in FALLAS:
        raise FALLAS[ruta]
    return RESPUESTAS[ruta]


S.ct_api = ct_api_falso
S.ct_get_token = lambda force=False: "token-falso"   # /salud lo pide
FALLOS = 0


def comprobar(etiqueta, condicion, detalle=""):
    global FALLOS
    if condicion:
        print(f"  {etiqueta} OK    · {detalle}")
    else:
        FALLOS += 1
        print(f"  {etiqueta} FALLA · {detalle}")


def iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


print("── V1 · una clave: precio y existencia EN VIVO, con la hora de esa llamada ──")
antes = datetime.now(timezone.utc).replace(microsecond=0)
r = S.h_vivo("PR-12V1A-A")
despues = datetime.now(timezone.utc)
v = (r.get("en_vivo") or [{}])[0]
comprobar("V1", [c["clave"] for c in r["candidatos"]] == ["ACCPVS220"] and len(r["en_vivo"]) == 1,
          f"número de parte → clave {[c['clave'] for c in r['candidatos']]}")
comprobar("V1", v.get("ok") and v.get("precio") == 3.31 and v.get("moneda") == "USD"
          and v.get("existencia_por_almacen") == {"13A": 104, "24A": 20},
          f"en vivo: {v.get('precio')} {v.get('moneda')} · {v.get('existencia_por_almacen')}")
comprobar("V1", v.get("consultado_en") and antes <= iso(v["consultado_en"]) <= despues,
          f"`consultado_en` es la hora de ESA llamada: {v.get('consultado_en')}")
comprobar("V1", v.get("promocion_por_almacen", {}).get("13A") ==
          {"precio": 3.15, "inicio": "2026-09-01T00:00:00.000Z", "fin": "2026-09-30T23:59:59.000Z"},
          f"la promoción viene aparte, por almacén y con su fin: {v.get('promocion_por_almacen', {}).get('13A')}")
comprobar("V1", "precio" not in r["candidatos"][0] and "existencia" not in r["candidatos"][0],
          "del catálogo solo sale la identidad del producto, nunca precio ni existencia")

print("── V1 · cómo se encuentra la clave ──")
r = S.h_vivo("ACCPVS220")
comprobar("V1", [c["clave"] for c in r["candidatos"]] == ["ACCPVS220"], "también por clave CT")
r = S.h_vivo("pr 12v1a a")
comprobar("V1", [c["clave"] for c in r["candidatos"]] == ["ACCPVS220"],
          "normaliza: «pr 12v1a a» es PR-12V1A-A")
LLAMADAS.clear()
r = S.h_vivo("DS-2CD1043G0-I")
comprobar("V1", r["candidatos"] == [] and r["en_vivo"] == [] and not LLAMADAS
          and isinstance(r.get("catalogo_edad_min"), int),
          f"no está en el catálogo → ni una llamada a CT; edad del catálogo {r.get('catalogo_edad_min')} min")

print("── V1 · número de parte con varias claves: se consultan todas, no se elige ──")
r = S.h_vivo("EMR213")
comprobar("V1", sorted(c["clave"] for c in r["candidatos"]) == ["MOUACC020", "MOUACC030"]
          and sorted(e["clave"] for e in r["en_vivo"]) == ["MOUACC020", "MOUACC030"],
          f"{len(r['candidatos'])} claves y {len(r['en_vivo'])} consultas en vivo")
LLAMADAS.clear()
r = S.h_vivo("REPETIDO-7")
comprobar("V1", len(r["candidatos"]) == 7 and len(r["en_vivo"]) == S.VIVO_MAX_CLAVES
          and r.get("truncado") is True and len(LLAMADAS) == S.VIVO_MAX_CLAVES,
          f"7 claves: se consultan {len(r['en_vivo'])} (tope {S.VIVO_MAX_CLAVES}) y se avisa `truncado`")

print("── V1 · una falla de CT se reporta, nunca se convierte en «no existe» ──")
FALLAS["/existencia/promociones/ACCPVS220"] = urllib.error.URLError("timed out")
r = S.h_vivo("PR-12V1A-A")
v = r["en_vivo"][0]
comprobar("V1", r["candidatos"] and v.get("ok") is False and "timed out" in (v.get("error") or "")
          and v.get("precio") is None and v.get("consultado_en"),
          f"CT sin respuesta → ok=false · {v.get('error')}")
FALLAS.clear()
r = S.h_vivo("SINPRECIO-1")
v = r["en_vivo"][0]
comprobar("V1", v.get("ok") is True and v.get("precio") == 0 and v.get("existencia_por_almacen") == {},
          "precio 0 se reporta TAL CUAL: decidir que no es precio le toca a quien consume")

print("── V1 · por HTTP: la llave, la barra en el número de parte y lo que NO cambia ──")
srv = S.ThreadingHTTPServer(("127.0.0.1", 0), S.H)
puerto = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def pedir(ruta, llave=True):
    pet = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}")
    if llave:
        pet.add_header("X-Token", "llave-de-prueba")
    try:
        with urllib.request.urlopen(pet, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


cod, _ = pedir("/vivo/PR-12V1A-A", llave=False)
comprobar("V1", cod == 401, f"sin llave → HTTP {cod}")
cod, cuerpo = pedir("/vivo/PR-12V1A-A")
comprobar("V1", cod == 200 and cuerpo["en_vivo"][0]["precio"] == 3.31, f"con llave → HTTP {cod}")
cod, cuerpo = pedir("/vivo/BN8029AVAIRAI%2FNDAA")
comprobar("V1", cod == 200 and [c["clave"] for c in cuerpo.get("candidatos", [])] == ["CAMBOL030"],
          "número de parte con barra, codificada: se encuentra")
cod, cuerpo = pedir("/salud", llave=False)
comprobar("V1", cod == 200 and cuerpo.get("catalogo_items") == len(CATALOGO),
          f"/salud sigue igual y sin llave: {cod}")
cod, cuerpo = pedir("/precio/PR-12V1A-A")
# /precio sigue dando el precio del CATÁLOGO (3.29), no el vivo (3.31): por eso no confirma.
comprobar("V1", cod == 200 and cuerpo.get("encontrado") is True and cuerpo.get("precio") == 3.29
          and cuerpo.get("existencia_viva_total") == 124,
          f"/precio sigue igual: {cuerpo.get('precio')} {cuerpo.get('moneda')} · "
          f"existencia viva {cuerpo.get('existencia_viva_total')}")
cod, cuerpo = pedir("/no-existe")
comprobar("V1", cod == 404 and "/vivo/<codigo>" in cuerpo.get("rutas", []),
          "la lista de rutas del 404 anuncia /vivo")
srv.shutdown()

print()
if FALLOS:
    print(f"===== {FALLOS} PRUEBA(S) FALLARON =====")
    sys.exit(1)
print("===== TODAS LAS PRUEBAS EN VERDE =====")
