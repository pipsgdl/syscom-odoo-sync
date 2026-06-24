#!/bin/bash
# Se ejecuta EN Roger: crea schema/tablas/vistas + carga los CSV via \copy (idempotente: trunca antes).
set -e
P(){ docker exec -i supabase-db psql -U postgres -d postgres -v ON_ERROR_STOP=1 "$@"; }
echo "== schema =="
P < /tmp/nojomo_schema.sql
echo "== truncate =="
P -c "truncate ocean.nojomo_compat; truncate ocean.nojomo_catalogo;"
echo "== copy catalogo =="
P -c "\copy ocean.nojomo_catalogo (sku,categoria,nombre,precio_costo,markup,precio_venta,stock,disponible,specs,imagenes,envio,url) from stdin with (format csv, null '')" < /tmp/nojomo_catalogo.csv
echo "== copy compat =="
P -c "\copy ocean.nojomo_compat (modelo_laptop,marca,sku,categoria) from stdin with (format csv, null '')" < /tmp/nojomo_compat.csv
echo "== verificación =="
P -c "select (select count(*) from ocean.nojomo_catalogo) catalogo, (select count(*) from ocean.nojomo_compat) compat, (select count(*) from public.calculadora_refacciones) publicas, (select count(distinct marca) from ocean.nojomo_compat) marcas, (select count(distinct modelo_laptop) from ocean.nojomo_compat) modelos;"
echo "== prueba: refacción para una laptop (Acer Aspire) =="
P -c "select modelo_laptop, marca, categoria, sku, nombre, precio from public.calculadora_compatibles where modelo_laptop ilike '%AcerAspire 1A114%' limit 3;"
echo "== confidencialidad: la vista pública NO debe traer costo =="
P -c "select column_name from information_schema.columns where table_schema='public' and table_name='calculadora_refacciones' order by 1;"
