#!/usr/bin/env python3
"""
COORDINADOR INTERACTIVO — Claude Agent
Habla con Claude en tiempo real con contexto del sistema Syscom→Odoo
"""
import urllib.request, json, os, sys, subprocess, tempfile
try: import readline
except: pass

N8N_KEY = "<<N8N_API_KEY>>"
CATS = ["Videovigilancia","Redes","Radiocomunicación","Automatización e Intrusión",
        "Cableado Estructurado","Control de Acceso","Energía","Detección de Incendio",
        "Sonido y Video","Herramientas"]

conversation_history = []

def get_system_context():
    try:
        req = urllib.request.Request(
            f"https://n8n.ocean-tech.com.mx/api/v1/workflows/ylxPHHe9ymC49FTO",
            headers={"X-N8N-API-KEY": N8N_KEY})
        with urllib.request.urlopen(req, timeout=8) as r:
            w = json.loads(r.read())
        g = w.get('staticData', {}).get('global', {})
        cat = int(g.get('categoryIndex', 0))

        req2 = urllib.request.Request(
            "https://n8n.ocean-tech.com.mx/api/v1/executions?workflowId=ylxPHHe9ymC49FTO&limit=5",
            headers={"X-N8N-API-KEY": N8N_KEY})
        with urllib.request.urlopen(req2, timeout=8) as r2:
            execs = json.loads(r2.read()).get('data', [])

        p = {"jsonrpc":"2.0","method":"call","id":1,"params":{"service":"object","method":"execute_kw",
            "args":["ocean-tech-0326",2,"<<ODOO_PASSWORD>>","product.template","search_count",[[["active","=",True]]]]}}
        req3 = urllib.request.Request("https://ocean-tech-0326.odoo.com/jsonrpc",
            data=json.dumps(p).encode(), headers={"Content-Type":"application/json"}, method='POST')
        with urllib.request.urlopen(req3, timeout=12) as r3:
            total = json.loads(r3.read()).get('result', 0)

        exec_summary = []
        for e in execs[:5]:
            exec_summary.append(f"#{e.get('id')} [{e.get('status')}] {(e.get('startedAt') or '')[:16]}")

        errors_recent = sum(1 for e in execs[:5] if e.get('status') != 'success')

        return {
            "categoria_actual": CATS[cat] if cat < len(CATS) else "?",
            "categoria_index": cat,
            "pagina_actual": int(g.get('page', 1)),
            "total_productos_odoo": total,
            "ultimas_5_ejecuciones": exec_summary,
            "errores_en_ultimas_5": errors_recent,
            "workflow_activo": w.get('active', False),
            "n8n_url": "https://n8n.ocean-tech.com.mx",
            "odoo_url": "https://ocean-tech-0326.odoo.com"
        }
    except Exception as e:
        return {"error": str(e)}

def ask_claude(user_msg, context):
    """Llama a Claude via CLI (ya tiene auth integrada)"""
    system_prompt = f"""Eres el Agente Coordinador de un sistema de sincronización de catálogo Syscom→Odoo para una empresa de seguridad electrónica en Jalisco, México.

ESTADO ACTUAL DEL SISTEMA:
{json.dumps(context, indent=2, ensure_ascii=False)}

CATEGORÍAS: {', '.join(CATS)}

TU ROL:
- Diagnosticar errores de los agentes (n8n workflows)
- Sugerir correcciones concretas
- Coordinar los agentes: Sync Monitor, Validador, Ejecutor
- Responder sobre el progreso de la sincronización
- Proponer nuevos agentes o automatizaciones

CONTEXTO TÉCNICO:
- n8n: {context.get('n8n_url','n8n')} | Workflow: ylxPHHe9ymC49FTO
- Odoo: {context.get('odoo_url','odoo')}
- Webhook trigger: /webhook/syscom-trigger-run
- Problema actual: rate limit de Syscom por exceso de requests

Responde en español, conciso y accionable."""

    # Historial como texto para el prompt
    hist_text = ""
    for msg in conversation_history[-10:]:
        role = "Felipe" if msg["role"] == "user" else "Coordinador"
        hist_text += f"{role}: {msg['content']}\n"

    full_prompt = f"{hist_text}Felipe: {user_msg}\nCoordinador:"

    try:
        result = subprocess.run(
            ["claude", "-p", full_prompt, "--system-prompt", system_prompt],
            capture_output=True, text=True, timeout=45
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        elif result.stderr:
            return f"❌ {result.stderr.strip()[:200]}"
        return "❌ Sin respuesta del CLI"
    except FileNotFoundError:
        return "❌ 'claude' CLI no encontrado en PATH"
    except subprocess.TimeoutExpired:
        return "❌ Timeout esperando respuesta"
    except Exception as e:
        return f"❌ Error: {e}"

def show_status(ctx):
    print(f"\n  📂 Categoría:  [{ctx.get('categoria_index')}] {ctx.get('categoria_actual')}")
    print(f"  📄 Página:     {ctx.get('pagina_actual')}")
    print(f"  📦 Productos:  {ctx.get('total_productos_odoo', 0):,}")
    print(f"  ⚠️  Errores/5: {ctx.get('errores_en_ultimas_5', 0)}")
    print(f"  🔄 Ejecuciones:")
    for e in ctx.get('ultimas_5_ejecuciones', []):
        icon = '✅' if 'success' in e else '❌'
        print(f"     {icon} {e}")
    print()

# ─── MAIN ───────────────────────────────────────────────
print("\033[1;35m")
print("╔══════════════════════════════════════════════════════════╗")
print("║     🤖  COORDINADOR INTERACTIVO — Claude Sonnet 4.6      ║")
print("║     Escribe tu mensaje y presiona Enter                  ║")
print("║     Comandos: /estado  /productos  /limpiar  /salir      ║")
print("╚══════════════════════════════════════════════════════════╝")
print("\033[0m")

print("📡 Cargando contexto del sistema...")
ctx = get_system_context()
show_status(ctx)

while True:
    try:
        user_input = input("\033[1;33mFelipe → Coordinador:\033[0m ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n👋 Coordinador desconectado.")
        break

    if not user_input:
        continue

    if user_input in ('/salir', '/exit', '/quit'):
        print("👋 Coordinador desconectado.")
        break

    elif user_input == '/estado':
        ctx = get_system_context()
        show_status(ctx)
        continue

    elif user_input == '/productos':
        try:
            p = {"jsonrpc":"2.0","method":"call","id":1,"params":{"service":"object","method":"execute_kw",
                "args":["ocean-tech-0326",2,"<<ODOO_PASSWORD>>","product.template","search_count",[[["active","=",True]]]]}}
            req = urllib.request.Request("https://ocean-tech-0326.odoo.com/jsonrpc",
                data=json.dumps(p).encode(), headers={"Content-Type":"application/json"}, method='POST')
            with urllib.request.urlopen(req, timeout=15) as r:
                count = json.loads(r.read()).get('result', 0)
            print(f"\n  📦 Productos activos en Odoo: {count:,}\n")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        continue

    elif user_input == '/limpiar':
        conversation_history.clear()
        print("  🗑️  Historial limpiado.\n")
        continue

    # Refrescar contexto cada 3 mensajes
    if len(conversation_history) % 6 == 0:
        ctx = get_system_context()

    print("\n\033[1;36m🤖 Coordinador:\033[0m")
    response = ask_claude(user_input, ctx)
    print(f"{response}\n")

    # Mantener historial para conversación continua
    conversation_history.append({"role": "user", "content": user_input})
    conversation_history.append({"role": "assistant", "content": response})

    # Limitar historial a últimos 20 mensajes (10 intercambios)
    if len(conversation_history) > 20:
        conversation_history[:] = conversation_history[-20:]
