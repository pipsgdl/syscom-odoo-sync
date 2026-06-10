#!/usr/bin/env python3
"""
Auto-update checklist de proveedores leyendo:
- Logs reales de syncs en ~/syscom-odoo-sync/logs/
- Progress JSONs (*_sync_progress.json)
- Conteos en vivo Odoo (productos por partner_id)
- PIDs activos de scrapers

Genera:
- /Volumes/HIKSEMI 512/Claude code/CHECKLIST_PROVEEDORES.md
- /Volumes/HIKSEMI 512/Claude code/CHECKLIST_PROVEEDORES.csv
- /Volumes/HIKSEMI 512/ObsidianVault/proyectos/checklist-proveedores.md (sync Obsidian)

Uso:
    python3 update_checklist_proveedores.py
    python3 update_checklist_proveedores.py --notion  # también pushea Notion (require notion-mcp)

Diseñado para correr al final de daily_sync_orchestrator.sh
"""
import json, csv, os, subprocess, glob, re, sys
from datetime import datetime, timedelta
from pathlib import Path

REPO       = Path(os.path.expanduser('~/syscom-odoo-sync'))
LOG_DIR    = REPO / 'logs'
SCRIPT_DIR = REPO / 'scripts'
DUMP       = '/Volumes/HIKSEMI 512/Claude code/LICITABOT/data/supabase_dump/proveedores.json'
OUT_MD     = '/Volumes/HIKSEMI 512/Claude code/CHECKLIST_PROVEEDORES.md'
OUT_CSV    = '/Volumes/HIKSEMI 512/Claude code/CHECKLIST_PROVEEDORES.csv'
OUT_VAULT  = '/Volumes/HIKSEMI 512/ObsidianVault/proyectos/checklist-proveedores.md'
ODOO       = 'https://ocean-tech.odoo.com/jsonrpc'

# Página Notion: "📊 Checklist Sync Proveedores" (bajo Mi Centro de Control)
# Para actualizar via Claude Code: open Claude → "actualiza checklist de Notion"
NOTION_PAGE_ID  = '3595ef43-4066-817c-8253-f0116f537144'
NOTION_PAGE_URL = 'https://www.notion.so/3595ef434066817c8253f0116f537144'

# Map nombre proveedor → partner_id Odoo (para conteo productos)
PARTNER_IDS = {
    'Syscom': 117,
    'Grupo CVA': 101,
    'CT Internacional': 93,
    'TVC Mayorista': 618,
    'Tecnosinergia': 92,
    'Ingram Micro': 99,    # ajustar si distinto
    'Exel del Norte': 145, # ajustar
    'Anixter': 1161,
    'Absa': 100,
    'DEXTRA': 1166,
    'Adises': 1144,
    'Adistec': 1145,
}

def rpc(model, method, args, kwargs=None):
    """Llamada RPC simple Odoo (lee productos count)."""
    try:
        payload = {"jsonrpc":"2.0","method":"call","params":{"service":"object","method":"execute_kw",
            "args":["ocean-tech",2,"M1ercole$",model,method,args,kwargs or {}]}}
        r = subprocess.run(['/usr/bin/curl','-s','--max-time','30','-H','Content-Type: application/json',
            '-d',json.dumps(payload),ODOO], capture_output=True, text=True, timeout=35)
        return json.loads(r.stdout).get('result')
    except: return None

def count_odoo_by_partner(partner_id):
    """Cuenta product.template publicados con ese supplier."""
    try:
        sis = rpc('product.supplierinfo','search_count',[[['partner_id','=',partner_id]]]) or 0
        return sis
    except: return 0

def latest_log(prefix):
    """Último log con fecha en su nombre para un proveedor."""
    candidates = sorted(glob.glob(str(LOG_DIR / f'{prefix}_*.log')), key=os.path.getmtime)
    if not candidates: return None
    last = candidates[-1]
    mtime = os.path.getmtime(last)
    return {'file': os.path.basename(last), 'mtime': datetime.fromtimestamp(mtime)}

def read_progress(prefix):
    """Lee {prefix}_sync_progress.json si existe."""
    p = SCRIPT_DIR / f'{prefix}_sync_progress.json'
    if not p.exists(): return None
    try: return json.loads(p.read_text())
    except: return None

def is_pid_running(name_pattern):
    """Detecta si hay un proceso scraper corriendo."""
    try:
        r = subprocess.run(['pgrep','-f',name_pattern], capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except: return False

def detect_estado(prov_name):
    """Detecta estado real combinando logs + progress + procesos vivos + Odoo."""
    name_low = prov_name.lower().replace(' ','')
    today = datetime.now().date()

    # 1) Procesos vivos
    if 'anixter' in name_low and is_pid_running('anixter_v2.py'):
        return ('🔵 SCRAPER CORRIENDO', 'Playwright LATAM', '-')
    if 'absa' in name_low and is_pid_running('absa_scraper'):
        return ('🔵 SCRAPER CORRIENDO', 'Playwright login', '-')

    # 2) Logs recientes (últimas 36h)
    log_pref_map = {
        'CT Internacional': 'ct',
        'Ingram Micro': 'ingram',
        'Exel del Norte': 'exel',
        'Syscom': 'syscom',
        'Grupo CVA': 'cva',
        'Tecnosinergia': 'tecno',
        'TVC Mayorista': 'tvc',
    }
    pref = log_pref_map.get(prov_name)
    if pref:
        last = latest_log(pref)
        if last and (datetime.now() - last['mtime']) < timedelta(hours=36):
            ts = last['mtime'].strftime('%Y-%m-%d %H:%M')
            # Validar exit code via progress
            prog = read_progress(pref) or {}
            errs = prog.get('errors', 0)
            if errs > 0 and prog.get('processed',0) > 0:
                return ('🟡 SYNC CON ERRORES', f'cron 6 AM ({errs} err)', ts)
            return ('🟢 PRODUCCIÓN', 'cron 6 AM', ts)
        elif last:
            ts = last['mtime'].strftime('%Y-%m-%d')
            return ('🟡 LOG VIEJO', f'>{int((datetime.now()-last["mtime"]).total_seconds()/3600)}h', ts)

    return None

def load_provs():
    return json.loads(Path(DUMP).read_text())

def main(push_notion=False):
    provs = load_provs()
    estado_map = {}

    print(f"[{datetime.now()}] Detectando estado real de {len(provs)} proveedores...")

    for p in provs:
        name = p['nombre']
        det = detect_estado(name)
        prods = count_odoo_by_partner(PARTNER_IDS.get(name, 0)) if name in PARTNER_IDS else None
        estado_map[name] = {
            'det': det,
            'productos': prods,
            'url': p.get('url') or '-',
            'tipo': p.get('tipo') or '-',
        }

    # Agrupar
    counts = {'🟢':0,'🔵':0,'🟡':0,'⚪':0,'⚫':0}
    rows = []
    for p in provs:
        n = p['nombre']
        e = estado_map[n]
        det = e['det']
        if det:
            estado, mecanismo, ult = det
            icon = estado.split()[0]
        else:
            estado = '⚪ MANUAL'
            mecanismo = '-'
            ult = '-'
            icon = '⚪'
        counts[icon] = counts.get(icon,0) + 1

        prods = e['productos'] or 0
        rows.append({
            'nombre': n,
            'estado': estado,
            'icon': icon,
            'mecanismo': mecanismo,
            'productos': prods,
            'ultimo': ult,
            'url': e['url'],
            'tipo': e['tipo'],
        })

    # Override: marcas especiales que sé que no detecto via log
    overrides = {
        'Grupo Dice':       {'estado':'⚫ REGISTRO REQUERIDO', 'mecanismo':'e-catalogo restringido'},
        'Anixter':          {'estado':'🔵 SCRAPER CORRIENDO' if is_pid_running('anixter_v2') else '⚪ PENDIENTE LOGIN', 'mecanismo':'Playwright LATAM'},
        'Absa':             {'estado':'⚪ PENDIENTE LOGIN', 'mecanismo':'Odoo Shop B2B'},
        'PCH Connect':      {'estado':'⚪ MARCADO SCRAPER', 'mecanismo':'-'},
        'Exel Solar':       {'estado':'⚪ MARCADO SCRAPER', 'mecanismo':'-'},
    }
    for r in rows:
        if r['nombre'] in overrides:
            r['estado'] = overrides[r['nombre']]['estado']
            r['mecanismo'] = overrides[r['nombre']]['mecanismo']
            r['icon'] = r['estado'].split()[0]

    # Recontar después de overrides
    counts = {'🟢':0,'🔵':0,'🟡':0,'⚪':0,'⚫':0}
    for r in rows:
        counts[r['icon']] = counts.get(r['icon'],0) + 1

    order = {'🟢':1,'🔵':2,'🟡':3,'⚪':4,'⚫':5}
    rows.sort(key=lambda r: (order.get(r['icon'],9), r['nombre'].lower()))

    # === Generar Markdown ===
    md = [
        f"# 📊 Checklist Sync Proveedores Ocean Tech",
        f"",
        f"**Última actualización:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Auto-generado** por `update_checklist_proveedores.py` después del cron 6 AM",
        f"",
        f"## Resumen",
        f"",
        f"| Estado | Cantidad |",
        f"|---|---|",
        f"| 🟢 PRODUCCIÓN | {counts['🟢']} |",
        f"| 🔵 EN CURSO | {counts['🔵']} |",
        f"| 🟡 PARCIAL / ERROR | {counts['🟡']} |",
        f"| ⚪ PENDIENTE | {counts['⚪']} |",
        f"| ⚫ BLOQUEADO | {counts['⚫']} |",
        f"| **Total** | **{len(provs)}** |",
        f"",
        f"## Detalle",
        f"",
        f"| Proveedor | Estado | Mecanismo | Productos | Último Sync | URL |",
        f"|-----------|--------|-----------|-----------|-------------|-----|",
    ]
    for r in rows:
        prods_str = f"{r['productos']:,}" if r['productos'] else '0'
        md.append(f"| **{r['nombre']}** | {r['estado']} | {r['mecanismo']} | {prods_str} | {r['ultimo']} | {r['url'][:40]} |")

    md += [
        f"",
        f"## Convenciones",
        f"- 🟢 PRODUCCIÓN: corre solo en cron, log <36h, sin errores",
        f"- 🔵 EN CURSO: scraper activo en este momento (PID detectado)",
        f"- 🟡 PARCIAL: log viejo (>36h) o sync con errores",
        f"- ⚪ PENDIENTE: sin sync productivo, requiere acción",
        f"- ⚫ BLOQUEADO: registro/aprobación requerido",
        f"",
        f"---",
        f"_Fuente: {DUMP}_  ",
        f"_Datos: logs en `~/syscom-odoo-sync/logs/`, progress JSONs, conteos Odoo en vivo_",
    ]

    Path(OUT_MD).write_text('\n'.join(md))
    print(f"  ✓ {OUT_MD}")

    # === CSV ===
    with open(OUT_CSV,'w',newline='') as f:
        w = csv.writer(f)
        w.writerow(['Proveedor','Estado','Mecanismo','Productos','Último Sync','URL','Tipo'])
        for r in rows:
            w.writerow([r['nombre'], r['estado'], r['mecanismo'], r['productos'],
                        r['ultimo'], r['url'], r['tipo']])
    print(f"  ✓ {OUT_CSV}")

    # === Sync Obsidian Vault ===
    try:
        Path(OUT_VAULT).parent.mkdir(parents=True, exist_ok=True)
        # Frontmatter Obsidian
        vault_md = [
            "---",
            f"title: Checklist Sync Proveedores",
            f"date: {datetime.now().strftime('%Y-%m-%d')}",
            f"tags: [proveedores, sync, ocean-tech, dashboard]",
            f"updated: {datetime.now().isoformat()}",
            "---",
            "",
            f"> [!info] Dashboard auto-actualizado",
            f"> Última actualización: **{datetime.now().strftime('%Y-%m-%d %H:%M')}**  ",
            f"> Generado por `~/syscom-odoo-sync/scripts/update_checklist_proveedores.py`",
            f"",
        ] + md[3:]  # body sin el primer h1 ni metadata duplicada
        Path(OUT_VAULT).write_text('\n'.join(vault_md))
        print(f"  ✓ {OUT_VAULT}")
    except Exception as e:
        print(f"  ✗ Obsidian sync error: {e}")

    # === Notion (si --notion) ===
    if push_notion:
        try:
            push_to_notion(md, counts, rows)
        except Exception as e:
            print(f"  ✗ Notion push error: {e}")

    print(f"\n{'='*60}")
    print(f"  🟢 PRODUCCIÓN: {counts['🟢']:>2}    🔵 EN CURSO: {counts['🔵']:>2}    🟡 PARCIAL: {counts['🟡']:>2}")
    print(f"  ⚪ PENDIENTE: {counts['⚪']:>2}    ⚫ BLOQUEADO: {counts['⚫']:>2}    TOTAL: {len(provs)}")
    print(f"{'='*60}")

def push_to_notion(md_lines, counts, rows):
    """Sube/actualiza página Notion via MCP — requiere notion-mcp configurado.

    Esta función NO se ejecuta dentro del MCP de Notion (no podemos llamar tools desde script).
    Genera un .md candidato que Felipe puede pegar en Notion manualmente,
    o el orquestador Claude Code puede leer este archivo y pushearlo.
    """
    notion_md = '/tmp/checklist_proveedores_notion.md'
    Path(notion_md).write_text('\n'.join(md_lines))
    print(f"  ✓ Notion candidate: {notion_md} (push manual via Claude Code MCP)")

if __name__ == '__main__':
    main(push_notion='--notion' in sys.argv)
