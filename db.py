from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from dataclasses import dataclass
from typing import Any


MAX_PLAYLIST_TRACKS = 1000
MAX_USER_PLAYLISTS = 15


def clean_environment_value(value: str, name: str) -> str:
    value = value.strip().strip('"\'')
    prefix = f"{name}="
    if value.lower().startswith(prefix.lower()):
        value = value[len(prefix):].strip().strip('"\'')
    return value


class StorageError(RuntimeError):
    def __init__(self, detail: str, client_message: str) -> None:
        super().__init__(detail)
        self.client_message = client_message


@dataclass(frozen=True)
class UserPreferences:
    default_volume: int = 100
    loop_mode: str = "off"
    autoplay: bool = True
    announce_now_playing: bool = True
    playlist_id: int | None = None


class PreferencesStore:
    def __init__(self) -> None:
        self.url = clean_environment_value(os.getenv("SUPABASE_URL", ""), "SUPABASE_URL").rstrip("/")
        self.key = (
            clean_environment_value(
                os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""), "SUPABASE_SERVICE_ROLE_KEY"
            )
            or clean_environment_value(os.getenv("SUPABASE_SERVICE_KEY", ""), "SUPABASE_SERVICE_KEY")
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def _request(
        self,
        method: str,
        table: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        body = json.dumps(payload).encode() if payload is not None else None
        suffix = f"?{urlencode(query)}" if query else ""
        request = Request(
            f"{self.url}/rest/v1/{table}{suffix}",
            data=body,
            method=method,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read() or "[]")
        except HTTPError as error:
            raise RuntimeError(f"Supabase request failed with HTTP {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"Supabase request failed: {error.reason}") from error

    async def get(self, user_id: int, guild_id: int) -> UserPreferences:
        if not self.enabled:
            return UserPreferences()

        response = await asyncio.to_thread(
            self._request,
            "GET",
            "user_preferences",
            None,
            {"user_id": f"eq.{user_id}", "guild_id": f"eq.{guild_id}"},
        )
        data = response[0] if response else {}
        return UserPreferences(
            default_volume=int(data.get("default_volume", 100)),
            loop_mode=data.get("loop_mode", "off"),
            autoplay=bool(data.get("autoplay", True)),
            announce_now_playing=bool(data.get("announce_now_playing", True)),
            playlist_id=int(data["playlist_id"]) if data.get("playlist_id") else None,
        )

    async def update(self, user_id: int, guild_id: int, **changes: Any) -> UserPreferences:
        current = await self.get(user_id, guild_id)
        values = {
            "user_id": user_id,
            "guild_id": guild_id,
            "default_volume": changes.get("default_volume", current.default_volume),
            "loop_mode": changes.get("loop_mode", current.loop_mode),
            "autoplay": changes.get("autoplay", current.autoplay),
            "announce_now_playing": changes.get(
                "announce_now_playing", current.announce_now_playing
            ),
            "playlist_id": changes.get("playlist_id", current.playlist_id),
        }
        if self.enabled:
            await asyncio.to_thread(self._request, "POST", "user_preferences", values)
        return UserPreferences(
            default_volume=values["default_volume"],
            loop_mode=values["loop_mode"],
            autoplay=values["autoplay"],
            announce_now_playing=values["announce_now_playing"],
            playlist_id=values["playlist_id"],
        )


preferences = PreferencesStore()


class GuildStore:
    def __init__(self) -> None:
        self.url = clean_environment_value(os.getenv("SUPABASE_URL", ""), "SUPABASE_URL").rstrip("/")
        self.key = (
            clean_environment_value(
                os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""), "SUPABASE_SERVICE_ROLE_KEY"
            )
            or clean_environment_value(os.getenv("SUPABASE_SERVICE_KEY", ""), "SUPABASE_SERVICE_KEY")
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    async def cache(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = [dict(row, updated_at=timestamp) for row in rows]
        await asyncio.to_thread(self._request, "POST", "guilds", payload)

    async def remove(self, guild_id: int) -> None:
        await asyncio.to_thread(
            self._request,
            "DELETE",
            "guilds",
            None,
            {"id": f"eq.{guild_id}"},
        )

    def _request(
        self,
        method: str,
        table: str,
        payload: Any | None = None,
        query: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            raise RuntimeError("Supabase guild cache is not configured")
        body = json.dumps(payload).encode() if payload is not None else None
        suffix = f"?{urlencode(query)}" if query else ""
        request = Request(
            f"{self.url}/rest/v1/{table}{suffix}",
            data=body,
            method=method,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read() or "[]")
        except HTTPError as error:
            raise RuntimeError(f"Supabase guild cache failed with HTTP {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"Supabase guild cache failed: {error.reason}") from error


guilds = GuildStore()


class PlaylistStore:
    def __init__(self) -> None:
        self.url = clean_environment_value(os.getenv("SUPABASE_URL", ""), "SUPABASE_URL").rstrip("/")
        self.key = (
            clean_environment_value(
                os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""), "SUPABASE_SERVICE_ROLE_KEY"
            )
            or clean_environment_value(os.getenv("SUPABASE_SERVICE_KEY", ""), "SUPABASE_SERVICE_KEY")
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    async def create(self, owner_id: int, guild_id: int | None, name: str, description: str) -> dict[str, Any]:
        existing = await asyncio.to_thread(
            self._request,
            "GET",
            "playlists",
            None,
            {"owner_id": f"eq.{owner_id}", "limit": str(MAX_USER_PLAYLISTS)},
        )
        if len(existing) >= MAX_USER_PLAYLISTS:
            raise StorageError(
                f"Playlist limit reached for owner {owner_id}",
                f"You can have at most {MAX_USER_PLAYLISTS} playlists.",
            )
        rows = await asyncio.to_thread(
            self._request,
            "POST",
            "playlists",
            {"owner_id": owner_id, "guild_id": guild_id, "name": name, "description": description},
        )
        if not rows:
            raise StorageError(
                "Supabase returned no playlist row after creation",
                "Supabase created no playlist record. Check that the playlists migration has been applied.",
            )
        return rows[0]

    async def list(self, owner_id: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._request,
            "GET",
            "playlists",
            None,
            {"owner_id": f"eq.{owner_id}", "order": "created_at.desc", "limit": "15"},
        )

    async def find(self, playlist_id: int, owner_id: int) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._request,
            "GET",
            "playlists",
            None,
            {"id": f"eq.{playlist_id}", "owner_id": f"eq.{owner_id}"},
        )
        return rows[0] if rows else None

    async def find_by_name(self, name: str, owner_id: int) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._request,
            "GET",
            "playlists",
            None,
            {
                "owner_id": f"eq.{owner_id}",
                "name": f"eq.{name}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def tracks(self, playlist_id: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._request,
            "GET",
            "playlist_tracks",
            None,
            {
                "playlist_id": f"eq.{playlist_id}",
                "order": "position.asc",
                "limit": str(MAX_PLAYLIST_TRACKS),
            },
        )

    async def search(self, owner_id: int, query: str, limit: int = 15) -> list[dict[str, Any]]:
        term = query.strip().replace("*", "")
        if not term:
            return []
        rows = await asyncio.to_thread(
            self._request,
            "GET",
            "playlists",
            None,
            {
                "owner_id": f"eq.{owner_id}",
                "name": f"ilike.*{term}*",
                "order": "created_at.desc",
                "limit": str(min(limit, 15)),
            },
        )
        return rows[:15]

    async def delete(self, playlist_id: int, owner_id: int) -> bool:
        rows = await asyncio.to_thread(
            self._request,
            "DELETE",
            "playlists",
            None,
            {"id": f"eq.{playlist_id}", "owner_id": f"eq.{owner_id}"},
        )
        return bool(rows)

    async def add_track(
        self,
        playlist_id: int,
        added_by: int,
        title: str,
        uri: str,
        length_ms: int | None,
    ) -> None:
        existing = await asyncio.to_thread(
            self._request,
            "GET",
            "playlist_tracks",
            None,
            {"playlist_id": f"eq.{playlist_id}", "order": "position.desc", "limit": "1"},
        )
        position = int(existing[0]["position"]) + 1 if existing else 1
        if position > MAX_PLAYLIST_TRACKS:
            raise StorageError(
                f"Playlist {playlist_id} reached its track limit",
                f"Each playlist can contain at most {MAX_PLAYLIST_TRACKS} songs.",
            )
        await asyncio.to_thread(
            self._request,
            "POST",
            "playlist_tracks",
            {
                "playlist_id": playlist_id,
                "position": position,
                "title": title,
                "uri": uri,
                "length_ms": length_ms,
                "added_by": added_by,
            },
        )

    async def add_tracks(
        self,
        playlist_id: int,
        added_by: int,
        tracks: list[dict[str, Any]],
    ) -> None:
        if not tracks:
            return
        existing = await asyncio.to_thread(
            self._request,
            "GET",
            "playlist_tracks",
            None,
            {"playlist_id": f"eq.{playlist_id}", "order": "position.desc", "limit": "1"},
        )
        next_position = int(existing[0]["position"]) + 1 if existing else 1
        available = max(MAX_PLAYLIST_TRACKS - next_position + 1, 0)
        payload = [
            {
                "playlist_id": playlist_id,
                "position": next_position + offset,
                "title": track["title"],
                "uri": track["uri"],
                "length_ms": track.get("length_ms"),
                "added_by": added_by,
            }
            for offset, track in enumerate(tracks[:available])
        ]
        if not payload:
            return
        await asyncio.to_thread(self._request, "POST", "playlist_tracks", payload)

    async def like(self, playlist_id: int, user_id: int) -> None:
        if self.enabled:
            await asyncio.to_thread(
                self._request,
                "POST",
                "playlist_likes",
                {"playlist_id": playlist_id, "user_id": user_id},
            )

    def _request(
        self,
        method: str,
        table: str,
        payload: Any | None = None,
        query: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            raise StorageError(
                "Supabase URL or service key is missing",
                "Playlist storage is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
            )
        parsed_url = urlparse(self.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise StorageError(
                f"Invalid Supabase URL: {self.url!r}",
                "Supabase URL is invalid. Use the Project URL ending in `.supabase.co`.",
            )
        body = json.dumps(payload).encode() if payload is not None else None
        suffix = f"?{urlencode(query)}" if query else ""
        request = Request(
            f"{self.url}/rest/v1/{table}{suffix}",
            data=body,
            method=method,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read() or "[]")
        except HTTPError as error:
            messages = {
                401: "Supabase rejected the service key. Use the project's service-role key.",
                403: "Supabase rejected the service key or database permission.",
                404: "Supabase endpoint or table was not found. Check SUPABASE_URL and apply migrations.",
                409: "Supabase rejected the playlist because it already exists or conflicts with another row.",
            }
            raise StorageError(
                f"Supabase request failed with HTTP {error.code}",
                messages.get(error.code, "Supabase is temporarily unavailable. Try again shortly."),
            ) from error
        except URLError as error:
            if isinstance(error.reason, socket.gaierror):
                client_message = "Supabase host could not be reached. Check that SUPABASE_URL is your Project URL."
            elif isinstance(error.reason, TimeoutError):
                client_message = "Supabase took too long to respond. Try the command again shortly."
            else:
                client_message = "Supabase could not be reached. Check the URL and try again."
            raise StorageError(f"Supabase request failed: {error.reason}", client_message) from error


playlists = PlaylistStore()
