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
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        body = json.dumps(payload).encode() if payload is not None else None
        suffix = f"?{urlencode(query)}" if query else ""
        request = Request(
            f"{self.url}/rest/v1/user_preferences{suffix}",
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
            await asyncio.to_thread(self._request, "POST", values)
        return UserPreferences(
            default_volume=values["default_volume"],
            loop_mode=values["loop_mode"],
            autoplay=values["autoplay"],
            announce_now_playing=values["announce_now_playing"],
        )


preferences = PreferencesStore()
