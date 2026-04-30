# Ingram → Odoo Sync · Quickstart

## 🚀 Primer setup (5 min)

### Paso 1 — Capturar credenciales del browser

1. Abre Chrome en https://mx.ingrammicro.com (ya logueado)
2. Pulsa **F12** para abrir DevTools
3. Ve al tab **Console**
4. Pega este comando completo (una sola línea):

```js
var o=JSON.parse(localStorage['okta-token-storage']);copy(`INGRAM_REFRESH_TOKEN=${o.refreshToken.refreshToken}\nINGRAM_OAUTH_CLIENT_ID=${o.idToken.clientId}\nINGRAM_CUSTOMER_NUMBER=80697300`);console.log('Copiado al clipboard ✓');
```

5. Pulsa **Enter**. Ya está en tu clipboard.

### Paso 2 — Crear archivo `.env`

```bash
cd ~/syscom-odoo-sync
cp .env.example .env
```

Edita `.env` y pega lo que copiaste (Cmd+V) sustituyendo las 3 líneas vacías.

### Paso 3 — Instalar venv + dependencias (UNA sola vez)

```bash
cd ~/syscom-odoo-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Akamai (CDN de Ingram) bloquea Python sin TLS de Chrome. `curl_cffi` lo emula.

### Paso 4 — Test dry-run

```bash
cd ~/syscom-odoo-sync && source .venv/bin/activate
python scripts/ingram_to_odoo_sync.py --dry-run --limit 5
```

Esto:
- Refresca el access_token
- Obtiene TC USD/MXN del día
- Conecta a Odoo
- Lista 5 productos sin escribir nada

Si funciona, el output se ve así:
```
[12:34:01] Token OAuth refrescado (expira en 3600s)
[12:34:02] TC USD/MXN = 17.4964 (fuente: er-api)
[12:34:03] Conectado a Odoo UID=2
[12:34:04] Precargando SKUs de Odoo...
[12:34:08]   13280 SKUs cargados
[12:34:08] Pagina 1 (size=100, vendor=*)
[12:34:09]   100 items en pagina (total=10000, paginas=100)
[12:34:09]   [DRY] NEW B1102E3 VPN=BR1100M2-LM APC cost=$251.79USD=4404.20MXN online=5731.78 stock=752
...
```

### Paso 5 — Sync real (sin --dry-run)

```bash
source .venv/bin/activate  # cada vez
python scripts/ingram_to_odoo_sync.py --limit 50    # 50 primero
python scripts/ingram_to_odoo_sync.py --vendor APC  # solo APC
python scripts/ingram_to_odoo_sync.py               # full sync
```

## 🔁 Refresh token

El script rota el refresh_token cada hora automáticamente (cache en `scripts/.ingram_token_cache.json`). Si pasa una semana sin correr o cierras sesión en el browser, el refresh expira — repite **Paso 1** para reintegrar.

## 📊 Comandos útiles

```bash
# Estado de la última corrida
python3 scripts/ingram_to_odoo_sync.py --status

# Solo rotar token (test de auth)
python3 scripts/ingram_to_odoo_sync.py --refresh-token

# Solo un vendor específico
python3 scripts/ingram_to_odoo_sync.py --vendor "Cisco" --limit 100
```

## 🗂️ Archivos generados

```
syscom-odoo-sync/
├── scripts/
│   ├── ingram_to_odoo_sync.py
│   ├── .ingram_token_cache.json   ← tokens (gitignore)
│   ├── .tc_cache.json             ← TC del día (gitignore)
│   └── ingram_sync_progress.json  ← progreso último run
└── logs/
    └── ingram_sync_YYYYMMDD_HHMM.log
```

## ⚠️ Notas

- **Precios USD → MXN**: TC del día desde Banxico (con token) o open.er-api (sin auth)
- **Stock**: incluido en la respuesta API gracias a `EnablePNA: true` (suma todos los warehouses)
- **Cap Ingram**: 10,000 productos por keyword. Para más, iterar por vendor (`--vendor X`)
- **Match Odoo**: 1) por `default_code = VPN` (cruza con Syscom/CVA existentes), 2) por SKU Ingram para nuevos
- **Margenes**: ver `MARGINS` y `INGRAM_CATEGORY_MARGINS` en el script
