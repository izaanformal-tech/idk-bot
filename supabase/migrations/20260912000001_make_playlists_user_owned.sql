alter table public.playlists
    alter column guild_id drop not null;

alter table public.playlists
    drop constraint if exists playlists_owner_id_guild_id_name_key;

alter table public.playlists
    add constraint playlists_owner_id_name_key unique (owner_id, name);