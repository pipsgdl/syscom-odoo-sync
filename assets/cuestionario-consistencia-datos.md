# Cuestionario: Reglas de Consistencia de Datos — Ocean Tech
**Fecha**: 2026-04-08  
**Propósito**: Definir las reglas maestras para que todos los proveedores (Syscom, CVA, TVC, manuales) alimenten Odoo con datos consistentes y el ecommerce funcione correctamente.

---

## Diagnóstico Actual (58,215 productos en Odoo)

| Campo | Con dato | Sin dato | % completo |
|-------|----------|----------|------------|
| SKU (default_code) | 57,833 | 382 | 99.3% |
| Barcode | 37,151 | 21,064 | 63.8% |
| Imagen principal | 37,297 | 20,918 | 64.1% |
| Precio venta > 0 | 57,871 | 344 | 99.4% |
| Costo > 0 | 57,775 | 440 | 99.2% |
| Categoría asignada | 57,818 | 397 | 99.3% |
| Descripción de venta | 55,758 | 2,457 | 95.8% |
| Código SAT (UNSPSC) | 39,030 | 19,185 | 67.0% |
| Peso | 0 | 58,215 | 0% |
| Es almacenable | 38,306 | 19,909 | 65.8% |
| Publicado en tienda | 1,954 | 56,261 | 3.4% |
| Proveedor (supplierinfo) | 0 | 58,215 | 0% |
| Imágenes extra | 232 | — | — |

### Hallazgos Críticos
1. **382 productos sin SKU** — son manuales (servicios, viáticos, rentas, "SERV", "anticipo")
2. **21,064 sin barcode** — mayoría son productos manuales en categoría "COMPONENTES DE COMPUTO" (categoría cajón)
3. **19,185 sin código SAT** — riesgo fiscal en facturación
4. **0 productos con peso** — afecta cotización de envío
5. **0 registros en product.supplierinfo** — no se sabe qué proveedor surte qué
6. **Categoría "COMPONENTES DE COMPUTO"** se usó como cajón de sastre para ~382 productos manuales

---

## SECCIÓN 1: Identidad del Producto (SKU / Barcode)

### P1. ¿Cuál es la fuente maestra del SKU?
El SKU (`default_code`) identifica al producto en todo el sistema. Hoy tenemos conflicto porque:
- Syscom usa `modelo` (ej: DS-2CD2347G2P-LSU/SL)
- CVA usa `codigo_fabricante` (similar pero no siempre idéntico) o `clave` interna
- TVC usa su propio código

**Opciones:**
- **A)** El SKU maestro es el `codigo_fabricante` del proveedor (el del fabricante: Hikvision, ZKTeco, etc.)
- **B)** El SKU maestro es el código del primer proveedor que lo dio de alta
- **C)** Creamos un SKU Ocean Tech propio (ej: OT-00001) y los códigos de proveedor van en campos separados

> **Tu respuesta**: ___

### P2. ¿Qué hacemos con el barcode?
Hoy barcode = default_code para productos de Syscom. Pero 21,064 productos no tienen barcode.

**Opciones:**
- **A)** Barcode = mismo que default_code siempre (como está ahora)
- **B)** Barcode = código EAN/UPC real del fabricante (si existe)
- **C)** Barcode queda vacío si no hay código EAN real

> **Tu respuesta**: ___

### P3. ¿Qué hacemos con los 382 productos sin SKU?
Son servicios, mano de obra, viáticos, rentas, etc. creados manualmente.

**Opciones:**
- **A)** Les asignamos SKU con prefijo SERV- (ej: SERV-INST-01, SERV-MO-01)
- **B)** Los movemos a una categoría "Servicios" separada y no los tocamos
- **C)** Los archivamos — no deberían estar mezclados con productos

> **Tu respuesta**: ___

---

## SECCIÓN 2: Imágenes

### P4. ¿Cuántas imágenes mínimas necesita un producto para publicarse?
Hoy la regla WF-REGLA-4FOTOS auto-publica/despublica.

**Opciones:**
- **A)** 1 imagen mínima (portada) para publicar
- **B)** 3 imágenes mínimas (portada + 2 extras)
- **C)** 4 imágenes mínimas (la regla actual)
- **D)** Depende de la categoría (cámaras=4, cables=1, servicios=0)

> **Tu respuesta**: ___

### P5. ¿Qué imagen usamos cuando el proveedor no tiene foto?
20,918 productos no tienen imagen. Muchos son de proveedores que no dan foto o son productos manuales.

**Opciones:**
- **A)** Imagen genérica por categoría (ej: foto genérica de "cable", "cámara", etc.)
- **B)** Se queda sin imagen y no se publica hasta tener foto real
- **C)** Buscar imagen del fabricante en su sitio web (Hikvision.com, ZKTeco.com, etc.)
- **D)** Combinación: buscar en fabricante, si no hay → genérica por categoría

> **Tu respuesta**: ___

### P6. ¿Quieres que el sync baje el logo de la marca también?
Syscom da `marca_logo` (URL del logo de Hikvision, Dahua, etc.). ¿Lo guardamos en Odoo?

**Opciones:**
- **A)** Sí, como campo personalizado o en la ficha de marca
- **B)** No es prioridad ahora

> **Tu respuesta**: ___

---

## SECCIÓN 3: Precios y Listas

### P7. ¿Cuántas listas de precios manejamos?
Hoy existen 5 en Odoo:
1. Public Pricelist (0 reglas)
2. Dolares (1 regla)
3. Online / Ecommerce (106 reglas)
4. Menudeo / Mostrador (106 reglas)
5. Proyecto / Cotización (106 reglas)

**Pregunta:** ¿Las 3 listas (Online/Menudeo/Proyecto) son las definitivas, o necesitamos más?

**Opciones:**
- **A)** Son las 3 definitivas
- **B)** Necesitamos agregar: Mayoreo (para instaladores frecuentes)
- **C)** Necesitamos agregar: Gobierno (para licitaciones con precio especial)
- **D)** Otra: ___

> **Tu respuesta**: ___

### P8. ¿Los márgenes son por categoría o por proveedor?
Hoy los márgenes están definidos por categoría (Videovig 5%, Redes 5%, Incendio 15% en Online). Pero el costo varía por proveedor (Syscom es más caro que CVA en ciertos productos).

**Opciones:**
- **A)** Margen por categoría (como está ahora) — el costo del proveedor más barato gana
- **B)** Margen por proveedor+categoría (ej: Syscom Videovig 5%, CVA Videovig 8%)
- **C)** Margen fijo global por lista (ej: Online siempre 10%, Menudeo siempre 20%)

> **Tu respuesta**: ___

### P9. ¿Cuál es la regla cuando un producto existe en 2+ proveedores?
Ej: DS-2CD2347G2P está en Syscom Y CVA. ¿Cuál precio gana?

**Opciones:**
- **A)** Siempre el costo más bajo (el sync compara y usa el más barato)
- **B)** Proveedor preferido: Syscom primero, CVA si no está en Syscom
- **C)** Depende de disponibilidad (stock > 0 gana, si ambos tienen stock → el más barato)

> **Tu respuesta**: ___

### P10. ¿El `list_price` en Odoo debe incluir IVA o no?
Hoy está corregido a SIN IVA (Odoo agrega IVA al facturar). Pero en ecommerce los clientes quieren ver precio CON IVA.

**Opciones:**
- **A)** `list_price` SIN IVA (Odoo muestra +IVA en tienda automáticamente) ← actual
- **B)** `list_price` CON IVA (hay que configurar Odoo para que no sume IVA de nuevo)

> **Tu respuesta**: ___

---

## SECCIÓN 4: Categorías

### P11. ¿Usamos las categorías de Odoo o las de Syscom?
Hoy hay un mapeo manual Syscom → Odoo (12 categorías principales). Pero CVA y TVC tienen sus propias categorías.

**Opciones:**
- **A)** Categorías propias de Ocean Tech (las que ya existen en Odoo) — todos los proveedores se mapean a ellas
- **B)** Crear subcategorías más granulares (ej: CCTV > Cámaras IP > Domo / Bullet / PTZ)
- **C)** Usar las categorías del ecommerce de Odoo (que pueden ser diferentes a las internas)

> **Tu respuesta**: ___

### P12. ¿Qué hacemos con "COMPONENTES DE COMPUTO"?
Es la categoría 7 y tiene ~382 productos manuales metidos ahí como cajón de sastre.

**Opciones:**
- **A)** Limpiar y reclasificar cada producto a su categoría correcta
- **B)** Renombrar a "Otros / Sin clasificar" y dejarlos ahí
- **C)** Archivar los que son basura (duplicados, pruebas, "!!!!!!")

> **Tu respuesta**: ___

---

## SECCIÓN 5: Datos Obligatorios para Ecommerce

### P13. ¿Cuáles campos deben ser OBLIGATORIOS para publicar un producto?
Marca con ✓ los que consideras obligatorios:

| Campo | ¿Obligatorio? |
|-------|---------------|
| SKU (default_code) | __ |
| Nombre limpio (no código) | __ |
| Imagen principal | __ |
| Al menos 1 imagen extra | __ |
| Precio de venta > 0 | __ |
| Costo > 0 | __ |
| Categoría (no "Sin clasificar") | __ |
| Descripción de venta | __ |
| Código SAT | __ |
| Marca | __ |
| Peso | __ |
| Stock > 0 | __ |
| Unidad de medida | __ |

> **Tu respuesta**: ___

### P14. ¿Queremos publicar productos sin stock?
Hoy 1,954 están publicados. Pero ¿qué pasa si un producto se queda sin stock?

**Opciones:**
- **A)** Despublicar automáticamente cuando stock = 0
- **B)** Mantener publicado pero mostrar "Agotado" / "Sobre pedido"
- **C)** Mantener publicado, el cliente puede comprar y lo pedimos al proveedor (dropship)

> **Tu respuesta**: ___

---

## SECCIÓN 6: Proveedores y Trazabilidad

### P15. ¿Debemos registrar qué proveedor surte cada producto?
Hoy `product.supplierinfo` tiene 0 registros. Nadie sabe si un producto viene de Syscom, CVA, TVC o es manual.

**Opciones:**
- **A)** Sí, obligatorio — cada sync debe registrar el proveedor en supplierinfo
- **B)** No es prioridad, con el SKU sabemos de dónde viene
- **C)** Sí, y además guardar el precio de cada proveedor para comparar

> **Tu respuesta**: ___

### P16. ¿Cuáles son los proveedores actuales y futuros?

| Proveedor | Estado | Productos | API/Integración |
|-----------|--------|-----------|-----------------|
| Syscom | Activo | ~54,391 | API REST ✓ |
| CVA | Activo | ~9,972 | API REST ✓ |
| TVC | Activo | ? | WF-TVC activo |
| Manual (mostrador) | Activo | ~382 | Manual |
| ¿Otro? | | | |

> **¿Hay más proveedores planeados?**: ___

---

## SECCIÓN 7: Actualización y Frecuencia

### P17. ¿Con qué frecuencia se actualizan precios?
**Opciones:**
- **A)** Tiempo real (cada cambio en proveedor → actualizar Odoo inmediatamente)
- **B)** Cada hora (WF1 Diff Precios ya corre cada 1h)
- **C)** Diario (una vez al día es suficiente)
- **D)** Diferente por proveedor: Syscom cada 1h, CVA diario, TVC semanal

> **Tu respuesta**: ___

### P18. ¿Con qué frecuencia se actualiza stock?
**Opciones:**
- **A)** Mismo que precios
- **B)** Más frecuente que precios (cada 30 min para stock, cada 1h para precios)
- **C)** Depende del proveedor

> **Tu respuesta**: ___

---

## SECCIÓN 8: Limpieza Pendiente

### P19. ¿Qué hacemos con los duplicados?
Hay productos con el mismo SKU en varios registros (ej: RSA-3050-B aparece 2 veces, CH150150BM aparece 2 veces).

**Opciones:**
- **A)** Fusionar automáticamente (quedarse con el que tiene más datos)
- **B)** Revisión manual antes de fusionar
- **C)** Archivar el duplicado que tenga menos datos

> **Tu respuesta**: ___

### P20. ¿Cuál es la prioridad de limpieza?
Ordena del 1 (más urgente) al 5 (menos urgente):

| Tarea | Prioridad |
|-------|-----------|
| Completar imágenes (20,918 sin foto) | __ |
| Completar código SAT (19,185 sin código) | __ |
| Limpiar categoría "COMPONENTES DE COMPUTO" | __ |
| Registrar proveedores en supplierinfo | __ |
| Agregar peso a todos los productos | __ |

> **Tu respuesta**: ___

---

## Resumen de Decisiones Necesarias

| # | Decisión | Impacto |
|---|----------|---------|
| P1 | Fuente maestra de SKU | Define cómo cruzan los proveedores |
| P2 | Uso de barcode | Lectura de código de barras en almacén |
| P4 | Imágenes mínimas para publicar | Cuántos productos aparecen en tienda |
| P7 | Listas de precios definitivas | Estructura comercial completa |
| P9 | Regla multi-proveedor | Quién gana cuando hay 2+ proveedores |
| P13 | Campos obligatorios ecommerce | Calidad de la tienda online |
| P14 | Productos sin stock | Experiencia del cliente |
| P15 | Trazabilidad de proveedor | Saber a quién pedir cada producto |
| P20 | Prioridad de limpieza | En qué nos enfocamos primero |

**Con tus respuestas, construyo las reglas de validación que se aplicarán automáticamente en todos los syncs.**
