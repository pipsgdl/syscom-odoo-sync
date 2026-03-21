# Syscom → Odoo Sync

Sincronización automática del catálogo completo de **Syscom** (54,391 productos) hacia **Odoo ERP** vía n8n.

## Arquitectura

```
Syscom API (categoria={id}) → n8n Workflow → Odoo ERP (ocean-tech-0326)
                                   ↑
                            Webhook Trigger
                                   ↑
                         ARES v4 (Executor)
                                   ↑
                        ARGOS v4 (Monitor)
```

## Versiones

### v4 (actual) — Full Catalog
- **54,391 productos** en 12 categorías via `categoria={id}`
- Fix: no actualiza `uom_id` en productos existentes (evita error de asientos contables)
- Batch de 30 productos con offset tracking dentro de páginas
- Workflow simplificado: 3 nodos (Webhook + Manual Trigger → Code)
- Contadores globales en staticData

### v3 — Category Search
- 1,678 productos via `busqueda={nombre}` (solo 10 categorías, cobertura parcial)
- Batch de 20 productos

### v2.0 — Agent Team
- Reescritura de agentes de monitoreo (tmux multi-window)
- Workflow Validator con `search_count`

### v1.0 — Initial
- Commit inicial con workflow principal y scripts básicos

## Componentes

### `workflow/syscom_odoo_sync_v4.json`
Workflow n8n (ID: `2rMRq0B9ewvd1lBn`). Procesa 30 productos por ejecución en 12 categorías:

| Idx | Cat ID | Categoría | Productos | Páginas |
|-----|--------|-----------|-----------|---------|
| 0 | 22 | Videovigilancia | 8,632 | 144 |
| 1 | 25 | Radiocomunicación | 6,690 | 112 |
| 2 | 26 | Redes e IT | 9,018 | 151 |
| 3 | 27 | IoT / GPS / Telemática | 1,906 | 32 |
| 4 | 30 | Energía / Herramientas | 6,769 | 113 |
| 5 | 32 | Automatización e Intrusión | 3,609 | 61 |
| 6 | 37 | Control de Acceso | 5,823 | 98 |
| 7 | 38 | Detección de Fuego | 2,170 | 37 |
| 8 | 65747 | Marketing | 130 | 3 |
| 9 | 65811 | Cableado Estructurado | 7,044 | 118 |
| 10 | 66523 | Audio y Video | 1,725 | 29 |
| 11 | 66630 | Industria / BMS / Robots | 875 | 15 |

### `workflow/sync_v4_code.js`
Código del nodo Code separado para facilitar revisión.

### `scripts/ares_v4.sh`
Ejecutor automático. Dispara webhook, espera nueva ejecución, repite.

### `scripts/argos_v4.sh`
Monitor visual con progreso, ETA, y log de ARES.

## Reglas de negocio

| Campo Odoo | Fuente Syscom | Notas |
|------------|---------------|-------|
| name | titulo | - |
| default_code / barcode | modelo (SKU) | Solo en creación |
| list_price | calculado | `(standard_price * 1.16) / 0.7` |
| standard_price | precio | Precio compra MXN |
| categ_id | categoria | Mapeado por cat ID |
| uom_id | unidad_de_medida | Solo en creación (no update) |
| unspsc_code_id | sat_key | Buscado en catálogo Odoo |
| image_1920 | img_portada | Base64 |

## Errores corregidos en v4

1. **"Este producto ya está siendo utilizado en asientos contables"** — Se removió `uom_id` de los updates
2. **Cobertura parcial** — De `busqueda={nombre}` (271 prods/cat) a `categoria={id}` (8,632 prods/cat)
3. **Productos saltados** — Offset tracking para procesar todos los productos de cada página
4. **Categorías faltantes** — De 10 a 12 categorías (IoT/GPS, Marketing, Industria/BMS)

## Configuración

### Credenciales (NO incluidas en repo)
```
N8N_URL=https://n8n.dealbapropiedades.com.mx
N8N_API_KEY=eyJ...
ODOO_URL=https://ocean-tech-0326.odoo.com
ODOO_DB=ocean-tech-0326
SYSCOM_CLIENT_ID=zq2u2Zr1VFGam5IzAg5UolwmeSnsChP7
```

## Notas técnicas

- BATCH_SIZE=30 para evitar OOM en n8n (2GB RAM)
- Memoria liberada después de cada producto (imageB64=null)
- Estado persistido en `$getWorkflowStaticData('global')`
- Syscom API: ~60 productos por página
- Estimado: ~1,800 ejecuciones para catálogo completo
