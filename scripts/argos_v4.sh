#!/bin/zsh
# 👁️ ARGOS v4 — Monitor Full Catalog Sync (54,391 productos)
N8N_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwYmJmOWMxNi1iY2QwLTRkZWYtOWJiMy1hZjA0ZjVkNDcyMWQiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiYjc4MjRjMTAtOTQxMS00ZDhmLTgwNTEtMWYzYzYzNmZmYTBkIiwiaWF0IjoxNzcyMjEyMzYwfQ.oFs4zmce5fdQv-hXHuVdt56pBI9i_24EFk_TBE3WNu8"
BASE="https://n8n.dealbapropiedades.com.mx/api/v1"
WF_ID="2rMRq0B9ewvd1lBn"
CATS=("Videovigilancia" "Radiocomunicación" "Redes e IT" "IoT/GPS/Telemática" "Energía/Herramientas" "Automatización" "Control Acceso" "Det. Fuego" "Marketing" "Cableado" "Audio/Video" "Industria/BMS")
TOTAL_CATS=12
BASE_PRODUCTS=2615
PREV_PRODUCTS=0
START_TIME=$(date +%s)

while true; do
  clear

  STATE=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/workflows/$WF_ID" | python3 -c "
import sys,json
w=json.load(sys.stdin)
g=w.get('staticData',{}).get('global',{})
print(g.get('categoryIndex',0), g.get('page',1), g.get('offset',0), g.get('totalCreated',0), g.get('totalUpdated',0), g.get('totalErrors',0), g.get('totalProcessed',0))
" 2>/dev/null)
  CAT=$(echo $STATE | awk '{print $1}')
  PAGE=$(echo $STATE | awk '{print $2}')
  OFFSET=$(echo $STATE | awk '{print $3}')
  G_CREATED=$(echo $STATE | awk '{print $4}')
  G_UPDATED=$(echo $STATE | awk '{print $5}')
  G_ERRORS=$(echo $STATE | awk '{print $6}')
  G_PROCESSED=$(echo $STATE | awk '{print $7}')

  PRODUCTS=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,'M1ercole\$','product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=10).read()).get('result',0))
" 2>/dev/null)
  NEW=$((PRODUCTS - BASE_PRODUCTS))

  RUNNING=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" \
    "$BASE/executions?status=running&workflowId=$WF_ID&limit=1" | python3 -c "
import sys,json; d=json.load(sys.stdin).get('data',[]); print(d[0]['id'] if d else '-')
" 2>/dev/null)

  # Progress
  PCT=0
  [ "$G_PROCESSED" -gt "0" ] 2>/dev/null && PCT=$((G_PROCESSED * 100 / 54391))
  [ $PCT -gt 100 ] && PCT=100
  FILLED=$((PCT / 2))
  [ $FILLED -gt 50 ] && FILLED=50
  EMPTY=$((50 - FILLED))
  BAR=$(printf '█%.0s' $(seq 1 $FILLED 2>/dev/null))
  SPACE=$(printf '░%.0s' $(seq 1 $EMPTY 2>/dev/null))

  # ETA calculation
  NOW=$(date +%s)
  ELAPSED=$((NOW - START_TIME))
  if [ "$G_PROCESSED" -gt "100" ] 2>/dev/null && [ "$ELAPSED" -gt "60" ]; then
    RATE=$(python3 -c "print(round($G_PROCESSED / $ELAPSED * 3600))" 2>/dev/null)
    REMAINING=$((54391 - G_PROCESSED))
    ETA_SECS=$(python3 -c "print(int($REMAINING / ($G_PROCESSED / $ELAPSED)))" 2>/dev/null)
    ETA_HRS=$((ETA_SECS / 3600))
    ETA_MIN=$(( (ETA_SECS % 3600) / 60 ))
    ETA_STR="${ETA_HRS}h ${ETA_MIN}m"
  else
    RATE="-"
    ETA_STR="calculando..."
  fi

  if [ "$CAT" -ge "$TOTAL_CATS" ] 2>/dev/null; then
    echo "\033[1;32m"
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║   🏁  ARGOS v4 — CATALOGO COMPLETO SINCRONIZADO!       ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo "\033[0m"
    echo "  📦 Total Odoo:     \033[1;32m${PRODUCTS}\033[0m"
    echo "  ➕ Nuevos:         \033[1;32m+${NEW}\033[0m"
    echo "  ✨ Creados:        ${G_CREATED}"
    echo "  🔄 Actualizados:   ${G_UPDATED}"
    echo "  ❌ Errores:        ${G_ERRORS}"
    echo "  📊 Procesados:     ${G_PROCESSED} / 54,391"
    echo ""
    echo "  $(date '+%H:%M:%S') — COMPLETADO"
    sleep 300
    continue
  fi

  echo "\033[1;36m╔══════════════════════════════════════════════════════════╗"
  echo "║   👁️  ARGOS v4 — Full Catalog Sync (54,391)             ║"
  echo "╚══════════════════════════════════════════════════════════╝\033[0m"
  echo ""
  echo "  📂 Categoría:  [\033[1;37m$CAT\033[0m/$TOTAL_CATS] ${CATS[$((CAT+1))]}"
  echo "  📄 Página:     \033[1;37m${PAGE}\033[0m  offset: ${OFFSET}"
  echo "  📊 Progreso:   [\033[1;36m${BAR}\033[0;90m${SPACE}\033[0m] ${PCT}%"
  echo "  ⏱️  ETA:        ${ETA_STR}  (${RATE} prod/hr)"
  echo ""

  if [ "$RUNNING" != "-" ]; then
    echo "  🔄 Estado:     \033[1;32m● EJECUTANDO\033[0m  exec #${RUNNING}"
  else
    echo "  ⏳ Estado:     \033[1;33m○ ESPERANDO\033[0m"
  fi

  echo ""
  echo "  ─────────────────────────────────────────────────────"
  echo "  📦 Productos Odoo:   \033[1;37m${PRODUCTS}\033[0m  (base: ${BASE_PRODUCTS})"
  if [ "$NEW" -gt "0" ]; then
    echo "  \033[1;32m➕ NUEVOS:             +${NEW}\033[0m"
    if [ "$PREV_PRODUCTS" -gt "0" ] && [ "$PRODUCTS" -gt "$PREV_PRODUCTS" ]; then
      DELTA=$((PRODUCTS - PREV_PRODUCTS))
      echo "  \033[1;33m⚡ Último batch:       +${DELTA}\033[0m"
    fi
  fi
  echo "  ✨ Creados:          \033[1;32m${G_CREATED}\033[0m"
  echo "  🔄 Actualizados:     ${G_UPDATED}"
  echo "  ❌ Errores:          \033[1;31m${G_ERRORS}\033[0m"
  echo "  📊 Procesados:       ${G_PROCESSED} / 54,391"

  PREV_PRODUCTS=$PRODUCTS

  echo ""
  echo "  ─────────────────────────────────────────────────────"
  echo "  📋 ARES LOG (últimos 5):"
  tail -5 /tmp/ares_v4_log.txt 2>/dev/null | while read line; do
    if echo "$line" | grep -q "✅"; then
      echo "  \033[1;32m  $line\033[0m"
    elif echo "$line" | grep -q "⚠️\|❌"; then
      echo "  \033[1;31m  $line\033[0m"
    else
      echo "    $line"
    fi
  done

  echo ""
  echo "  \033[0;90m🕐 $(date '+%H:%M:%S')  |  Refresca c/20s  |  Ctrl+C salir\033[0m"
  sleep 20
done
