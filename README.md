# Syscom → Odoo Sync

Sincronización automática del catálogo de productos **Syscom** hacia **Odoo ERP** vía n8n, para empresa de seguridad electrónica en Jalisco, México.

## Arquitectura

```
Syscom API → n8n Workflow → Odoo ERP (ocean-tech-0326)
                ↑
         Webhook Trigger
                ↑
         Auto-Executor (macOS)
```

## Componentes

### `workflow/syscom_odoo_sync.json`
Workflow principal de n8n (ID: `ylxPHHe9ymC49FTO`). Procesa 20 páginas por ejecución (400 productos), en 10 categorías:

| Idx | Categoría |
|-----|-----------|
| 0 | Videovigilancia |
| 1 | Redes |
| 2 | Radiocomunicación |
| 3 | Automatización e Intrusión |
| 4 | Cableado Estructurado |
| 5 | Control de Acceso |
| 6 | Energía |
| 7 | Detección de Incendio |
| 8 | Sonido y Video |
| 9 | Herramientas |

**Flujo por ejecución:**
1. Webhook Trigger → Get Syscom Token (OAuth2 client_credentials)
2. Init Search Terms → HTTP Syscom Products (20 páginas × 20 productos)
3. Process Pagination → Map Syscom Odoo (transforma campos)
4. Store Original State → Search SAT Code (busca clave UNSPSC en Odoo)
5. Enrich with SAT → Get many items (busca producto en Odoo por `default_code`)
6. If exists → Update an item / Create an item (con descarga de imagen)
7. Guardar Estado (avanza página +20 en staticData)

**Bugs corregidos:**
- `Merge` node en modo `combine` esperaba 2 inputs → cambio a `passThrough` + `numberInputs:1`
- `Merge1` node misma condición para path de imagen → mismo fix
- Conexión `Store Original State → Merge` eliminada (deadlock cuando producto ya existe)
- Conexión `Edit Fields → Merge1` eliminada (deadlock cuando producto sin imagen)

### `scripts/auto_executor.sh`
Dispara automáticamente el webhook tras cada ejecución exitosa, con backoff en errores. Detecta cuando `categoryIndex > 5` para reportar completado.

```bash
# Ejecutar en background
/tmp/auto_executor.sh &
```

### `scripts/monitor_sync.sh`
Monitor visual en terminal, se actualiza cada 30s. Muestra progreso, última ejecución, y conteo de productos en Odoo.

```bash
/tmp/monitor_sync.sh
```

### `scripts/coordinador_chat.py`
Agente interactivo Claude que consulta el estado del sistema en tiempo real.

```bash
python3 scripts/coordinador_chat.py
```

## Configuración

### Variables de entorno requeridas
```bash
ANTHROPIC_API_KEY=sk-ant-...   # Para coordinador chat
```

### Credenciales (NO incluidas en repo)
Crear archivo `.env` (no commitear):
```
N8N_URL=https://n8n.dealbapropiedades.com.mx
N8N_API_KEY=eyJ...
ODOO_URL=https://ocean-tech-0326.odoo.com
ODOO_DB=ocean-tech-0326
ODOO_UID=2
ODOO_PASSWORD=...
SYSCOM_CLIENT_ID=zq2u2Zr1VFGam5IzAg5UolwmeSnsChP7
SYSCOM_CLIENT_SECRET=...
WEBHOOK_URL=https://n8n.dealbapropiedades.com.mx/webhook/syscom-trigger-run
```

## Estado de la sincronización

| Categoría | Estado | Productos |
|-----------|--------|-----------|
| Control de Acceso (cat[5]) | 🔄 En progreso | ~600 páginas |
| Demás categorías | ⏳ Pendiente | - |

**Tiempo estimado por run:** ~13 min (400 productos)
**Runs necesarios para Control de Acceso:** ~30 (600 páginas / 20 por run)

## Notas técnicas

- n8n server: 2GB RAM → `PAGES_PER_RUN = 20` para evitar OOM
- Syscom API rate limit: esperar 20+ min tras burst de requests
- Estado persistido en `$getWorkflowStaticData('global')` de n8n
- Schedule: cada 20 min de 11am-10pm CST cuando el executor no está activo
