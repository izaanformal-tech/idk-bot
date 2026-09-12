create table if not exists public.guilds (
    id bigint primary key,
    name text not null,
    updated_at timestamptz not null default now()
);

alter table public.guilds enable row level security;