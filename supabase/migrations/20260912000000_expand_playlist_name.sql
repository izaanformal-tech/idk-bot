alter table public.playlists
    drop constraint if exists playlists_name_check;

alter table public.playlists
    add constraint playlists_name_check check (char_length(name) between 1 and 200);