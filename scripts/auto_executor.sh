#!/bin/zsh
N8N_KEY="<<N8N_API_KEY>>"
BASE="https://n8n.ocean-tech.com.mx/api/v1"
WEBHOOK="https://n8n.ocean-tech.com.mx/webhook/syscom-trigger-run"
LOG="/tmp/auto_executor_log.txt"
WAIT_SUCCESS=30
WAIT_ERROR=300

echo "$(date '+%H:%M:%S') - Auto-executor started" >> $LOG

LAST_EXEC_ID=0
RUN=0

while true; do
  # Get current state
  STATE=$(curl -s --max-time 10 -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/workflows/ylxPHHe9ymC49FTO" | python3 -c "
import sys,json
w=json.load(sys.stdin)
g=w.get('staticData',{}).get('global',{})
print(g.get('categoryIndex',5), g.get('page',1))
" 2>/dev/null)
  CAT_IDX=$(echo $STATE | awk '{print $1}')
  PAGE=$(echo $STATE | awk '{print $2}')

  # Check if Control de Acceso (cat 5) is done - moved to cat 6+
  if [ "$CAT_IDX" -gt "5" ] 2>/dev/null; then
    COUNT_AFTER=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,os.environ.get('ODOO_PASSWORD',''),'product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=15).read()).get('result',0))
" 2>/dev/null)
    echo "$(date '+%H:%M:%S') - ✅ CONTROL DE ACCESO COMPLETADO! cat[$CAT_IDX] Products: $COUNT_AFTER" >> $LOG
    echo "COMPLETADO: cat[$CAT_IDX] pág $PAGE productos: $COUNT_AFTER" >> /tmp/auto_executor_DONE.txt
    break
  fi

  # Get latest execution status
  EXEC_DATA=$(curl -s --max-time 10 -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/executions?workflowId=ylxPHHe9ymC49FTO&limit=1" | python3 -c "
import sys,json
data=json.load(sys.stdin).get('data',[])
e=data[0] if data else {}
print(e.get('id',0), e.get('status','?'))
" 2>/dev/null)
  EXEC_ID=$(echo $EXEC_DATA | awk '{print $1}')
  EXEC_STATUS=$(echo $EXEC_DATA | awk '{print $2}')

  # Still running?
  RUNNING=$(curl -s --max-time 10 -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/executions?status=running&limit=1" | python3 -c "
import sys,json; d=json.load(sys.stdin).get('data',[]); print(len(d))
" 2>/dev/null)

  if [ "$RUNNING" = "1" ]; then
    # Execution in progress - wait 60s and check again
    sleep 60
    continue
  fi

  # New completed execution?
  if [ "$EXEC_ID" != "$LAST_EXEC_ID" ] && [ "$EXEC_STATUS" = "success" ]; then
    LAST_EXEC_ID=$EXEC_ID
    RUN=$((RUN+1))
    echo "$(date '+%H:%M:%S') - ✅ Run #$RUN exec #$EXEC_ID success cat[$CAT_IDX] pág $PAGE" >> $LOG
    sleep $WAIT_SUCCESS
    # Trigger next
    WH=$(curl -s -o /dev/null -w "%{http_code}" -X POST $WEBHOOK -H "Content-Type: application/json" -d '{}' --max-time 15)
    echo "$(date '+%H:%M:%S') - Triggered next: HTTP $WH" >> $LOG
  elif [ "$EXEC_ID" != "$LAST_EXEC_ID" ] && ([ "$EXEC_STATUS" = "error" ] || [ "$EXEC_STATUS" = "crashed" ]); then
    LAST_EXEC_ID=$EXEC_ID
    echo "$(date '+%H:%M:%S') - ❌ Error exec #$EXEC_ID, waiting ${WAIT_ERROR}s" >> $LOG
    sleep $WAIT_ERROR
    # Retry
    WH=$(curl -s -o /dev/null -w "%{http_code}" -X POST $WEBHOOK -H "Content-Type: application/json" -d '{}' --max-time 15)
    echo "$(date '+%H:%M:%S') - Retried: HTTP $WH" >> $LOG
  else
    # No change, wait
    sleep 30
  fi
done
