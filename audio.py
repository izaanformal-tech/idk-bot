from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import wavelink


class AudioSourceError(RuntimeError):
    """Raised when Lavalink cannot resolve an audio source."""


@dataclass(frozen=True)
class PlaylistLoadResult:
    """Summary returned after a playlist has been added to a player's queue."""

    name: str
    track_count: int


async def process_audio_source(
    player: wavelink.Player,
    query: str,
) -> PlaylistLoadResult | wavelink.Playable:
    """Resolve a raw search query or media URI and add it to ``player.queue``.

    Lavalink, including LavaSrc when configured, decides whether ``query`` is a
    Spotify URI, YouTube URI, playlist URI, or a normal search query. Private
    Spotify playlists and unavailable YouTube media normally resolve to no
    tracks, so they are reported as ``AudioSourceError`` rather than silently
    adding an empty queue.
    """
    if player is None:
        raise AudioSourceError("A connected Lavalink player is required.")

    raw_query = query.strip()
    if not raw_query:
        raise AudioSourceError("The audio source cannot be empty.")

    try:
        result: Any = await wavelink.Playable.search(raw_query)
    except Exception as error:
        raise AudioSourceError(f"Lavalink could not load this audio source: {error}") from error

    if isinstance(result, wavelink.Playlist):
        tracks = [track for track in result.tracks if track is not None]
        if not tracks:
            raise AudioSourceError(
                "The playlist is empty, private, unavailable, or unsupported by Lavalink."
            )
        for track in tracks:
            await player.queue.put_wait(track)
        return PlaylistLoadResult(
            name=getattr(result, "name", None) or "Imported playlist",
            track_count=len(tracks),
        )

    if isinstance(result, wavelink.Playable):
        await player.queue.put_wait(result)
        return result

    if isinstance(result, list):
        tracks = [track for track in result if isinstance(track, wavelink.Playable)]
        if not tracks:
            raise AudioSourceError(
                "No playable tracks were found. The source may be private, unavailable, or unsupported."
            )
        track = tracks[0]
        await player.queue.put_wait(track)
        return track

    raise AudioSourceError(
        "Lavalink returned no playable track or playlist for this source."
    )