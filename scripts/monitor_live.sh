#!/bin/zsh
N8N_KEY="${N8N_API_KEY}"
BASE_PRODUCTS=2464
PREV_PRODUCTS=0
FIRST_NEW_DETECTED=false

while true; do
  clear

  # Get state
  STATE=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" "https://n8n.ocean-tech.com.mx/api/v1/workflows/ylxPHHe9ymC49FTO" | python3 -c "
import sys,json
w=json.load(sys.stdin)
g=w.get('staticData',{}).get('global',{})
print(g.get('categoryIndex',5), g.get('page',1))
" 2>/dev/null)
  CAT=$(echo $STATE | awk '{print $1}')
  PAGE=$(echo $STATE | awk '{print $2}')

  # Get products
  PRODUCTS=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,os.environ.get('ODOO_PASSWORD',''),'product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=10).read()).get('result',0))
" 2>/dev/null)
  NEW=$((PRODUCTS - BASE_PRODUCTS))

  # Get running exec
  RUNNING=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" \
    "https://n8n.ocean-tech.com.mx/api/v1/executions?status=running&limit=1" | python3 -c "
import sys,json; d=json.load(sys.stdin).get('data',[]); print(d[0]['id'] if d else '-')
" 2>/dev/null)

  # Progress bar
  TOTAL_PAGES=600
  PCT=$((PAGE * 100 / TOTAL_PAGES))
  FILLED=$((PCT / 2))
  EMPTY=$((50 - FILLED))
  BAR=$(printf '█%.0s' $(seq 1 $FILLED 2>/dev/null))
  SPACE=$(printf '░%.0s' $(seq 1 $EMPTY 2>/dev/null))

  # Header
  if [ "$CAT" -gt "5" ] 2>/dev/null; then
    echo "\033[1;32m"
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║   🏁  ARGOS — CONTROL DE ACCESO COMPLETADO!              ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo "\033[0m"
    echo "  📦 Productos totales:  \033[1;32m${PRODUCTS}\033[0m"
    echo "  ➕ Productos NUEVOS:   \033[1;32m+${NEW}\033[0m"
    echo ""
    echo "  ✅ Categoría avanzó a cat[$CAT]"
    echo ""
    echo "  $(date '+%H:%M:%S') — SINCRONIZACIÓN TERMINADA"
    break
  fi

  echo "\033[1;35m╔══════════════════════════════════════════════════════════╗"
  echo "║   👁️  ARGOS — Sync Syscom → Odoo  |  cat[5]              ║"
  echo "╚══════════════════════════════════════════════════════════╝\033[0m"
  echo ""
  echo "  📄 Página:     \033[1;37m${PAGE}\033[0m / ~${TOTAL_PAGES}"
  echo "  📊 Progreso:   [\033[1;36m${BAR}\033[0;90m${SPACE}\033[0m] ${PCT}%"
  echo ""

  if [ "$RUNNING" != "-" ]; then
    echo "  🔄 Estado:     \033[1;32m● EJECUTANDO\033[0m  exec #${RUNNING}"
  else
    echo "  ⏳ Estado:     \033[1;33m○ ESPERANDO\033[0m  próximo run..."
  fi

  echo ""
  echo "  ─────────────────────────────────────────────────────"
  echo "  📦 Productos Odoo:   \033[1;37m${PRODUCTS}\033[0m"
  echo "  📦 Baseline:         2,464"

  if [ "$NEW" -gt "0" ]; then
    if [ "$FIRST_NEW_DETECTED" = "false" ]; then
      FIRST_NEW_DETECTED=true
      echo ""
      echo "  \033[1;5;32m🎉🎉🎉 ¡PRODUCTOS NUEVOS DETECTADOS! 🎉🎉🎉\033[0m"
    fi
    echo "  \033[1;32m➕ NUEVOS:             +${NEW}\033[0m"

    # Show delta from last check
    if [ "$PREV_PRODUCTS" -gt "0" ] && [ "$PRODUCTS" -gt "$PREV_PRODUCTS" ]; then
      DELTA=$((PRODUCTS - PREV_PRODUCTS))
      echo "  \033[1;33m⚡ Último batch:       +${DELTA}\033[0m"
    fi
  else
    echo "  ➕ Nuevos:           +0  \033[0;90m(updates de existentes)\033[0m"
  fi

  PREV_PRODUCTS=$PRODUCTS

  echo ""
  echo "  ─────────────────────────────────────────────────────"
  echo "  📋 EXECUTOR LOG (últimos 5):"
  tail -5 /tmp/auto_executor_log.txt 2>/dev/null | while read line; do
    if echo "$line" | grep -q "✅"; then
      echo "  \033[1;32m  $line\033[0m"
    elif echo "$line" | grep -q "❌"; then
      echo "  \033[1;31m  $line\033[0m"
    else
      echo "    $line"
    fi
  done

  echo ""
  echo "  \033[0;90m🕐 $(date '+%H:%M:%S')  |  Actualiza cada 20s  |  Ctrl+C salir\033[0m"
  sleep 20
done
