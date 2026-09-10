alter table public.user_preferences
    add column if not exists playlist_id bigint references public.playlists(id) on delete set null;