#!/bin/zsh
# CENTINELA — Vigila ejecuciones Syscom→Odoo en tiempo real
N8N_KEY="${N8N_API_KEY}"
BASE="https://n8n.ocean-tech.com.mx/api/v1"
CATS=("Videovigilancia" "Redes" "Radiocomunicación" "Automatización" "Cableado" "Control de Acceso" "Energía" "Detección Incendio" "Sonido y Video" "Herramientas")

while true; do
  clear
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║     🛡️  CENTINELA — Vigila Syscom → Odoo              ║"
  echo "╠══════════════════════════════════════════════════════╣"

  STATE=$(curl -s -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/workflows/ylxPHHe9ymC49FTO" | python3 -c "
import sys,json
w=json.load(sys.stdin)
g=w.get('staticData',{}).get('global',{})
print(g.get('categoryIndex',0), g.get('page',1), g.get('lastSave','N/A')[:19], g.get('_foundEmptyPage',False))
" 2>/dev/null)

  CAT_IDX=$(echo $STATE | awk '{print $1}')
  PAGE=$(echo $STATE | awk '{print $2}')
  LAST_SAVE=$(echo $STATE | awk '{print $3}')
  EMPTY=$(echo $STATE | awk '{print $4}')
  CAT_NAME=${CATS[$((CAT_IDX+1))]}

  echo "║  📂 Categoría:  [$CAT_IDX] $CAT_NAME"
  echo "║  📄 Página:     $PAGE"
  echo "║  💾 Último run: $LAST_SAVE"
  echo "║  ⚠️  Vacía:     $EMPTY"
  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  📋 ÚLTIMAS 5 EJECUCIONES:"

  curl -s -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/executions?workflowId=ylxPHHe9ymC49FTO&limit=5" | python3 -c "
import sys,json
data=json.load(sys.stdin).get('data',[])
for e in data:
  s=e.get('status','?')
  icon='✅' if s=='success' else '❌' if s=='error' else '💥' if s=='crashed' else '🔄'
  t=(e.get('startedAt') or '')[:16]
  print(f'║  {icon} #{e[\"id\"]:>5} {t}  [{s}]')
" 2>/dev/null

  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  🕐 $(date '+%Y-%m-%d %H:%M:%S')  (refresca c/30s)       ║"
  echo "╚══════════════════════════════════════════════════════╝"
  sleep 30
done
