#!/bin/zsh
N8N_KEY="<<N8N_API_KEY>>"

while true; do
  clear
  echo "\033[1;35m╔══════════════════════════════════════════════════╗"
  echo "║   🔁  MONITOR SYNC Syscom → Odoo                ║"
  echo "║   Control de Acceso (cat[5])                     ║"
  echo "╚══════════════════════════════════════════════════╝\033[0m"
  echo ""

  STATE=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" "https://n8n.dealbapropiedades.com.mx/api/v1/workflows/ylxPHHe9ymC49FTO" | python3 -c "
import sys,json
w=json.load(sys.stdin)
g=w.get('staticData',{}).get('global',{})
print(g.get('categoryIndex',5), g.get('page',1), g.get('lastSave','?'))
" 2>/dev/null)
  CAT=$(echo $STATE | awk '{print $1}')
  PAGE=$(echo $STATE | awk '{print $2}')
  SAVED=$(echo $STATE | awk '{print $3}')

  PRODUCTS=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,os.environ.get('ODOO_PASSWORD',''),'product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=10).read()).get('result',0))
" 2>/dev/null)
  NEW=$((PRODUCTS - 2464))

  RUNNING=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" \
    "https://n8n.dealbapropiedades.com.mx/api/v1/executions?status=running&limit=1" | python3 -c "
import sys,json; d=json.load(sys.stdin).get('data',[]); print(d[0]['id'] if d else '-')
" 2>/dev/null)

  LAST=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" \
    "https://n8n.dealbapropiedades.com.mx/api/v1/executions?workflowId=ylxPHHe9ymC49FTO&limit=1" | python3 -c "
import sys,json; d=json.load(sys.stdin).get('data',[]); e=d[0] if d else {}; print(f\"#{e.get('id')} [{e.get('status')}]\")
" 2>/dev/null)

  if [ "$CAT" -gt "5" ] 2>/dev/null; then
    echo "\033[1;32m  🏁 CONTROL DE ACCESO COMPLETADO!\033[0m"
    echo "  📦 Productos: $PRODUCTS"
    echo "  ➕ Nuevos: $NEW"
  else
    echo "  📂 Categoría:  cat[$CAT] Control de Acceso"
    echo "  📄 Página:     $PAGE / ~600 estimado"
    echo "  📈 Progreso:   $(( PAGE * 100 / 600 ))%  [$PAGE páginas completadas]"
    echo ""
    if [ "$RUNNING" != "-" ]; then
      echo "  \033[1;32m🔄 EJECUTANDO: exec #$RUNNING\033[0m"
    else
      echo "  \033[1;33m⏳ ESPERANDO próxima ejecución...\033[0m"
    fi
    echo "  🔄 Última:     $LAST"
    echo ""
    echo "  📦 Productos:  $PRODUCTS (base: 2,464)"
    echo "  ➕ Nuevos:    $NEW"
    echo ""
    echo "  🕐 Actualizado: $(date '+%H:%M:%S')"
    echo ""
    echo "  📋 AUTO-EXECUTOR:"
    tail -5 /tmp/auto_executor_log.txt 2>/dev/null | while read line; do echo "     $line"; done
  fi

  echo ""
  echo "  [Ctrl+C para salir]"
  sleep 30
done
