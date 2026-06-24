-- Calculadora de refacciones — instancia PÚBLICA (oracle-vps / supabase.ocean-tech.com.mx)
-- SOLO datos públicos: precio_venta, sin costo ni markup. El costo nunca llega aquí.
create table if not exists public.calculadora_refacciones (
  sku        text primary key,
  categoria  text,
  nombre     text,
  precio     numeric,
  disponible boolean,
  specs      jsonb default '{}'::jsonb,
  imagenes   jsonb default '[]'::jsonb,
  envio      jsonb default '{}'::jsonb
);
create table if not exists public.calculadora_compat (
  id            bigserial primary key,
  modelo_laptop text not null,
  marca         text,
  sku           text not null,
  categoria     text
);
create index if not exists idx_calc_compat_modelo on public.calculadora_compat (lower(modelo_laptop));
create index if not exists idx_calc_compat_marca  on public.calculadora_compat (marca);
create index if not exists idx_calc_compat_sku    on public.calculadora_compat (sku);

create or replace view public.calculadora_compatibles as
  select co.modelo_laptop, co.marca, co.categoria, c.sku, c.nombre, c.precio, c.disponible, c.imagenes
  from public.calculadora_compat co
  join public.calculadora_refacciones c on c.sku = co.sku;

create or replace view public.calculadora_marcas as
  select marca, categoria, count(distinct modelo_laptop)::int as modelos
  from public.calculadora_compat where marca is not null
  group by marca, categoria order by marca;

grant select on public.calculadora_refacciones to anon, authenticated;
grant select on public.calculadora_compat       to anon, authenticated;
grant select on public.calculadora_compatibles  to anon, authenticated;
grant select on public.calculadora_marcas       to anon, authenticated;
notify pgrst, 'reload schema';
