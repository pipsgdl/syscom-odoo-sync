#!/bin/bash
# Se ejecuta EN oracle-vps: crea tablas/vistas públicas de la calculadora + carga (idempotente).
set -e
P(){ docker exec -i supabase-db psql -U postgres -d postgres -v ON_ERROR_STOP=1 "$@"; }
echo "== schema público =="
P < /tmp/oracle_pub_schema.sql
echo "== truncate =="
P -c "truncate public.calculadora_compat; truncate public.calculadora_refacciones;"
echo "== copy refacciones (sin costo) =="
P -c "\copy public.calculadora_refacciones (sku,categoria,nombre,precio,disponible,specs,imagenes,envio) from stdin with (format csv, null '')" < /tmp/nojomo_catalogo_pub.csv
echo "== copy compat =="
P -c "\copy public.calculadora_compat (modelo_laptop,marca,sku,categoria) from stdin with (format csv, null '')" < /tmp/nojomo_compat.csv
echo "== verificación =="
P -c "select (select count(*) from public.calculadora_refacciones) refacciones, (select count(*) from public.calculadora_compat) compat, (select count(distinct marca) from public.calculadora_compat) marcas, (select count(distinct modelo_laptop) from public.calculadora_compat) modelos;"
echo "== reiniciar PostgREST para exponer =="
docker restart supabase-rest >/dev/null 2>&1 && echo "supabase-rest reiniciado"
