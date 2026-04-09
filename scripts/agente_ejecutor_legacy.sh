#!/bin/zsh
# AGENTE 4 - EJECUTOR — Control de Acceso con backoff inteligente
N8N_KEY="<<N8N_API_KEY>>"
BASE="https://n8n.ocean-tech.com.mx/api/v1"
WEBHOOK="https://n8n.ocean-tech.com.mx/webhook/syscom-trigger-run"
CATS=("Videovigilancia" "Redes" "Radiocomunicación" "Automatización" "Cableado" "Control de Acceso" "Energía" "Detección Incendio" "Sonido y Video" "Herramientas")

LOG="/tmp/ejecutor_log.txt"
WAIT_AFTER_ERROR=180   # 3 min entre intentos si hay error
WAIT_AFTER_SUCCESS=20  # 20s entre runs exitosos
CONSEC_ERRORS=0
MAX_CONSEC_ERRORS=3    # Tras 3 errores seguidos, esperar más

echo "$(date '+%H:%M:%S') - Ejecutor iniciado (v2 con backoff)" >> $LOG

COUNT_BEFORE=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,os.environ.get('ODOO_PASSWORD',''),'product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=20).read()).get('result',0))
" 2>/dev/null)

LAST_EXEC_ID=0
RUN=0

while true; do
  clear
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║     🔁  AGENTE 4 — EJECUTOR  Control de Acceso      ║"
  echo "╠══════════════════════════════════════════════════════╣"

  # Obtener estado
  STATE=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/workflows/ylxPHHe9ymC49FTO" | python3 -c "
import sys,json
w=json.load(sys.stdin)
g=w.get('staticData',{}).get('global',{})
print(g.get('categoryIndex',5), g.get('page',1))
" 2>/dev/null)
  CAT_IDX=$(echo $STATE | awk '{print $1}')
  PAGE=$(echo $STATE | awk '{print $2}')

  LAST_EXEC=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/executions?workflowId=ylxPHHe9ymC49FTO&limit=1" | python3 -c "
import sys,json
data=json.load(sys.stdin).get('data',[])
e=data[0] if data else {}
print(e.get('id',0), e.get('status','?'))
" 2>/dev/null)
  EXEC_ID=$(echo $LAST_EXEC | awk '{print $1}')
  EXEC_STATUS=$(echo $LAST_EXEC | awk '{print $2}')

  echo "║  📊 Runs: $RUN  |  Errores consecutivos: $CONSEC_ERRORS"
  echo "║  📂 cat[$CAT_IDX] = ${CATS[$((CAT_IDX+1))]}"
  echo "║  📄 Página: $PAGE"
  echo "║  🔄 Último: #$EXEC_ID [$EXEC_STATUS]"
  echo "║  📦 Productos antes: $COUNT_BEFORE"
  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  📋 LOG:"
  tail -5 $LOG 2>/dev/null | while read line; do echo "║    $line"; done
  echo "╠══════════════════════════════════════════════════════╣"

  # ¿Terminó?
  if [ "$CAT_IDX" -gt "5" ] 2>/dev/null; then
    COUNT_AFTER=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,os.environ.get('ODOO_PASSWORD',''),'product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=20).read()).get('result',0))
" 2>/dev/null)
    ADDED=$((COUNT_AFTER - COUNT_BEFORE))
    echo "║  🏁 ¡CONTROL DE ACCESO COMPLETADO!"
    echo "║  📦 ANTES:  $COUNT_BEFORE"
    echo "║  📦 DESPUÉS: $COUNT_AFTER"
    echo "║  ➕ NUEVOS: $ADDED"
    echo "$(date '+%H:%M:%S') - COMPLETADO: +$ADDED productos" >> $LOG
    echo "╚══════════════════════════════════════════════════════╝"
    sleep 600
    continue
  fi

  # Decidir si disparar
  if [ "$EXEC_ID" != "$LAST_EXEC_ID" ] && \
     ([ "$EXEC_STATUS" = "success" ] || [ "$EXEC_STATUS" = "error" ] || [ "$EXEC_STATUS" = "crashed" ] || [ "$EXEC_STATUS" = "warning" ]); then

    LAST_EXEC_ID=$EXEC_ID

    if [ "$EXEC_STATUS" = "success" ]; then
        CONSEC_ERRORS=0
        RUN=$((RUN+1))
        WH=$(curl -s -o /dev/null -w "%{http_code}" -X POST $WEBHOOK -H "Content-Type: application/json" -d '{}' --max-time 10)
        echo "║  🚀 Disparo #$RUN → $WH"
        echo "$(date '+%H:%M:%S') - ✅ Disparo #$RUN cat[$CAT_IDX] pág $PAGE" >> $LOG
        echo "╚══════════════════════════════════════════════════════╝"
        sleep $WAIT_AFTER_SUCCESS
    else
        CONSEC_ERRORS=$((CONSEC_ERRORS+1))
        if [ $CONSEC_ERRORS -ge $MAX_CONSEC_ERRORS ]; then
            WAIT=$((WAIT_AFTER_ERROR * CONSEC_ERRORS))
            [ $WAIT -gt 600 ] && WAIT=600  # máx 10 min
            echo "║  ⚠️  $CONSEC_ERRORS errores → esperando ${WAIT}s"
            echo "$(date '+%H:%M:%S') - ⚠️  $CONSEC_ERRORS errores consecutivos, wait ${WAIT}s" >> $LOG
            echo "╚══════════════════════════════════════════════════════╝"
            sleep $WAIT
        else
            RUN=$((RUN+1))
            WH=$(curl -s -o /dev/null -w "%{http_code}" -X POST $WEBHOOK -H "Content-Type: application/json" -d '{}' --max-time 10)
            echo "║  🔄 Reintento #$RUN (error #$CONSEC_ERRORS) → $WH"
            echo "$(date '+%H:%M:%S') - 🔄 Reintento #$RUN error #$CONSEC_ERRORS" >> $LOG
            echo "╚══════════════════════════════════════════════════════╝"
            sleep $WAIT_AFTER_ERROR
        fi
    fi
  else
    echo "║  ⏳ Esperando resultado de #$EXEC_ID..."
    echo "╚══════════════════════════════════════════════════════╝"
    sleep 15
  fi
done
