# Contrato del Feed — Calculadora de Refacciones (Ocean Tech)

> **Para:** Joselinne (diseño/front) + Saúl (web). **Backend:** Felipe + Claude (desacoplado).
> El front consume SOLO estos endpoints. Nunca ve costo ni markup — solo `precio` de venta (IVA incl.).
> **Actualizado:** 2026-06-24 · Datos: 818 refacciones · 13,661 modelos de laptop · 33 marcas.

## Conexión
- **Base URL:** `https://supabase.ocean-tech.com.mx/rest/v1`
- **Headers (en TODAS las llamadas):**
  - `apikey: <ANON_KEY>`
  - `Authorization: Bearer <ANON_KEY>`
  - (la anon key pública la entrega Felipe; es de solo-lectura sobre estas vistas)
- Es PostgREST: filtros por querystring (`col=eq.valor`, `ilike.*texto*`, `order=`, `limit=`, `select=`).

## Flujo de la calculadora (3 pasos)

### Paso 1 — Tipo + Marca
La gente elige tipo de refacción (batería/cargador/pantalla…) y marca de su laptop.
```
GET /calculadora_marcas?select=marca,categoria,modelos&order=marca
```
`categoria`: `bat`=batería · `ac`=cargador · `lcd`=pantalla · `fan`=ventilador · `psc`=fuente.
Respuesta: `[{"marca":"HP","categoria":"bat","modelos":2288}, ...]`

### Paso 2 — Modelo de laptop (autocompletar)
```
GET /calculadora_modelos?marca=eq.HP&categoria=eq.bat&select=modelo_laptop&order=modelo_laptop
```
Respuesta: `[{"modelo_laptop":"LT-HPEliteBook840 G1"}, ...]` (ya viene sin duplicados).
Para buscador libre: `?modelo_laptop=ilike.*pavilion*`

### Paso 3 — Refacción compatible + PRECIO (lo que paga el cliente)
```
GET /calculadora_compatibles?modelo_laptop=eq.LT-HPEliteBook840%20G1&select=categoria,sku,nombre,precio,disponible,imagenes
```
Respuesta:
```json
[{"categoria":"bat","sku":"BT12078","nombre":"Para HP EliteBook 840 G1","precio":597,"disponible":true,
  "imagenes":["Img/Baterias/BT12078-01_Big.jpg"]}]
```
- `precio`: **precio de venta final** (MXN, IVA incluido, markup ya aplicado). El front lo muestra tal cual.
- `disponible`: boolean (stock en Nojomo).
- `imagenes`: rutas relativas; URL completa = `https://www.nojomo.info/<ruta>` *(si Nojomo bloquea hotlink, rehospedamos — avisar al backend)*.

### Detalle de un producto (specs + envío)
```
GET /calculadora_refacciones?sku=eq.BT12078&select=sku,nombre,precio,disponible,specs,imagenes,envio
```
`specs` (jsonb): voltaje, amperaje, watts, celdas, color… · `envio` (jsonb): opciones de paquetería con costo (se suma en el checkout).

## Reglas para el front
1. **Nunca** pedir ni mostrar costo/markup — esas columnas NO existen en la instancia pública.
2. Diseño **Ocean 2.0 "Abyssal Pulse" + logo original** (regla obligatoria de UI).
3. El `precio` ya es el de venta — no aplicarle ningún factor.
4. El **envío** se cobra aparte (viene en `envio`), no está incluido en `precio`.

## Pendiente backend (no bloquea el front)
- Refresco semanal de precios (re-scrape → recarga del feed).
- Checkout + pago (gateway) + colocación de pedido dropship en Nojomo (bot).
