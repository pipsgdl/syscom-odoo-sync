"""
Herramientas in-process que exponen al agente Ocean Tech los precios/stock de CT
Internacional (mayorista de cómputo) vía el hub mcpo LOCAL de O2 (`127.0.0.1:11100/cc`),
que a su vez llama al servicio ct-connect en Oracle 1 (única IP autorizada por CT).

Patrón idéntico a vision_tool.py / rag_tool.py:
  @tool -> POST httpx al mcpo con Bearer (config.CC_MCPO_API_KEY) -> create_sdk_mcp_server()
  -> se registra en ClaudeAgentOptions.mcp_servers de app.py como "ocean_ct".

Cadena completa: agente O2 -> hub mcpo O2 (/cc/ct_*) -> ct-connect Oracle1 (tailnet
100.105.9.127:11130) -> catálogo FTP local (5,800 productos, 15 min) + API CT Connect
en vivo (existencia por almacén, promos, tipo de cambio).

Seguridad: transporte puro; Bearer/URL de config/entorno; degrada con gracia; salida
acotada. Override propio: OCEAN_CT_MCPO_URL.
"""
import logging
import os

import httpx
from claude_agent_sdk import tool, create_sdk_mcp_server

import config

log = logging.getLogger("ocean_agent.ct")

_BASE = (os.environ.get("OCEAN_CT_MCPO_URL") or config.ROGER_RAG_URL).rstrip("/")
_TIMEOUT_S = 45
_MAX_OUTPUT_CHARS = 8000


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if config.CC_MCPO_API_KEY:
        h["Authorization"] = f"Bearer {config.CC_MCPO_API_KEY}"
    return h


async def _post(tool_path: str, payload: dict) -> dict:
    url = f"{_BASE}/{tool_path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=_headers())
            resp.raise_for_status()
            data = resp.text
        log.info("← %s HTTP %s, %d chars", tool_path, resp.status_code, len(data))
        return {"ok": True, "text": data}
    except httpx.TimeoutException:
        return {"ok": False, "error": f"CT mcpo sin respuesta (timeout > {_TIMEOUT_S}s)."}
    except httpx.HTTPStatusError as exc:  # noqa: BLE001
        body = exc.response.text[:300] if exc.response is not None else ""
        code = exc.response.status_code if exc.response is not None else "?"
        return {"ok": False, "error": f"CT mcpo HTTP {code}: {body}"}
    except Exception as exc:  # noqa: BLE001
        log.exception("%s ERROR", tool_path)
        return {"ok": False, "error": f"CT mcpo error: {exc}"}


def _wrap(res: dict, label: str) -> dict:
    if not res["ok"]:
        return {"content": [{"type": "text", "text": f"{label}: {res['error']}"}], "is_error": True}
    return {"content": [{"type": "text", "text": res["text"][:_MAX_OUTPUT_CHARS]}]}


@tool(
    "ct_buscar",
    "Busca productos en el catalogo de CT Internacional (mayorista de computo, ~5,800 "
    "productos: laptops, PCs, servidores, redes, impresion, accesorios). PRECIOS DE "
    "DISTRIBUIDOR en MXN (costo Ocean, sin IVA) — al cotizar a cliente SIEMPRE aplica margen, "
    "NUNCA des el costo tal cual. consulta: texto libre (ej. 'laptop acer i5 16gb', 'switch "
    "24 puertos poe'). Devuelve clave CT, numParte, precio y existencia (corte 15 min). Para "
    "stock EN VIVO por almacen usa ct_precio con la clave.",
    {"consulta": str, "limite": int},
)
async def ct_buscar(args):
    consulta = (args.get("consulta") or "").strip()
    if not consulta:
        return {"content": [{"type": "text", "text": "ct_buscar: falta 'consulta'."}],
                "is_error": True}
    try:
        limite = max(1, min(int(args.get("limite") or 8), 20))
    except (TypeError, ValueError):
        limite = 8
    log.info("→ ct_buscar %r limite=%d", consulta[:80], limite)
    res = await _post("ct_buscar", {"consulta": consulta, "limite": limite})
    return _wrap(res, "Busqueda CT")


@tool(
    "ct_precio",
    "Precio de DISTRIBUIDOR y existencia EN VIVO por almacen de un producto de CT "
    "Internacional. codigo: clave CT (ej. COMACR9460), numero de parte/VPN (ej. "
    "NX.B17AL.006) o modelo. Devuelve precio distribuidor MXN (costo Ocean — al cliente "
    "aplicar margen), stock en vivo por almacen (API CT Connect), promocion vigente y tipo "
    "de cambio CT. Si no conoces la clave exacta usa primero ct_buscar.",
    {"codigo": str},
)
async def ct_precio(args):
    codigo = (args.get("codigo") or "").strip()
    if not codigo:
        return {"content": [{"type": "text", "text": "ct_precio: falta 'codigo'."}],
                "is_error": True}
    log.info("→ ct_precio %r", codigo[:60])
    res = await _post("ct_precio", {"codigo": codigo})
    return _wrap(res, "Precio CT")


@tool(
    "ct_promociones",
    "Promociones vigentes de CT Internacional (precio oferta de distribuidor, en vivo). "
    "Util para ofertas de e-commerce o cotizaciones agresivas. limite: cuantas (default 20).",
    {"limite": int},
)
async def ct_promociones(args):
    try:
        limite = max(1, min(int(args.get("limite") or 20), 100))
    except (TypeError, ValueError):
        limite = 20
    res = await _post("ct_promociones", {"limite": limite})
    return _wrap(res, "Promociones CT")


@tool(
    "ct_tipo_cambio",
    "Tipo de cambio USD/MXN que CT Internacional aplica hoy en sus precios.",
    {},
)
async def ct_tipo_cambio(args):
    res = await _post("ct_tipo_cambio", {})
    return _wrap(res, "Tipo de cambio CT")


def build_ct_server():
    """SDK MCP server in-process con las tools de CT Internacional."""
    return create_sdk_mcp_server(
        name="ocean_ct",
        version="0.1.0",
        tools=[ct_buscar, ct_precio, ct_promociones, ct_tipo_cambio],
    )
