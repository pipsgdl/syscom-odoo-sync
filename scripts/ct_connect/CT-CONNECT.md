# CT Internacional — Integración completa (CT Connect API + catálogo FTP)

> **Estado:** ✅ productivo desde 2026-07-03 · **Dueño backend:** Felipe + Claude
> **Contacto CT:** Carolina Wong (Gerente comercio electrónico) · carolina.wong@ctin.com.mx · (662) 109-0000 ext. 478
> **Cuenta:** GDL2508 · RFC OIS170119FZ9

## Las 2 llaves que destrabaron el acceso (aprendido a la mala, abril→julio)
1. **El email del token es CASE-SENSITIVE**: `OCEANT_SOLUTIONS@HOTMAIL.COM` en MAYÚSCULAS → token OK; en minúsculas → `errorCode 4010 "No autorizado"`. Meses de 4010 por esto.
2. **Allowlist de IP**: el API sólo responde a la IP de **Oracle 1 (160.34.217.156)**. Desde cualquier otra red da timeout. Por eso todo corre/proxea desde Oracle 1.

## Arquitectura
```
CT Internacional
 ├─ FTP (catálogo completo) ──> Oracle 1: cron */15 min ──> /opt/ct-cache/ct-catalog.json
 │                              (ct-cache-builder.sh, ~5,800 productos únicos, precio MXN)
 └─ CT Connect API (:3001) ──> Oracle 1: servicio ct-connect (systemd, 127.0.0.1:11130)
                                │  token 24h cacheado · existencia EN VIVO · promos · t.c.
                                └─ tailscale serve tcp 11130 → tailnet 100.105.9.127:11130
                                       │
              ┌────────────────────────┴───────────────────────┐
   hub mcpo Roger (:11100/cc)                    hub mcpo Oracle 2 (:11100/cc)
   → agentes Telegram/Hermes                     → webUI ai.ocean-tech.com.mx
   tools: ct_buscar · ct_precio ·                (mismas tools)
          ct_promociones · ct_tipo_cambio

   Sync a Odoo (independiente): Mac mini ct_to_odoo_sync.py --diff (diario 06:00)
   lee el MISMO ct-catalog.json (vía SCP) → product.template + 3 pricelists +
   supplierinfo partner_id=93. Config: ~/syscom-odoo-sync/config/odoo_config_prod.json
   (local-first desde 2026-07-03; el volumen HIKSEMI daba PermissionError/TCC).
```

## Servicio `ct-connect` (Oracle 1)
- **Código:** `/opt/ct-connect/ct_svc.py` (stdlib puro, sin pips) · systemd `ct-connect.service` (Restart=always)
- **Config:** `/opt/ct-connect/.env` (chmod 600): `CT_BASE/EMAIL/CLIENTE/RFC`, `SVC_TOKEN`, `CATALOG_PATH`, `BIND/PORT`
- **Endpoints** (auth `X-Token: <SVC_TOKEN>` salvo `/salud`):
  | Ruta | Qué da |
  |---|---|
  | `GET /salud` | ok, items del catálogo, edad en min, token CT ok |
  | `GET /precio/<codigo>` | ficha: precio distribuidor MXN + **existencia EN VIVO por almacén** + promo + t.c. (match por clave CT, numParte/VPN o modelo) |
  | `GET /buscar?q=texto&limit=8` | búsqueda en catálogo (clave/parte/modelo/nombre/marca/descr) |
  | `GET /promociones?limit=25` | promos vigentes (cache 10 min) |
  | `GET /tipocambio` | t.c. CT (cache 1 h) |
- **Operación:** `sudo systemctl status ct-connect` · logs `journalctl -u ct-connect -n 50` · reinicio seguro (token se renueva solo).

## Tools para agentes IA / webUI (hubs mcpo `/cc/`)
Registradas en `ocean-cc-tools.py` de **ambos** hubs (Roger `100.122.241.82:11100` y Oracle 2 local); token del servicio en `.ct-svc-token` junto al archivo:
- **`ct_buscar(consulta, limite)`** — encuentra productos y su clave CT.
- **`ct_precio(codigo)`** — precio distribuidor + stock EN VIVO por almacén + promo + t.c.
- **`ct_promociones(limite)`** — ofertas vigentes.
- **`ct_tipo_cambio()`** — t.c. que CT aplica hoy.
⚠️ El precio es **costo distribuidor** (Ocean). Para cliente: aplicar margen (cómputo online 7% → proyecto 23%+; ver tabla en `ct_to_odoo_sync.py`).

## API CT Connect — rutas verificadas (2026-07-03)
- `POST /cliente/token` body `{"email","cliente","rfc"}` → `{token}` JWT exp 24 h.
- `GET /existencia/{clave}` (header `x-auth`) → existencia por almacén (35A, 06A…).
- `GET /existencia/promociones` → `[{codigo, precio, moneda, almacenes[]}]`.
- `GET /pedido/tipoCambio` → `{"tipoCambio": 17.62}`.
- `/precio/*`, `/producto/*`, `/productos`, `/paridad` **no existen** (4042). Rutas de pedidos/facturación: pedir doc a Carolina (pendiente).

## Salud / troubleshooting
1. `curl http://100.105.9.127:11130/salud` (desde tailnet) — `ok:true` y `catalogo_edad_min < 120`.
2. Si `catalogo_edad_min` alto → revisar cron `ct-cache-builder.sh` en Oracle 1 (`/opt/ct-cache/ct-cache.log`).
3. Si `token_ct_ok:false` → probar token a mano (email EN MAYÚSCULAS) y confirmar IP con Carolina.
4. Sync Odoo: log diario `~/syscom-odoo-sync/logs/ct_diff_YYYYMMDD_0600.log` en Mac mini.
