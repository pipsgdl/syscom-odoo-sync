# Syscom → Odoo Sync v2.0

Sincronizacion automatica del catalogo de productos **Syscom** hacia **Odoo ERP** via n8n, para empresa de seguridad electronica en Jalisco, Mexico.

## Arquitectura

```
Syscom API ──→ n8n Workflows ──→ Odoo ERP (ocean-tech-0326)
                    ↑
             Webhook Trigger
                    ↑
             Auto-Executor (tmux agents)
                    ↑
             Monitor Live + Validator + Coordinator
```

## Workflows n8n

### `workflow/syscom_odoo_sync.json` (Main Sync)
Workflow principal (ID: `ylxPHHe9ymC49FTO`). Procesa 20 paginas por ejecucion (~400 productos) en 10 categorias.

**Flujo por ejecucion:**
1. Webhook Trigger → Get Syscom Token (OAuth2 client_credentials)
2. Init Search Terms v9 → HTTP Syscom Products (20 pags x 20 productos)
3. Process Pagination v10 → Map Syscom→Odoo (transforma campos)
4. Store Original State → Search SAT Code → Enrich with SAT
5. Get many items (busca en Odoo por `default_code`)
6. If exists → Update / Create (con descarga de imagen)
7. Guardar Estado v9 (avanza pagina +20 en staticData)

### `workflow/coordinator.json` (Coordinator)
Workflow coordinador (ID: `fxr0UXKZNsJPZ9Bv`). Envia reportes de estado por correo.

### `workflow/validator.json` (Validator)
Workflow validador (ID: `Az3wFgYIk3ySqwXr`). Analiza completitud de productos en Odoo usando `search_count` (ligero, sin cargar imagenes). Envia reporte HTML por email a las 9am CST.

**Metricas que reporta:**
- Total productos activos
- % completitud (precio + imagen + SKU)
- Campos faltantes: imagen, precio, descripcion, SKU
- Top 20 productos incompletos

## Agentes (scripts tmux)

| Script | Funcion | Refresh |
|--------|---------|---------|
| `agente_sync_monitor.sh` | Monitor de ejecuciones n8n en tiempo real | 30s |
| `agente_validador.sh` | Validador de productos en Odoo (local) | 60s |
| `agente_coordinador.sh` | Monitor del workflow coordinador | 60s |
| `agente_ejecutor.sh` | Auto-ejecutor con backoff inteligente | continuo |
| `agente_coordinador_chat.py` | Coordinador interactivo via Claude CLI | interactivo |
| `monitor_live.sh` | Monitor visual con barra de progreso y deteccion de nuevos productos | 20s |

### Setup tmux
```bash
tmux new-session -s agentes
# Window 0: Monitor live
bash scripts/monitor_live.sh
# Window 1: Sync monitor
bash scripts/agente_sync_monitor.sh
# Window 2: Validador
bash scripts/agente_validador.sh
# Window 3: Ejecutor
bash scripts/agente_ejecutor.sh
# Window 4: Coordinador chat
python3 scripts/agente_coordinador_chat.py
```

## Categorias Syscom

| Idx | Categoria |
|-----|-----------|
| 0 | Videovigilancia |
| 1 | Redes |
| 2 | Radiocomunicacion |
| 3 | Automatizacion e Intrusion |
| 4 | Cableado Estructurado |
| 5 | Control de Acceso |
| 6 | Energia |
| 7 | Deteccion de Incendio |
| 8 | Sonido y Video |
| 9 | Herramientas |

## Configuracion

### Variables de entorno requeridas
```bash
export N8N_API_KEY="..."
export ODOO_PASSWORD="..."
export ANTHROPIC_API_KEY="sk-ant-..."  # Para coordinador chat
```

### Credenciales (NO incluidas en repo)
```
N8N_URL=https://n8n.dealbapropiedades.com.mx
ODOO_URL=https://ocean-tech-0326.odoo.com
ODOO_DB=ocean-tech-0326
ODOO_UID=2
SYSCOM_CLIENT_ID=zq2u2Zr1VFGam5IzAg5UolwmeSnsChP7
WEBHOOK_URL=https://n8n.dealbapropiedades.com.mx/webhook/syscom-trigger-run
```

## Notas tecnicas

- n8n server: 2GB RAM → `PAGES_PER_RUN = 20` para evitar OOM
- Syscom API rate limit: esperar 20+ min tras burst de requests
- Estado persistido en `$getWorkflowStaticData('global')` de n8n
- Schedule: cada 20 min de 11am-10pm CST
- Validator usa `search_count` (no `search_read` con imagenes) para evitar OOM

## Changelog

### v2.0 (2026-03-20)
- Reescritura completa de agentes de monitoreo (tmux multi-window)
- Nuevo `monitor_live.sh` con barra de progreso y deteccion de nuevos productos
- Nuevo `agente_ejecutor.sh` con backoff inteligente (3 errores max, espera progresiva)
- Nuevo `agente_coordinador_chat.py` con Claude CLI interactivo
- Workflow Validator reescrito: `search_count` en lugar de `search_read` (fix OOM)
- Workflow Coordinator y Validator exportados
- Fix: `Process Pagination v10` itera `$input.all()` en vez de `$input.first()`
- Fix: `Init Search Terms v9` guarda `_startPage`, no pre-avanza pagina
- Fix: `categoria_actual` como objeto `{nombre: ...}` en lugar de string
- Credenciales sanitizadas con variables de entorno

### v1.0 (2026-03-20)
- Commit inicial con workflow principal y scripts basicos
