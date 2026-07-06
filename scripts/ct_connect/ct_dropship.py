#!/usr/bin/env python3
"""
ct_dropship.py — Motor de pedidos (dropshipping) CT Internacional vía API CT Connect.
Corre en Oracle 1 (única IP autorizada). SANDBOX POR DEFECTO; producción doblemente gateada.
v2 — endurecido tras revisión adversarial multi-lente (2026-07-03): gates atados al HOST
real (no al flag), write-ahead log, auditoría de fallos, idempotencia por folio, lock
anti-carrera, bitácora tolerante a corrupción, coerción de tipos del contrato, tope de monto.

Flujo CT (doc oficial v1.0.11): POST /pedido (vigencia 48h) -> POST /pedido/confirmar
(CT FACTURA = compromete línea de crédito) -> POST /pedido/guias (opcional) -> estatus.

Uso:
  ct_dropship.py token|listar
  ct_dropship.py solicitar --pedido pedido.json                    # sandbox
  ct_dropship.py estatus|detalle --folio W01-000001
  ct_dropship.py confirmar --folio W01-000001                      # sandbox
  ct_dropship.py solicitar --pedido p.json --produccion --confirmo-compra          # PROD
  ct_dropship.py confirmar --folio W.. --produccion --confirmo-compra --frase CONFIRMO-FACTURACION
"""
import argparse, fcntl, json, os, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone

ENVF = "/opt/ct-connect/.env"
LOGDIR = "/opt/ct-connect"
CAMPOS_ENVIO = ("nombre", "direccion", "noExterior", "colonia", "estado",
                "ciudad", "codigoPostal", "telefono")

ENV = {}
for _l in open(ENVF, encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1); ENV[k] = v

BASES = {"sandbox": ENV.get("CT_SANDBOX_BASE", "http://sandbox.ctonline.mx:3001"),
         "produccion": ENV.get("CT_BASE", "http://connect.ctonline.mx:3001")}
MAX_TOTAL = float(ENV.get("CT_MAX_TOTAL_PEDIDO", "50000"))  # tope anti-typo (MXN)

# [GATE-CONFIG] sanity fail-fast: el gate se ata al HOST real, no al flag (hallazgo crítico)
_h_sbx = urllib.parse.urlparse(BASES["sandbox"]).hostname or ""
_h_prod = urllib.parse.urlparse(BASES["produccion"]).hostname or ""
if "sandbox" not in _h_sbx or BASES["sandbox"].rstrip("/") == BASES["produccion"].rstrip("/"):
    raise SystemExit(f"[GATE-CONFIG] BASES['sandbox']={BASES['sandbox']} NO parece sandbox "
                     f"(el host debe contener 'sandbox' y diferir de producción). Revisa {ENVF}.")
if "sandbox" in _h_prod:
    raise SystemExit(f"[GATE-CONFIG] BASES['produccion']={BASES['produccion']} apunta a un "
                     f"host sandbox. Revisa CT_BASE en {ENVF}.")

_tok_cache = {}


# ---------------------------------------------------------------- auditoría
def log_audit(ambiente, accion, payload, respuesta, ok):
    reg = {"ts": datetime.now(timezone.utc).isoformat(), "ambiente": ambiente,
           "accion": accion, "ok": ok, "payload": payload, "respuesta": respuesta,
           "usuario": os.environ.get("SUDO_USER") or os.environ.get("USER") or "?"}
    with open(f"{LOGDIR}/pedidos_{ambiente}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        f.flush(); os.fsync(f.fileno())


def leidas_auditoria(ambiente):
    """Bitácora tolerante: una línea corrupta NO bloquea el motor (pero alerta)."""
    p = f"{LOGDIR}/pedidos_{ambiente}.jsonl"
    if not os.path.exists(p):
        return []
    regs, malas = [], []
    for n, l in enumerate(open(p, encoding="utf-8"), 1):
        if not l.strip():
            continue
        try:
            r = json.loads(l)
            regs.append(r) if isinstance(r, dict) else malas.append(n)
        except json.JSONDecodeError:
            malas.append(n)
    if malas:
        aviso = (f"⚠️ bitácora {p}: línea(s) {malas} corrupta(s) — se IGNORAN para "
                 "idempotencia; repara el JSONL")
        print(aviso, file=sys.stderr)
        tg_alerta(aviso)
    return regs


def tg_alerta(msg):
    tok, chat = ENV.get("TG_TOKEN"), ENV.get("TG_CHAT")
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data),
            timeout=10)
    except Exception:
        pass  # la alerta nunca bloquea


def abortar(ambiente, accion, payload, detalle):
    """Todo fallo queda AUDITADO y alertado antes de abortar (hallazgo alta)."""
    try:
        log_audit(ambiente, accion, payload, {"error": detalle[:800]}, False)
    except Exception:
        pass
    tg_alerta(f"❌ CT {ambiente}: FALLÓ '{accion}': {detalle[:300]}")
    raise SystemExit(f"[{ambiente}] {detalle}")


# ---------------------------------------------------------------- API
def token(ambiente, force=False):
    c = _tok_cache.get(ambiente)
    if c and not force and time.time() - c[1] < 20 * 3600:
        return c[0]
    body = json.dumps({"email": ENV["CT_EMAIL"], "cliente": ENV["CT_CLIENTE"],
                       "rfc": ENV["CT_RFC"]}).encode()
    req = urllib.request.Request(BASES[ambiente] + "/cliente/token", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        tok = json.loads(r.read()).get("token")
    if not tok:
        raise RuntimeError("CT no devolvió token")
    _tok_cache[ambiente] = (tok, time.time())
    return tok


def api(ambiente, metodo, path, body=None, accion=None):
    """Llamada al API. TODO fallo (HTTP/red/token/parseo) pasa por abortar() = auditado."""
    accion = accion or f"{metodo} {path}"
    for intento in (1, 2):
        try:
            req = urllib.request.Request(BASES[ambiente] + path, method=metodo)
            req.add_header("x-auth", token(ambiente, force=(intento == 2)))
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, data=data, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode()[:400]
            if e.code == 401 and intento == 1:
                continue
            abortar(ambiente, accion, body, f"HTTP {e.code} en {metodo} {path}: {cuerpo}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            abortar(ambiente, accion, body,
                    f"RED en {metodo} {path}: {e!r} — ⚠️ RESULTADO INDETERMINADO: el pedido "
                    f"PUDO haberse creado/confirmado en CT. Verifica con 'listar'/'estatus' "
                    f"ANTES de reintentar.")
        except (json.JSONDecodeError, RuntimeError) as e:
            abortar(ambiente, accion, body, f"respuesta ilegible/token en {metodo} {path}: {e!r}")
    return None


# ---------------------------------------------------------------- validación
def validar_pedido(p):
    errores = []
    if not isinstance(p.get("idPedido"), int) or isinstance(p.get("idPedido"), bool) \
            or p["idPedido"] <= 0:
        errores.append("idPedido debe ser entero > 0 (usa el id de la SO de Odoo)")
    if not p.get("almacen"):
        errores.append("falta almacen (ej '01A')")
    env = p.get("envio") or {}
    for c in CAMPOS_ENVIO:
        if c not in env or env[c] in (None, ""):
            errores.append(f"envio.{c} requerido")
    # contrato CT: codigoPostal y telefono son INT (coerción de strings numéricos — caso Odoo/Excel)
    for c in ("codigoPostal", "telefono"):
        v = env.get(c)
        if isinstance(v, bool) or not (isinstance(v, int)
                                       or (isinstance(v, str) and v.strip().isdigit())):
            if f"envio.{c} requerido" not in errores:
                errores.append(f"envio.{c} debe ser entero (contrato CT), recibido "
                               f"{type(v).__name__}: {v!r}")
        else:
            env[c] = int(v)
    prods = p.get("productos") or []
    if not prods:
        errores.append("productos vacío")
    for i, pr in enumerate(prods):
        if not pr.get("clave"):
            errores.append(f"productos[{i}].clave requerida")
        if not isinstance(pr.get("cantidad"), int) or isinstance(pr.get("cantidad"), bool) \
                or pr["cantidad"] <= 0:
            errores.append(f"productos[{i}].cantidad debe ser entero > 0")
        if isinstance(pr.get("precio"), bool) or not isinstance(pr.get("precio"), (int, float)) \
                or pr["precio"] <= 0:
            errores.append(f"productos[{i}].precio debe ser > 0")
        if pr.get("moneda") not in ("MXN", "USD"):
            errores.append(f"productos[{i}].moneda debe ser MXN o USD")
    if errores:
        raise SystemExit("Pedido INVÁLIDO:\n  - " + "\n  - ".join(errores))
    return sum(pr["cantidad"] * pr["precio"] for pr in prods)


def gate_produccion(args, accion, resumen=""):
    if args.ambiente != "produccion":
        return
    if not args.confirmo_compra:
        raise SystemExit(f"[GATE] '{accion}' en PRODUCCIÓN exige --confirmo-compra explícito.")
    if accion == "confirmar" and args.frase != "CONFIRMO-FACTURACION":
        raise SystemExit("[GATE] confirmar en PRODUCCIÓN factura contra el crédito CT: "
                         "exige --frase CONFIRMO-FACTURACION (literal).")
    if args.forzar and args.frase_forzar != "FORZAR-PRODUCCION":
        raise SystemExit("[GATE] --forzar en PRODUCCIÓN puede DUPLICAR pedidos reales: "
                         "exige --frase-forzar FORZAR-PRODUCCION (literal).")
    tg_alerta(f"⚠️ CT PRODUCCIÓN: '{accion}' {resumen} "
              f"({datetime.now().strftime('%Y-%m-%d %H:%M')}). Bitácora: pedidos_produccion.jsonl")


class LockAmbiente:
    """Lock por ambiente: mata la carrera check-then-act del candado de idempotencia."""
    def __init__(self, ambiente):
        self.path = f"{LOGDIR}/.lock_{ambiente}"
    def __enter__(self):
        self.f = open(self.path, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("[LOCK] otra instancia está operando este ambiente; espera a que "
                             "termine (evita pedidos duplicados).")
        return self
    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()


def folio_de(reg):
    return (((reg.get("respuesta") or {}).get("respuestaCT")) or {}).get("pedidoWeb")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Pedidos CT Connect (dropship) v2")
    ap.add_argument("accion", choices=["token", "solicitar", "confirmar", "guias",
                                       "estatus", "detalle", "listar"])
    ap.add_argument("--pedido"); ap.add_argument("--folio")
    ap.add_argument("--guia"); ap.add_argument("--paqueteria"); ap.add_argument("--archivo-pdf")
    ap.add_argument("--produccion", action="store_true")
    ap.add_argument("--confirmo-compra", action="store_true")
    ap.add_argument("--frase", default=""); ap.add_argument("--frase-forzar", default="")
    ap.add_argument("--forzar", action="store_true")
    ap.add_argument("--sobre-limite", action="store_true",
                    help=f"permite total > CT_MAX_TOTAL_PEDIDO (${MAX_TOTAL:,.0f})")
    args = ap.parse_args()
    args.ambiente = "produccion" if args.produccion else "sandbox"
    # defensa en profundidad: el ambiente EFECTIVO se ata al host real, no al flag
    if "sandbox" not in (urllib.parse.urlparse(BASES[args.ambiente]).hostname or ""):
        args.ambiente = "produccion"
    amb = args.ambiente
    print(f"── ambiente: {amb.upper()} · base: {BASES[amb]} ──")

    if args.accion == "token":
        try:
            print(f"token OK ({len(token(amb))} chars)")
        except Exception as e:
            abortar(amb, "token", None, f"token falló: {e!r}")
        return

    if args.accion == "solicitar":
        if not args.pedido:
            raise SystemExit("--pedido pedido.json requerido")
        p = json.load(open(args.pedido, encoding="utf-8"))
        total = validar_pedido(p)
        if total > MAX_TOTAL and not args.sobre_limite:
            raise SystemExit(f"[TOPE] total ${total:,.2f} > límite ${MAX_TOTAL:,.2f} "
                             f"(CT_MAX_TOTAL_PEDIDO). Si es intencional: --sobre-limite.")
        with LockAmbiente(amb):
            regs = leidas_auditoria(amb)
            # idempotencia por FOLIO devuelto (un pedido con errores TAMBIÉN existe en CT)
            previos = [r for r in regs if r.get("accion") == "solicitar" and folio_de(r)
                       and (r.get("payload") or {}).get("idPedido") == p["idPedido"]]
            if previos and not args.forzar:
                raise SystemExit(f"[IDEMPOTENCIA] idPedido={p['idPedido']} YA tiene folio "
                                 f"{folio_de(previos[-1])} en {amb} ({previos[-1].get('ts')}). "
                                 "--forzar solo si sabes lo que haces.")
            # intento previo sin resultado (crash/red) => exigir verificación manual
            intentos = [r for r in regs if r.get("accion") == "solicitar_intento"
                        and (r.get("payload") or {}).get("idPedido") == p["idPedido"]]
            cerrados = [r for r in regs if r.get("accion") == "solicitar"
                        and (r.get("payload") or {}).get("idPedido") == p["idPedido"]]
            if len(intentos) > len(cerrados) and not args.forzar:
                raise SystemExit(f"[INDETERMINADO] hay un intento previo de idPedido="
                                 f"{p['idPedido']} sin resultado registrado. Corre 'listar' "
                                 "para verificar si existe en CT; si no existe, usa --forzar.")
            gate_produccion(args, "solicitar", f"idPedido={p['idPedido']} total=${total:,.2f}")
            body = {"idPedido": p["idPedido"], "almacen": p["almacen"],
                    "tipoPago": str(p.get("tipoPago", "99")), "cfdi": p.get("cfdi", "G01"),
                    "envio": [p["envio"]],
                    "producto": [{"cantidad": pr["cantidad"], "clave": pr["clave"],
                                  "precio": pr["precio"], "moneda": pr["moneda"]}
                                 for pr in p["productos"]]}
            print(f"solicitando idPedido={p['idPedido']} · {len(p['productos'])} partida(s) "
                  f"· TOTAL ${total:,.2f}")
            log_audit(amb, "solicitar_intento", body, None, False)   # write-ahead
            r = api(amb, "POST", "/pedido", body, accion="solicitar")
            rc = (r.get("respuestaCT") if isinstance(r, dict) else None) or {}
            errs = rc.get("errores") or []
            ok = bool(rc.get("pedidoWeb")) and not errs
            log_audit(amb, "solicitar", body, r, ok)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:1200])
        if errs:
            print(f"\n⚠️ respuestaCT.errores ({len(errs)}):")
            for e in errs:
                print(f"  - {e}")
        if ok:
            print(f"\n✅ FOLIO: {rc['pedidoWeb']} · estatus: {rc.get('estatus')} · "
                  f"t.c.: {rc.get('tipoDeCambio')} · ⚠️ confirma antes de 48 h o se cancela")
        elif rc.get("pedidoWeb"):
            print(f"\n❌ CT creó el folio {rc['pedidoWeb']} PERO con errores: NO confirmar; "
                  "se auto-cancela a las 48 h si no se confirma.")
        else:
            print("\n❌ sin folio — el pedido NO se creó.")
        return

    if args.accion == "confirmar":
        if not args.folio:
            raise SystemExit("--folio requerido")
        with LockAmbiente(amb):
            regs = leidas_auditoria(amb)
            ya = [r for r in regs if r.get("accion") == "confirmar" and r.get("ok")
                  and (r.get("payload") or {}).get("folio") == args.folio]
            if ya and not args.forzar:
                raise SystemExit(f"[IDEMPOTENCIA] folio {args.folio} YA confirmado en {amb} "
                                 f"({ya[-1].get('ts')}).")
            # cross-check: el folio debe haber nacido de un solicitar de ESTE ambiente
            conocido = any(folio_de(r) == args.folio for r in regs
                           if r.get("accion") == "solicitar")
            if not conocido and not args.forzar:
                raise SystemExit(f"[CROSS-CHECK] folio {args.folio} no aparece en la bitácora "
                                 f"de {amb} (¿es de otro ambiente?). --forzar para saltarlo.")
            gate_produccion(args, "confirmar", f"folio={args.folio}")
            log_audit(amb, "confirmar_intento", {"folio": args.folio}, None, False)
            try:
                r = api(amb, "POST", "/pedido/confirmar", {"folio": args.folio},
                        accion="confirmar")
            except SystemExit:
                print(f"⚠️ Resultado INDETERMINADO: CT pudo haber facturado {args.folio}. "
                      f"Verifica: ct_dropship.py estatus --folio {args.folio}"
                      + (" --produccion" if amb == "produccion" else ""))
                raise
            ok = isinstance(r, dict) and str(r.get("okCode", "")) == "2000"
            log_audit(amb, "confirmar", {"folio": args.folio}, r, ok)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:600])
        print("✅ confirmado — CT factura" if ok else "❌ NO confirmado (ver respuesta)")
        return

    if args.accion == "guias":
        if not (args.folio and args.guia and args.paqueteria):
            raise SystemExit("--folio --guia --paqueteria requeridos")
        g = {"guia": args.guia, "paqueteria": args.paqueteria}
        if args.archivo_pdf:
            import base64
            g["archivo"] = base64.b64encode(open(args.archivo_pdf, "rb").read()).decode()
        gate_produccion(args, "guias", f"folio={args.folio}")
        log_audit(amb, "guias_intento", {"folio": args.folio, "guia": args.guia}, None, False)
        r = api(amb, "POST", "/pedido/guias", {"folio": args.folio, "guias": [g]},
                accion="guias")
        log_audit(amb, "guias", {"folio": args.folio, "guia": args.guia,
                                 "paqueteria": args.paqueteria}, r, True)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:600])
        return

    if args.accion in ("estatus", "detalle"):
        if not args.folio:
            raise SystemExit("--folio requerido")
        r = api(amb, "GET", f"/pedido/{args.accion}/{urllib.parse.quote(args.folio)}",
                accion=args.accion)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:1500])
        return

    if args.accion == "listar":
        r = api(amb, "GET", "/pedido/listar", accion="listar")
        lst = r if isinstance(r, list) else [r]
        print(f"{len(lst)} pedido(s)")
        for x in lst[-15:]:
            if not isinstance(x, dict):
                continue
            rc = x.get("respuestaCT") or {}
            print(f"  idPedido={x.get('idPedido')} folio={rc.get('pedidoWeb')} "
                  f"estatus={rc.get('estatus')}")
        return


if __name__ == "__main__":
    main()
