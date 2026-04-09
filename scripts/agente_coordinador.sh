#!/bin/zsh
# HERMES — Mensajero de los dioses. Reportes y comunicaciones por correo
N8N_KEY="${N8N_API_KEY}"
BASE="https://n8n.ocean-tech.com.mx/api/v1"

while true; do
  clear
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║     📧  HERMES — Mensajero & Reportes                ║"
  echo "╠══════════════════════════════════════════════════════╣"

  curl -s -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/executions?workflowId=fxr0UXKZNsJPZ9Bv&limit=5" | python3 -c "
import sys,json
data=json.load(sys.stdin).get('data',[])
if not data:
    print('║  ⚠️  Sin ejecuciones del coordinador')
else:
    print('║  📋 Últimas 5 ejecuciones del coordinador:')
    for e in data:
        s=e.get('status','?')
        icon='✅' if s=='success' else '❌' if s=='error' else '🔄'
        t=(e.get('startedAt') or '')[:16]
        print(f'║  {icon} #{e[\"id\"]:>5} {t}  [{s}]')
" 2>/dev/null

  echo "╠══════════════════════════════════════════════════════╣"

  # Próximo envío de correo (cada 3h)
  python3 -c "
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)
next_3h = now.replace(minute=0, second=0, microsecond=0)
while next_3h.hour % 3 != 0:
    next_3h += timedelta(hours=1)
if next_3h <= now:
    next_3h += timedelta(hours=3)
diff = next_3h - now
mins = int(diff.total_seconds() / 60)
print(f'║  🕐 Próximo reporte: {next_3h.strftime(\"%H:%M\")} UTC  (en {mins} min)')
print(f'║  📮 Destino: pipess21@gmail.com')
"

  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  🕐 $(date '+%Y-%m-%d %H:%M:%S')  (refresca c/60s)       ║"
  echo "╚══════════════════════════════════════════════════════╝"
  sleep 60
done
