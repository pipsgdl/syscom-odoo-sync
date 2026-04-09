# Syscom → Odoo Sync

Sincronización automática del catálogo completo de **Syscom** (54,391 productos) hacia **Odoo ERP** vía n8n.
Incluye flujos diferenciales de precios/stock, monitor de promociones y emails automáticos.

**Versión actual: v6.1** | Odoo: `ocean-tech.odoo.com` (producción)

## Arquitectura

```
Syscom API
   │
   ├─→ ARES v6 (Full Catalog Sync, cada 30s vía VPS Executor)
   │       └→ Odoo ERP (ocean-tech — producción)
   │
   ├─→ WF2 Monitor Promos (cada 4h) → Odoo + Email
   ├─→ WF3 Detector Altas (diario 1am) → Odoo
   ├─→ WF4 Detector Bajas (semanal dom 2am) → Odoo
   └─→ Promos Email (diario 8am CST) → Gmail SMTP → 5 destinatarios
```

## Workflows activos

| Archivo | ID n8n | Descripción | Frecuencia |
|---------|--------|-------------|------------|
| `syscom_odoo_sync_v6.json` | `bZL70XeQjzvWb8og` | ARES v6 — Full Catalog Sync (1,005 subcategorías) | Continuo |
| `ares_v6_vps_executor.json` | `sdk2MjZN6OyrgiuQ` | VPS Executor — dispara ARES cada 30s | Cada 30s |
| `promos_syscom_email.json` | `kU9S5PSxUT9QFu7y` | Email diario con ~100 promos ≥20% OFF | Diario 8am CST |
| `wf2_monitor_promos.json` | `atlvmnV2n9Gr4KME` | Monitor Promos → Odoo + alerta email | Cada 4h |
| `wf3_detector_altas.json` | `IHbypZWRzHURVkK0` | Detecta productos nuevos en Syscom → crea en Odoo | Diario 1am |
| `wf4_detector_bajas.json` | `EOrLx6luVYEXgNOt` | Detecta productos descontinuados → baja suave Odoo | Dom 2am |

## Versiones

### v6.1 (actual) — Producción + Email Promos
- Migración completa a Odoo producción (`ocean-tech.odoo.com`)
- Email diario de promociones: **~100 promos/día**, 5 destinatarios (@oceantech.com.mx)
- Flujos diferenciales WF2/WF3/WF4 para mantener catálogo actualizado
- 4 workflows diferenciales activos post-carga inicial

### v6.0 — Full Catalog VPS Autonomous
- **54,391 productos** en 1,005 subcategorías nivel 3
- Iteración autónoma via `$getWorkflowStaticData('global')` — sin scripts externos
- VPS Executor cada 30s (sin depender de Mac Mini)
- Parámetro `pagina` (corregido de `page`)

### v4 — Full Catalog (12 categorías nivel 1)
- 54,391 productos via `categoria={id}` en 12 categorías
- Fix: no actualiza `uom_id` en productos existentes
- Batch 30 productos con offset tracking

### v3 — Category Search
- 1,678 productos via `busqueda={nombre}` (cobertura parcial)

## Reglas de negocio

| Campo Odoo | Fuente Syscom | Notas |
|------------|---------------|-------|
| name | titulo | - |
| default_code / barcode | modelo (SKU) | Solo en creación |
| list_price | calculado | `(standard_price * 1.16) / 0.7` |
| standard_price | precio_descuento * 0.96 | Precio compra + 4% contado |
| categ_id | categoria | Mapeado por cat ID |
| uom_id | unidad_de_medida | Solo en creación (no update) |
| unspsc_code_id | sat_key | Buscado en catálogo Odoo |
| image_1920 | img_portada | Base64 |

**Nota**: Syscom da 4% adicional por pago de contado (transferencia).

## Email de Promociones

Detecta productos con `precio_descuento < precio_lista` (≥20% descuento) en 8 categorías:
Videovigilancia, Control de Acceso, Energía, Detección de Fuego, Automatización, Radiocomunicación, Redes, IoT/GPS.

- Escanea 5 páginas por categoría, toma top 13 por categoría → ~104 promos/email
- Destinatarios: pipess21, comercial@, german@, heber@, felipe.dealba@ (@oceantech.com.mx)
- Credencial SMTP: `8Sh1bZ6TghVIY8JW` (Gmail SMTP - pipess21)

## Configuración

### Credenciales (NO incluidas en repo)
```
N8N_URL=https://n8n.ocean-tech.com.mx
ODOO_URL=https://ocean-tech.odoo.com
ODOO_DB=ocean-tech
ODOO_UID=2
SYSCOM_CLIENT_ID=zq2u2Zr1VFGam5IzAg5UolwmeSnsChP7
```

## Notas técnicas

- BATCH_SIZE=30 para evitar OOM en n8n (2GB RAM VPS)
- Memoria liberada después de cada producto (imageB64=null)
- Estado persistido en `$getWorkflowStaticData('global')`
- Rate limit Syscom: 60 peticiones/minuto
- ~1,005 subcategorías, ~54 productos por subcategoría promedio
