import asyncio
import os
from dataclasses import dataclass
from typing import Any

from supabase import Client, create_client


@dataclass(frozen=True)
class UserPreferences:
    default_volume: int = 100
    loop_mode: str = "off"
    autoplay: bool = True
    announce_now_playing: bool = True


class PreferencesStore:
    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self.client: Client | None = create_client(url, key) if url and key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def get(self, user_id: int, guild_id: int) -> UserPreferences:
        if self.client is None:
            return UserPreferences()

        response = await asyncio.to_thread(
            lambda: self.client.table("user_preferences")
            .select("default_volume, loop_mode, autoplay, announce_now_playing")
            .eq("user_id", user_id)
            .eq("guild_id", guild_id)
            .maybe_single()
            .execute()
        )
        data = response.data or {}
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
        if self.client is not None:
            await asyncio.to_thread(
                lambda: self.client.table("user_preferences").upsert(values).execute()
            )
        return UserPreferences(
            default_volume=values["default_volume"],
            loop_mode=values["loop_mode"],
            autoplay=values["autoplay"],
            announce_now_playing=values["announce_now_playing"],
        )


preferences = PreferencesStore()
