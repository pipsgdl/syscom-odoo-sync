# Reglas de Consistencia de Datos — Ocean Tech
**Fecha**: 2026-04-08 | **Aprobado por**: Felipe de Alba  
**Aplica a**: Todos los syncs (Syscom, CVA, TVC) y carga manual

---

## Decisiones Tomadas — Cuestionario Completo (2026-04-08)

| # | Pregunta | Decisión |
|---|----------|---------|
| P1 | SKU | Viene de cada proveedor (codigo_fabricante del fabricante) |
| P2 | Barcode | = SKU siempre |
| P3 | Sin SKU (382 servicios) | Prefijo SERV-, mover a categoría "Servicios" |
| P4 | Fotos mínimas para publicar | Por categoría: Cámaras/DVR/Acceso/Incendio/Audio=3, Redes/Energía/Accesorios=2, Cable=1, Servicios=imagen genérica |
| P5 | Sin foto | Buscar en sitio fabricante → imagen genérica por categoría |
| P6 | Logo de marca | Guardar en Odoo |
| P7 | Listas de precios | 5: Online, Menudeo, Proyecto, Mayoreo, Gobierno |
| P8 | Márgenes | Por categoría sobre costo más bajo entre proveedores |
| P9 | Multi-proveedor | Comparativo, el más barato gana como standard_price |
| P10 | IVA en tienda | Precios CON IVA incluido (list_price = costo/(1-margen)*1.16) |
| P11 | Categorías | Subcategorías granulares para ecommerce |
| P12 | Categoría COMPUTO | Nueva categoría + subcategorías, limpiar cajón de sastre |
| P13 | Descripción | Bullet points estilo Amazon (características técnicas) |
| P14 | Sin stock | Despublicar automáticamente |
| P15 | Proveedor | Registrar en product.supplierinfo |
| P16 | Próximos proveedores | CT → PCH → Exel Norte → Tecnosinergia → Ingram (a $200K USD/año) |
| P17 | Frecuencia precios | 1 vez al día (nocturno) |
| P18 | Frecuencia stock | Cada 30 minutos |
| P19 | Duplicados | Fusión automática: más datos + precio más bajo gana |
| P20 | Prioridad limpieza | 1-Duplicados → 2-Imágenes → 3-SAT → 4-Categorías → 5-Supplierinfo |

---

## REGLA 1: Campos Obligatorios para Publicar

Un producto se publica **solo si cumple TODOS**:

| Campo Odoo | Regla | Validación |
|------------|-------|------------|
| `image_1920` | != False | Tiene imagen principal |
| `default_code` | != False y != "" | Tiene SKU/modelo |
| `description_sale` | != False y len > 10 | Tiene descripción |
| `list_price` | > 0 | Tiene precio de venta |
| `qty_available` | > 0 | Tiene stock |

**Resultado**: `is_published = True` solo si cumple los 5.  
**Hoy**: 17,124 productos cumplen todo (vs 1,954 publicados actualmente)

### Auto-despublicar
- Si `qty_available` baja a 0 → `is_published = False`
- Si `image_1920` se borra → `is_published = False`

---

## REGLA 2: Identidad del Producto (SKU)

```
default_code = codigo_fabricante del proveedor
barcode      = mismo que default_code (para scanner)
```

### Cruce entre proveedores
Cuando llega un producto de CVA/TVC cuyo `codigo_fabricante` ya existe en Odoo:
1. **NO crear duplicado** → actualizar el existente
2. Agregar el proveedor en `product.supplierinfo`
3. Si el costo es menor → actualizar `standard_price`

### Productos manuales/servicios (382 sin SKU)
- Asignar SKU con prefijo: `SERV-xxx`, `MO-xxx`, `VIAT-xxx`
- Categoría: mover a "Servicios" (no mezclar con productos)
- `is_storable = False`
- NUNCA publicar en ecommerce

---

## REGLA 3: Registro de Proveedor (product.supplierinfo)

Cada sync DEBE registrar:

```python
{
    "partner_id": ID_PROVEEDOR,      # 117=SYSCOM, 101=Grupo CVA, 618=TVCENLINEA
    "product_tmpl_id": product_id,
    "price": costo_proveedor,         # Precio de compra del proveedor
    "currency_id": 33,                # MXN
    "min_qty": 1,
    "delay": 3,                       # Días de entrega estimados
    "product_code": sku_proveedor,    # SKU del proveedor (puede diferir)
}
```

### IDs de proveedores en Odoo
| Proveedor | partner_id | Productos |
|-----------|-----------|-----------|
| SYSCOM | 117 | ~54,391 |
| Grupo CVA | 101 | ~9,972 |
| TVCENLINEA | 618 | Por confirmar |

---

## REGLA 4: Precios Multi-Proveedor

### Flujo cuando producto existe en 2+ proveedores:

```
Producto X existe en Syscom ($500) y CVA ($450)

1. product.supplierinfo tiene 2 registros:
   - Syscom: $500 MXN
   - CVA: $450 MXN

2. standard_price = MIN($500, $450) = $450 (el más barato)

3. list_price se calcula con el costo más bajo:
   - Online:   $450 / (1 - 0.05) = $473.68
   - Menudeo:  $450 / (1 - 0.18) = $548.78
   - Proyecto: $450 / (1 - 0.30) = $642.86
```

### Tabla de márgenes por categoría+lista

| Categoría | Online | Menudeo | Proyecto |
|-----------|--------|---------|----------|
| Videovigilancia | 5% | 18% | 30% |
| Redes e IT | 5% | 12% | 22% |
| Detección Incendio | 15% | 30% | 45% |
| Control de Acceso | 10% | 20% | 35% |
| Cableado Estructurado | 5% | 15% | 25% |
| Energía/UPS | 8% | 18% | 28% |
| Audio y Video | 10% | 22% | 35% |
| Automatización/Intrusión | 10% | 20% | 33% |
| IoT/GPS | 10% | 20% | 30% |
| Computo | 7% | 17% | 23% |
| WiFi Consumer (EZVIZ/TAPO) | 33% | 40% | 44% |

---

## REGLA 5: Datos por Proveedor — Mapeo de Campos

### Syscom API (`&moneda=MXN`)
| Campo Syscom | → Campo Odoo | Nota |
|-------------|-------------|------|
| `modelo` | `default_code`, `barcode` | SKU maestro |
| `titulo` | `name` | Nombre del producto |
| `precios.precio_descuento` | `standard_price` | Costo en MXN SIN IVA |
| `img_portada` | `image_1920` | Imagen principal |
| `imagenes[]` | `product.image` | Hasta 3 extras (endpoint individual) |
| `sat_key` | `unspsc_code_id` | Buscar en catálogo, nunca crear |
| `marca` | `description_sale` prefix | "Marca: HIKVISION" |
| `total_existencia` | `qty_available` | Via stock.quant |
| `unidad_de_medida.nombre` | `uom_id` | Mapeo UOM |
| `peso` | `weight` | En kg |
| `categorias[].id` | `categ_id` | Via CAT_MAP |
| `garantia` | — | No se guarda hoy |
| `producto_id` | — | Para consultar endpoint individual |

### CVA API
| Campo CVA | → Campo Odoo | Nota |
|----------|-------------|------|
| `codigo_fabricante` | `default_code`, `barcode` | Buscar existente primero |
| `clave` | `product_code` en supplierinfo | Clave interna CVA |
| `descripcion` / `titulo` | `name` | Si no existe ya |
| `precio` | `standard_price` (si es menor) | Comparar con existente |
| `imagen` | `image_1920` (si vacía) | No sobreescribir si ya tiene |
| `existencia` | `qty_available` | Sumar o reemplazar? |
| `sat` | `unspsc_code_id` | Si no tiene ya |

### TVC
| Campo TVC | → Campo Odoo | Nota |
|----------|-------------|------|
| Por documentar | — | WF-TVC activo pero no mapeado |

---

## REGLA 6: Validación Pre-Escritura (Doble Verificación)

Antes de hacer `write` o `create` en Odoo, validar:

```javascript
// 1. Precio no puede ser absurdo
if (listPrice > 500000 || (costo > 0 && listPrice / costo > 5)) {
  → RECHAZAR como price_anomaly
}

// 2. SKU no puede estar vacío
if (!sku || sku.trim() === "") {
  → SKIP
}

// 3. Nombre no puede ser solo números o código
if (nombre.length < 5 || /^\d+$/.test(nombre)) {
  → SKIP
}

// 4. Costo debe ser positivo
if (costo <= 0) {
  → SKIP (no publicar producto gratis)
}
```

---

## REGLA 7: Auto-publicación (WF-REGLA-PUBLICAR)

Correr cada 30 minutos:

```
PUBLICAR si:
  image_1920 IS NOT NULL
  AND default_code IS NOT NULL
  AND description_sale IS NOT NULL AND len > 10
  AND list_price > 0
  AND qty_available > 0

DESPUBLICAR si:
  qty_available <= 0
  OR image_1920 IS NULL
  OR list_price <= 0
```

---

## REGLA 8: Frecuencia de Actualización

| Proveedor | Precios | Stock | Altas/Bajas |
|-----------|---------|-------|-------------|
| Syscom | Cada 1h (WF1) | Cada 1h (WF1) | Diario 1am (WF3/WF4) |
| CVA | Cada 3 min (WF-CVA) | Cada 3 min | Con el sync |
| TVC | Cada ? (WF-TVC) | Cada ? | Con el sync |

---

## Estado Actual vs Meta

| Métrica | Hoy | Meta | Gap |
|---------|-----|------|-----|
| Productos con imagen | 37,297 (64%) | 45,000+ (77%) | -7,703 |
| Productos publicados | 1,954 (3%) | 17,124 (29%) | -15,170 |
| Con proveedor registrado | 0 (0%) | 58,215 (100%) | -58,215 |
| Con código SAT | 39,030 (67%) | 50,000+ (86%) | -10,970 |
| Con peso | 0 (0%) | 38,306 (66%) | -38,306 |

---

## Plan de Implementación

### Fase 1 — Inmediata (esta semana)
1. Actualizar WF-REGLA-PUBLICAR con las 5 condiciones
2. Agregar `product.supplierinfo` a Syscom sync
3. Agregar `product.supplierinfo` a CVA sync
4. Despublicar los 2 productos sin stock

### Fase 2 — Corto plazo (2 semanas)
5. Crear comparativo multi-proveedor (dashboard o reporte)
6. Completar imágenes pendientes (WF5 + CVA + fabricante)
7. Agregar peso desde Syscom API (`peso` field)
8. Limpiar categoría "COMPONENTES DE COMPUTO"

### Fase 3 — Mediano plazo (1 mes)
9. Implementar regla del costo más bajo automático
10. Completar códigos SAT faltantes
11. Mapear campos TVC
12. Dashboard de salud de datos
