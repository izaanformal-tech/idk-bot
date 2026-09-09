from __future__ import annotations

import asyncio
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UserPreferences:
    default_volume: int = 100
    loop_mode: str = "off"
    autoplay: bool = True
    announce_now_playing: bool = True


class PreferencesStore:
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

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
        }
        if self.enabled:
            await asyncio.to_thread(self._request, "POST", "user_preferences", values)
        return UserPreferences(
            default_volume=values["default_volume"],
            loop_mode=values["loop_mode"],
            autoplay=values["autoplay"],
            announce_now_playing=values["announce_now_playing"],
        )


preferences = PreferencesStore()


class PlaylistStore:
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    async def create(self, owner_id: int, guild_id: int, name: str, description: str) -> dict[str, Any]:
        rows = await asyncio.to_thread(
            self._request,
            "POST",
            "playlists",
            {"owner_id": owner_id, "guild_id": guild_id, "name": name, "description": description},
        )
        return rows[0] if rows else {"id": None, "name": name, "description": description}

    async def list(self, guild_id: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._request,
            "GET",
            "playlists",
            None,
            {"guild_id": f"eq.{guild_id}", "order": "created_at.desc"},
        )

    async def find(self, playlist_id: int, guild_id: int) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._request,
            "GET",
            "playlists",
            None,
            {"id": f"eq.{playlist_id}", "guild_id": f"eq.{guild_id}"},
        )
        return rows[0] if rows else None

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
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
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


playlists = PlaylistStore()
