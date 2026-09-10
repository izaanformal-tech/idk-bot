from __future__ import annotations

import asyncio
import base64
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import aiohttp
import supabase


class SpotifyTokenError(RuntimeError):
    """Raised when a Spotify token cannot be loaded or refreshed."""


class SpotifyTokenManager:
    """Manage Spotify OAuth credentials stored in Supabase.

    The OAuth callback is intentionally outside this process. The Edge Function
    exchanges the authorization code and writes the token row before the bot
    calls ``get_access_token``.
    """

    AUTHORIZATION_URL = "https://accounts.spotify.com/authorize"
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_URL = "https://api.spotify.com/v1"
    SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"

    def __init__(self) -> None:
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()
        supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not service_key:
            service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self.supabase: Any | None = (
            getattr(supabase, "create_client")(supabase_url, service_key)
            if supabase_url and service_key
            else None
        )

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri and self.supabase)

    def authorization_url(self, discord_id: int | str) -> str:
        """Return the Spotify Authorization Code Flow URL for a Discord user."""
        if not self.client_id or not self.redirect_uri:
            raise SpotifyTokenError("Spotify OAuth is not configured.")
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": self.SCOPES,
                "state": str(discord_id),
            }
        )
        return f"{self.AUTHORIZATION_URL}?{query}"

    async def get_credentials(self, discord_id: int | str) -> dict[str, Any] | None:
        """Fetch one user's token row from ``user_spotify_tokens``."""
        if self.supabase is None:
            raise SpotifyTokenError("Supabase is not configured.")
        client: Any = self.supabase

        def fetch() -> dict[str, Any] | None:
            response = (
                client.table("user_spotify_tokens")
                .select("discord_id,refresh_token,access_token,expires_at")
                .eq("discord_id", str(discord_id))
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None

        try:
            return await asyncio.to_thread(fetch)
        except Exception as error:
            raise SpotifyTokenError("Could not read the Spotify token record.") from error

    async def get_access_token(self, discord_id: int | str) -> str:
        """Return a valid access token, refreshing and storing it when needed."""
        credentials = await self.get_credentials(discord_id)
        if credentials is None:
            raise SpotifyTokenError("Spotify is not linked for this Discord account.")

        expires_at = self._parse_timestamp(credentials.get("expires_at"))
        if expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
            return credentials["access_token"]

        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            raise SpotifyTokenError("The Spotify connection has no refresh token.")
        refreshed = await self._refresh_access_token(refresh_token)
        await self._update_credentials(str(discord_id), refreshed)
        return refreshed["access_token"]

    async def _refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.TOKEN_URL,
                    data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                    headers={"Authorization": f"Basic {basic}"},
                ) as response:
                    if response.status >= 400:
                        raise SpotifyTokenError(f"Spotify token refresh failed with HTTP {response.status}.")
                    data = await response.json()
        except SpotifyTokenError:
            raise
        except (aiohttp.ClientError, ValueError) as error:
            raise SpotifyTokenError("Spotify token refresh request failed.") from error

        access_token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not access_token or not isinstance(expires_in, (int, float)):
            raise SpotifyTokenError("Spotify returned an invalid refresh response.")
        return {
            "access_token": access_token,
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
        }

    async def _update_credentials(self, discord_id: str, token: dict[str, Any]) -> None:
        if self.supabase is None:
            raise SpotifyTokenError("Supabase is not configured.")
        client: Any = self.supabase

        def update() -> None:
            client.table("user_spotify_tokens").update(
                {
                    "access_token": token["access_token"],
                    "refresh_token": token["refresh_token"],
                    "expires_at": token["expires_at"],
                }
            ).eq("discord_id", discord_id).execute()

        try:
            await asyncio.to_thread(update)
        except Exception as error:
            raise SpotifyTokenError("Could not save the refreshed Spotify token.") from error

    async def get(self, discord_id: int | str, path: str) -> dict[str, Any]:
        """Make an authenticated Spotify API request for a linked user."""
        token = await self.get_access_token(discord_id)
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.API_URL}{path}", headers={"Authorization": f"Bearer {token}"}
                ) as response:
                    if response.status >= 400:
                        raise SpotifyTokenError(f"Spotify API request failed with HTTP {response.status}.")
                    return await response.json()
        except SpotifyTokenError:
            raise
        except (aiohttp.ClientError, ValueError) as error:
            raise SpotifyTokenError("Spotify API request failed.") from error

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


spotify = SpotifyTokenManager()
