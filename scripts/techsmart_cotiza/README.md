# Techsmart — cómo cotizar (guía para futuras cotizaciones)

> Techsmart (`techsmart.com.mx/techsmartv2`) = mayorista de cómputo/hardware. Cuenta Ocean: `GDL...` (ver `.env`).
> Credenciales: `/Volumes/HIKSEMI 512/Claude code/LICITABOT/.env` → `TECHSMART_RFC/USER/PASS/BASE`.

## ⚠️ LO MÁS IMPORTANTE — bug de moneda (corregido 2026-07-06)

**Los precios del catálogo vienen en USD o en MXN según la categoría/marca — NUNCA asumas una sola moneda.**

Ejemplo real: un procesador AMD se muestra como `$183.19 USD`, un monitor XZEAL como `$1,664.43 MXN` — **en la misma sesión, en la misma cuenta**. Si tu script solo busca `\$([\d,]+\.\d{2})\s*MXN` (el bug original), **toda la categoría en USD se lee como $0.00** y parece "sin precio asignado" cuando SÍ tiene precio real. Esto pasó meses en el conector de producción antes de detectarse.

**Regla:** el regex de precio debe capturar `USD` o `MXN`, y todo precio en USD se debe convertir a MXN usando el **tipo de cambio que el propio sitio muestra** (navbar de cualquier página logueada: `Tipo de cambio: $17.51`, `id="tipo_cambioo"`) — nunca un tipo de cambio fijo/hardcodeado.

Categorías que en la práctica salen en **USD** (verificado): PROCESADORES, MOTHERBOARDS, TARJETAS DE VIDEO, ALMACENAMIENTO, MEMORIAS RAM Y FLASH.
Categorías que en la práctica salen en **MXN**: ENFRIAMIENTO, MONITORES, TECLADOS.
(No es una regla fija de Techsmart — puede cambiar; el parser debe seguir aceptando ambas siempre.)

## Cómo consultar (mecánica del portal)

1. **Login:** `POST {BASE}/acciones/login.php` con `rfc, usuario, txtPass, movil=''` → JSON `{"error":"no","msg":"Acceso correcto"}` + cookie `PHPSESSID`.
2. **Catálogo con precio (server-side, síncrono):**
   `GET {BASE}/Clientes/Catalogo?txtCategoria=<CAT>&txtMarca=<MARCA>`
   — **exige ambos parámetros**. Usa `T` como valor para "TODAS" (funciona tanto en categoría como en marca — `txtMarca=T` trae todas las marcas de esa categoría).
3. Cada tarjeta de producto ancla en `cveProducto=<CODIGO>&TipoMoneda=<USD|MXN>&Marca=<MARCA>`, seguida de `MODELO:`, descripción, y dos precios (lista tachado + con descuento). El **precio con descuento es el costo Ocean**.
4. El **tipo de cambio** vive en el navbar de CUALQUIER página logueada: `Tipo de cambio: $X.XX`.

## Categorías (31) y marcas (45) — snapshot 2026-07-06
**Categorías:** ACCESORIOS, ADAPTADORES, ALMACENAMIENTO, AUDIFONOS, BATERIAS, BOCINAS, CABLEADO ESTRUCTURADO, CABLES, ENFRIAMIENTO, FUENTES DE PODER, GABINETES, LECTORES DE MEMORIAS, LIMPIADORES, MEMORIAS RAM Y FLASH, MICROFONOS, MINI PCS Y PORTATILES, MONITORES, MOTHERBOARDS, MOUSE PADS, MOUSES, NO BREAKS, PROCESADORES, PUNTO DE VENTA, REDES, REGULADORES DE VOLTAJE, SERVIDORES NAS, SILLAS, SIMULADORES, SMARTWATCHS, TARJETAS DE VIDEO, TECLADOS.

**Marcas:** ACER, ACTECK, AMD, ANTEC, ASUS, ASUSTOR, BALAM RUSH, BENQ, BIOSTAR, COOLER MASTER, CORSAIR, CRUCIAL, DAHUA, ECS, ELGATO, EVOTEC, GAMDIAS, GIGABYTE, HYTE, INTEL, JHETA, K-MEX, KINGSTON, MSI, NACEB, NITROTEL, NZXT, PATRIOT, PIXXO, PNY, POINT OF VIEW, PREDATOR, SANDISK, SEAGATE, STYLOS, TOSHIBA, TP-LINK, TRUST, VERICO, VERTAGEAR, WD, XFX, XZEAL, YAGUARET, ZOTAC.

**No están en el catálogo de marcas** (pídelos a otro distribuidor): ASRock, Thermalright, Lenovo, Dell, HP, Logitech, Razer.

## Gaps REALES del catálogo (confirmados, no son el bug de moneda)
- **RAM DDR5: no existe ninguna** en el catálogo — solo DDR4 (DIMM/SODIMM). Cualquier build AM5/Intel-DDR5 necesita RAM de otro proveedor.
- **Teclados mecánicos: no hay** — solo membrana (ACTECK/STYLOS/XZEAL/BALAM RUSH, kits oficina/gamer básicos).
- **ASRock, MSI (motherboards/GPU), Thermalright, Cooler Master/NZXT/HYTE (enfriamiento):** estas marcas están en el selector pero sin catálogo/precio para nuestra cuenta (`0 productos` al consultar). Puede cambiar — vale la pena reintentar cada tanto.

## Marcas/categorías CON precio real confirmado (útiles para armar cotizaciones)
- **PROCESADORES → AMD**: línea completa Ryzen 5000/7000/8000/9000 (AM4 y AM5).
- **MOTHERBOARDS → ASUS**: PRIME/ROG STRIX serie B650/B850 (AM5).
- **TARJETAS DE VIDEO → ZOTAC**: RTX 5060 Ti Twin Edge OC 8GB (y revisar otras según necesidad).
- **ALMACENAMIENTO → WD, CRUCIAL**: SSD NVMe M.2 (SN3000, SN350, CT E100…) y disco duro (Purple/PurZ).
- **ENFRIAMIENTO → BALAM RUSH, XZEAL**: AIO líquido 120/240/360mm ARGB, algunos **con display LCD** (ej. `BR-944069 Cryo Prism 360`).
- **MONITORES → XZEAL, BALAM RUSH, STYLOS, ACTECK**: 19"-27", FHD, hasta 180Hz/240Hz, curvos y planos.
- **TECLADOS → ACTECK, STYLOS**: membrana, oficina/gamer básico.

## Herramientas
- **Cotización puntual (rápida, no toca Odoo):** `scripts/techsmart_cotiza/techsmart_buscar.py "<CATEGORIA>" "<MARCA o T>" [--contiene texto] [--min N] [--max N]`
  Ej: `techsmart_buscar.py "MONITORES" "T" --contiene "27" --max 2000`
- **Sync completo a Odoo (producción):** `scripts/techsmart_to_odoo_sync.py --dry-run` (valida) / `--diff` (escribe supplierinfo). Ya trae el fix de moneda.

## Historial
- 2026-06-11: alta como 8º distribuidor + mecánica del portal descubierta.
- 2026-06-11: corregido el error de "requiere categoría+marca" (antes se creía sin precios).
- **2026-07-06: corregido el bug de moneda** (USD leído como $0) — cobertura real de precios subió significativamente en las categorías de mayor valor (CPU/GPU/motherboard/RAM/almacenamiento). Ver cifras exactas en la memoria del proyecto.
