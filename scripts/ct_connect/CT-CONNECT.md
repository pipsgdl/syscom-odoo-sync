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

## API CT Connect — mapa COMPLETO (doc oficial recibida de Carolina 2026-07-03)
Doc oficial: `https://api.ctonline.mx/documentacion.html` (OpenAPI 3.1, v1.0.11; protegida por Cloudflare — abrir en navegador). **Sandbox:** `http://sandbox.ctonline.mx` · **Producción:** `http://connect.ctonline.mx:3001`. **IP autorizada CONFIRMADA por CT:** `160.34.217.156` (Oracle 1).

### Autenticación
- `POST /cliente/token` body `{"email","cliente","rfc"}` → `{token}` JWT exp 24 h. ⚠️ email CASE-SENSITIVE (MAYÚSCULAS). Todo lo demás con header `x-auth: <token>`.

### Artículos
- `GET /existencia/promociones` → listado con precio+stock · `GET /existencia/promociones/{codigo}` → **promo y precio POR artículo**.
- `GET /existencia/{codigo}` → stock por almacén · `GET /existencia/{codigo}/{almacen}` · `GET /existencia/detalle/{codigo}/{almacen}` · `GET /existencia/{codigo}/TOTAL`.

### Orden de Compra (dropshipping) — flujo confirmado por Carolina
1. **`POST /pedido`** (Solicitar) — body: `idPedido` (referencia nuestra, int), `almacen` (ej "01A"), `tipoPago` (**"99" = Crédito CT**), `cfdi` ("G01" default), `envio: [{nombre, direccion, entreCalles, noExterior, noInterior, colonia, estado, ciudad, codigoPostal, telefono}]` (= dirección del CLIENTE FINAL → dropship directo), `producto: [{cantidad, clave, precio, moneda MXN|USD}]`. → Respuesta `respuestaCT: {pedidoWeb: "W01-000001", tipoDeCambio, estatus: "Pendiente", errores[]}`. ⚠️ **Vigencia 48 h: si no se confirma, se cancela solo.**
2. **`POST /pedido/confirmar`** `{folio}` → okCode 2000. **Esto dispara la facturación de CT.**
3. **`POST /pedido/guias`** `{folio, guias: [{guia, paqueteria, archivo (PDF base64)}]}` — opcional, si nosotros ponemos la guía; almacén CT empaca y entrega.
4. Seguimiento: `GET /pedido/listar` · `GET /pedido/estatus/{folio}` ("Pendiente"/"Confirmado"…) · `GET /pedido/detalle/{folio}`.

### Utilidades
- `GET /pedido/tipoCambio` → `{"tipoCambio": 17.62}` · `GET /paqueteria/volumetria/{codigo}` (peso/dimensiones para cotizar envío) · `GET /series/factura/{factura}` (números de serie por factura).

### Diseño del bot de dropship (siguiente incremento)
Solicitar (`idPedido` = id de la SO de Odoo → idempotencia) → validar total vs cotizado → **gate de confirmación humana** → confirmar → poll estatus → registrar folio/factura en Odoo. Probar TODO en sandbox (`sandbox.ctonline.mx`) antes de producción. tipoPago 99 = va contra la línea de crédito CT: el gate humano es obligatorio.

## Salud / troubleshooting
1. `curl http://100.105.9.127:11130/salud` (desde tailnet) — `ok:true` y `catalogo_edad_min < 120`.
2. Si `catalogo_edad_min` alto → revisar cron `ct-cache-builder.sh` en Oracle 1 (`/opt/ct-cache/ct-cache.log`).
3. Si `token_ct_ok:false` → probar token a mano (email EN MAYÚSCULAS) y confirmar IP con Carolina.
4. Sync Odoo: log diario `~/syscom-odoo-sync/logs/ct_diff_YYYYMMDD_0600.log` en Mac mini.

## Bot de pedidos `ct_dropship.py` (v2, 2026-07-03) — construido y probado en sandbox
`/opt/ct-connect/ct_dropship.py` (Oracle 1, stdlib puro). **Sandbox por default**; producción doblemente gateada. Endurecido con revisión adversarial multi-lente (16 agentes, 11 hallazgos corregidos):
- **Gates atados al HOST real** (no al flag): sanity-check de `CT_SANDBOX_BASE`/`CT_BASE` al arrancar + ambiente efectivo por hostname. Producción exige `--produccion --confirmo-compra`; confirmar exige además `--frase CONFIRMO-FACTURACION`; `--forzar` en prod exige `--frase-forzar FORZAR-PRODUCCION`.
- **Write-ahead audit**: `solicitar_intento`/`confirmar_intento` ANTES del POST + resultado después (fsync). TODO fallo (HTTP/red/token/parseo) queda en bitácora `pedidos_<ambiente>.jsonl` + alerta Telegram. Red caída a mitad de pedido → estado INDETERMINADO explícito que exige verificar con `listar`/`estatus` antes de reintentar.
- **Idempotencia por folio** (un pedido con `errores[]` también existe en CT) + cross-check de folio contra bitácora en confirmar + lock `flock` anti-carrera + bitácora tolerante a líneas corruptas.
- **Anti-typo**: coerción int de codigoPostal/telefono, tope `CT_MAX_TOTAL_PEDIDO` (default $50k, `--sobre-limite`), total impreso antes de disparar.
- **E2E sandbox verificado**: token ✓, gate producción bloquea ✓, validación de inventario real del API ✓ (4008 sin stock en 01A → correcto), auditoría de fallos ✓.
- **⛔ BLOQUEADO por cuenta, no por código:** `POST /pedido` responde `4000 "Sin linea de credito. Contacte a su asesor de venta"` — la cuenta GDL2508 no tiene línea de crédito para pedidos API (tipoPago 99). **Acción: pedir a Carolina Wong** habilitar línea/modalidad de pago para pedidos API (sandbox y producción) + catálogo de valores válidos de `tipoPago`.
