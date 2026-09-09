create table if not exists public.user_preferences (
    user_id bigint not null,
    guild_id bigint not null,
    default_volume smallint not null default 100 check (default_volume between 0 and 100),
    loop_mode text not null default 'off' check (loop_mode in ('off', 'track', 'queue')),
    autoplay boolean not null default true,
    announce_now_playing boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (user_id, guild_id)
);

create or replace function public.set_user_preferences_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists user_preferences_updated_at on public.user_preferences;
create trigger user_preferences_updated_at
before update on public.user_preferences
for each row execute function public.set_user_preferences_updated_at();

alter table public.user_preferences enable row level security;
