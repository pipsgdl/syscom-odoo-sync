-- Almacén de la calculadora de refacciones Nojomo (e-commerce dropshipping Ocean Tech)
create schema if not exists ocean;

-- Tabla INTERNA (incluye costo + markup — NUNCA se expone al front)
create table if not exists ocean.nojomo_catalogo (
  sku           text primary key,
  categoria     text not null,
  nombre        text,
  precio_costo  numeric,        -- costo Nojomo (INTERNO)
  markup        numeric,        -- factor aplicado (INTERNO)
  precio_venta  numeric,        -- público = round(costo*markup)
  stock         text,
  disponible    boolean,
  specs         jsonb default '{}'::jsonb,
  imagenes      jsonb default '[]'::jsonb,
  envio         jsonb default '{}'::jsonb,
  url           text,
  actualizado   timestamptz default now()
);

-- Mapa modelo_laptop -> sku (lo que alimenta la búsqueda de la calculadora)
create table if not exists ocean.nojomo_compat (
  id            bigserial primary key,
  modelo_laptop text not null,
  marca         text,
  sku           text not null,
  categoria     text
);
create index if not exists idx_njcompat_modelo on ocean.nojomo_compat (lower(modelo_laptop));
create index if not exists idx_njcompat_marca  on ocean.nojomo_compat (marca);
create index if not exists idx_njcompat_sku    on ocean.nojomo_compat (sku);
create index if not exists idx_njcat_categoria on ocean.nojomo_catalogo (categoria);

-- ===== VISTAS PÚBLICAS (sin costo ni markup) = CONTRATO de la calculadora =====
-- En schema public (expuesto por PostgREST). El front usa anon key contra estas vistas.
create or replace view public.calculadora_refacciones as
  select sku, categoria, nombre, precio_venta as precio, disponible, specs, imagenes, envio
  from ocean.nojomo_catalogo
  where precio_venta is not null and precio_venta > 0;

create or replace view public.calculadora_compatibles as
  select co.modelo_laptop, co.marca, co.categoria, c.sku, c.nombre,
         c.precio_venta as precio, c.disponible, c.imagenes
  from ocean.nojomo_compat co
  join ocean.nojomo_catalogo c on c.sku = co.sku
  where c.precio_venta is not null and c.precio_venta > 0;

create or replace view public.calculadora_marcas as
  select marca, categoria, count(distinct modelo_laptop)::int as modelos
  from ocean.nojomo_compat where marca is not null
  group by marca, categoria
  order by marca;

-- Permisos: el front (anon) solo ve las vistas públicas; nunca el schema ocean ni el costo.
grant select on public.calculadora_refacciones to anon, authenticated;
grant select on public.calculadora_compatibles to anon, authenticated;
grant select on public.calculadora_marcas      to anon, authenticated;

-- Refrescar cache de PostgREST para exponer las vistas nuevas
notify pgrst, 'reload schema';
