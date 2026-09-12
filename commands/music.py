import discord
import wavelink
import asyncio
from discord import app_commands
from discord.ext import commands
from typing import Any, cast
from urllib.parse import urlparse

from audio import AudioSourceError
from branding import DISPLAY_NAME
from commands.voice import get_player
from db import StorageError, playlists, preferences


LOOP_MODES = {
    "off": wavelink.QueueMode.normal,
    "track": wavelink.QueueMode.loop,
    "queue": wavelink.QueueMode.loop_all,
}
PLAYLIST_TRACK_LIMIT = 1000
QUEUE_TRACK_LIMIT = 1000
TRANSITION_FADE_MS = 1000
TRANSITION_START_MS = 500
TRANSITION_STEPS = 10
PLAYBACK_OPERATION_TIMEOUT = 12
CHANNEL_STATUS_TIMEOUT = 5


async def report_interaction_error(interaction: discord.Interaction, error: Exception) -> None:
    print(f"Music interaction failed: {error}")
    message = "I could not complete that music action. Please try again."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


def selected_value(interaction: discord.Interaction) -> str:
    data: dict[str, Any] = cast(dict[str, Any], interaction.data or {})
    values = data.get("values")
    if not isinstance(values, list) or not values:
        raise ValueError("The interaction did not contain a selection.")
    return str(values[0])


def voice_player(member: discord.Member) -> wavelink.Player | None:
    player = member.guild.voice_client
    return cast(wavelink.Player, player) if player is not None else None


def interaction_member(interaction: discord.Interaction) -> discord.Member:
    if not isinstance(interaction.user, discord.Member):
        raise RuntimeError("Music commands must be used in a server.")
    return interaction.user


def interaction_guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise RuntimeError("Music commands must be used in a server.")
    return interaction.guild_id


class MusicView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
        /,
    ) -> None:
        await report_interaction_error(interaction, error)


class MusicModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        await report_interaction_error(interaction, error)


class TrackSelection(MusicView):
    def __init__(self, cog: "Music", member: discord.Member, tracks: list[wavelink.Playable]) -> None:
        super().__init__(timeout=60)
        self.cog = cog
        self.member = member
        self.tracks = tracks
        options = [
            discord.SelectOption(
                label=track.title[:100],
                description=f"Result {index} | {format_duration(track.length)}"[:100],
                value=str(index - 1),
            )
            for index, track in enumerate(tracks, start=1)
        ]
        select = discord.ui.Select(
            placeholder="Choose a track to play...",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Only the person who searched can choose a track.", ephemeral=True)
            return False
        return True

    async def select_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            track = self.tracks[int(selected_value(interaction))]
            message = await self.cog.queue_tracks(self.member, [track])
        except (IndexError, ValueError):
            await interaction.followup.send("That track selection is no longer available.", ephemeral=True)
            self.stop()
            return
        except Exception as error:
            print(f"Track selection failed: {error}")
            await interaction.followup.send(
                "I could not start that track. Check that I can join and speak in your voice channel.",
                ephemeral=True,
            )
            return

        if interaction.message is not None:
            await interaction.message.edit(view=None)
        await interaction.followup.send(
            content=message,
            embed=self.cog.selected_track_embed(track),
            view=SelectedTrackView(self.cog, self.member, track),
            ephemeral=True,
        )
        self.stop()


class SelectedTrackView(MusicView):
    def __init__(self, cog: "Music", member: discord.Member, track: wavelink.Playable) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.member = member
        self.track = track

    @discord.ui.button(label="Play another", style=discord.ButtonStyle.primary, emoji="🔎")
    async def play_another(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SearchModal(self.cog, self.member))


class PlaylistTrackSelection(MusicView):
    def __init__(
        self,
        cog: "Music",
        member: discord.Member,
        playlist: dict,
        tracks: list[wavelink.Playable],
    ) -> None:
        super().__init__(timeout=60)
        self.cog = cog
        self.member = member
        self.playlist = playlist
        self.tracks = tracks
        options = [
            discord.SelectOption(
                label=track.title[:100],
                description=f"{format_duration(track.length)} • {track.author or 'Unknown artist'}"[:100],
                value=str(index),
                emoji="🎵",
            )
            for index, track in enumerate(tracks)
        ]
        select = discord.ui.Select(placeholder="Choose a song to save...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Only the requester can choose a song.", ephemeral=True)
            return False
        return True

    async def select_callback(self, interaction: discord.Interaction) -> None:
        track = self.tracks[int(selected_value(interaction))]
        if not track.uri:
            await interaction.response.send_message("That result has no saveable source URL.", ephemeral=True)
            return
        await playlists.add_track(
            self.playlist["id"],
            self.member.id,
            track.title,
            track.uri,
            track.length,
        )
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = True
        embed = self.cog.playlist_track_embed(self.playlist, track, "✅ Song added")
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class PlaylistLikeView(MusicView):
    def __init__(self, playlist: dict, member: discord.Member) -> None:
        super().__init__(timeout=300)
        self.playlist = playlist
        self.member = member

    @discord.ui.button(label="Like playlist", style=discord.ButtonStyle.success, emoji="💜")
    async def like_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await playlists.like(self.playlist["id"], interaction.user.id)
        button.disabled = True
        button.label = "Liked"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="💜 Playlist liked",
                description=f"You liked **{self.playlist['name']}**.",
                color=discord.Color.purple(),
            ),
            view=self,
        )


class SearchModal(MusicModal, title="Search music"):
    query = discord.ui.TextInput(
        label="Song, artist, URL, or playlist",
        placeholder="Try: artist - song title",
        max_length=200,
    )

    def __init__(self, cog: "Music", member: discord.Member) -> None:
        super().__init__()
        self.cog = cog
        self.member = member

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        tracks = await self.cog.search_tracks(str(self.query))
        if not tracks:
            await interaction.followup.send("No matches found.", ephemeral=True)
            return
        if is_url(str(self.query)) or len(tracks) == 1:
            await interaction.followup.send(
                await self.cog.queue_tracks(self.member, tracks), ephemeral=True
            )
            return
        await interaction.followup.send(
            "Choose a result before adding it to the queue.",
            embed=self.cog.search_embed(str(self.query), tracks[:5]),
            view=TrackSelection(self.cog, self.member, tracks[:5]),
            ephemeral=True,
        )


class MusicPanel(MusicView):
    def __init__(self, cog: "Music", member: discord.Member) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.member = member

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Run `/music panel` to get your own controls.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, emoji="🔎")
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SearchModal(self.cog, self.member))

    @discord.ui.button(label="Now playing", style=discord.ButtonStyle.secondary, emoji="🎵")
    async def now_playing_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = await self.cog.now_playing_embed(self.member)
        await interaction.response.send_message(embed=embed, view=self.cog.now_playing_view(self.member), ephemeral=True)

    @discord.ui.button(label="Pause / resume", style=discord.ButtonStyle.secondary, emoji="⏯️")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = voice_player(self.member)
        paused = bool(player and player.paused)
        await interaction.response.send_message(await self.cog.pause(self.member, not paused), ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.skip(self.member), ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, emoji="📜", row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.queue_text(self.member), ephemeral=True)

    @discord.ui.button(label="Import Spotify playlist", style=discord.ButtonStyle.success, emoji="🎧", row=1)
    async def import_spotify_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SpotifyPlaylistURLModal(self.cog, self.member))


class SpotifyPlaylistURLModal(discord.ui.Modal, title="Import Spotify playlist"):
    url = discord.ui.TextInput(
        label="Spotify playlist URL",
        placeholder="https://open.spotify.com/playlist/...",
        max_length=300,
    )

    def __init__(self, cog: "Music", member: discord.Member) -> None:
        super().__init__()
        self.cog = cog
        self.member = member

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        message = await self.cog.import_lavalink_playlist(self.member, str(self.url))
        await interaction.followup.send(message, ephemeral=True)


def is_url(query: str) -> bool:
    return urlparse(query).scheme in {"http", "https"}


def normalize_spotify_playlist_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() not in {"open.spotify.com", "www.open.spotify.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0].startswith("intl-"):
        parts.pop(0)
    if len(parts) < 2 or parts[0] != "playlist" or not parts[1]:
        return None
    return f"https://open.spotify.com/playlist/{parts[1]}"


def format_duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "live"
    total_seconds = max(milliseconds, 0) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


class Music(commands.GroupCog, group_name="music"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._transition_tasks: dict[int, asyncio.Task[None]] = {}
        self._transitioning: set[int] = set()

    playlist = app_commands.Group(name="playlist", description="Create and share playlists")

    @staticmethod
    async def search_tracks(query: str) -> list[wavelink.Playable]:
        try:
            result = await asyncio.wait_for(wavelink.Playable.search(query.strip()), timeout=12)
        except asyncio.TimeoutError as error:
            raise RuntimeError("Music search timed out. Please check Lavalink and try again.") from error
        if isinstance(result, wavelink.Playlist):
            return list(result.tracks)
        return list(result)

    async def import_lavalink_playlist(
        self,
        member: discord.Member,
        url: str,
        name: str | None = None,
        description: str = "",
    ) -> str:
        spotify_url = normalize_spotify_playlist_url(url)
        if spotify_url is None:
            return "Enter a valid Spotify playlist URL from open.spotify.com."
        try:
            result = await asyncio.wait_for(wavelink.Playable.search(spotify_url), timeout=12)
        except Exception as error:
            print(f"Spotify playlist import failed: {error}")
            return "Lavalink could not load that Spotify playlist. Check that LavaSrc is enabled and configured on the Lavalink host."
        if not isinstance(result, wavelink.Playlist):
            return "Lavalink did not return a playlist for that URL."
        tracks = [track for track in result.tracks if track is not None]
        if not tracks:
            return "No playable tracks were found in that Spotify playlist."
        return await self.save_imported_playlist(member, result, tracks, name, description)

    async def save_imported_playlist(
        self,
        member: discord.Member,
        playlist: wavelink.Playlist,
        tracks: list[wavelink.Playable],
        name_override: str | None = None,
        description: str = "",
    ) -> str:
        name = (name_override or getattr(playlist, "name", None) or "Imported playlist").strip() or "Imported playlist"
        cached_tracks = [track for track in tracks[:PLAYLIST_TRACK_LIMIT] if track.uri]
        try:
            saved_playlist = await playlists.create(member.id, None, name, description)
            if saved_playlist.get("id") is None:
                return "I could not save that playlist because playlist storage is unavailable."
            await playlists.add_tracks(
                saved_playlist["id"],
                member.id,
                [
                    {"title": track.title, "uri": track.uri, "length_ms": track.length}
                    for track in cached_tracks
                ],
            )
        except StorageError as error:
            print(f"Playlist persistence failed: {error}")
            return error.client_message
        extra_count = max(len(tracks) - PLAYLIST_TRACK_LIMIT, 0)
        message = f"Created playlist **{name}** with {len(cached_tracks)} song(s)."
        if extra_count:
            message += f" {extra_count} extra song(s) were left uncached because the limit is 1000."
        elif len(tracks) > len(cached_tracks):
            message += f" {len(tracks) - len(cached_tracks)} track(s) were not saved because Lavalink returned no source URL."
        return message

    async def play_query(self, member: discord.Member, query: str) -> str:
        if not query.strip():
            return "Give me a song name, URL, or playlist URL."
        try:
            player = await get_player(member)
        except PermissionError as error:
            return str(error)
        if player is None:
            return "Join a voice channel first."

        saved = await preferences.get(member.id, member.guild.id)
        await player.set_volume(saved.default_volume)
        player.queue.mode = LOOP_MODES[saved.loop_mode]

        tracks = await self.search_tracks(query)
        if not tracks:
            return "I could not find anything for that search."

        return await self.queue_tracks(member, tracks)

    async def queue_tracks(self, member: discord.Member, tracks: list[wavelink.Playable]) -> str:
        try:
            player = await asyncio.wait_for(
                get_player(member), timeout=PLAYBACK_OPERATION_TIMEOUT
            )
        except asyncio.TimeoutError:
            return "Voice connection timed out. Please try again."
        if player is None:
            return "Join a voice channel first."

        queued_count = len(player.queue)
        available_slots = max(QUEUE_TRACK_LIMIT - queued_count, 0)
        if available_slots == 0:
            return "The queue is full. The maximum queue size is 1000 tracks."
        tracks = tracks[:available_slots]

        saved = await preferences.get(member.id, member.guild.id)
        if saved.playlist_id:
            destination = await playlists.find(saved.playlist_id, member.id)
            if destination:
                for track in tracks:
                    if track.uri:
                        await playlists.add_track(
                            destination["id"], member.id, track.title, track.uri, track.length
                        )
        for track in tracks:
            await player.queue.put_wait(track)
        if not player.playing:
            first = await player.queue.get_wait()
            try:
                await asyncio.wait_for(
                    player.play(first), timeout=PLAYBACK_OPERATION_TIMEOUT
                )
            except asyncio.TimeoutError:
                return "Lavalink took too long to start the song. Please try again."
            await self.update_voice_status(player, first)
            added = len(tracks) - 1
            suffix = f" Added {added} more tracks to the queue." if added else ""
            return f"Now playing **{first.title}**.{suffix}"
        return f"Queued **{len(tracks)} track(s)**."

    async def update_voice_status(
        self,
        player: wavelink.Player,
        track: wavelink.Playable | None,
    ) -> None:
        channel = player.channel
        if channel is None:
            return
        if track:
            title = track.title.strip() or "a song"
            artist = (track.author or "Unknown artist").strip()
            status = f"▶️ now playing {title} - {artist}"
        else:
            status = None
        try:
            await asyncio.wait_for(
                channel.edit(status=status, reason="Update music voice channel status"),
                timeout=CHANNEL_STATUS_TIMEOUT,
            )
        except (asyncio.TimeoutError, discord.Forbidden, discord.HTTPException) as error:
            print(f"Could not update music voice channel status: {error}")

    def now_playing_view(self, member: discord.Member) -> discord.ui.View:
        view = discord.ui.View(timeout=300)
        player = voice_player(member)
        track = player.current if player else None
        if track and track.uri:
            view.add_item(discord.ui.Button(label="▶️ Play source", style=discord.ButtonStyle.link, url=track.uri))
        return view

    async def now_playing_embed(self, member: discord.Member) -> discord.Embed:
        player = voice_player(member)
        if player is None or player.current is None:
            return discord.Embed(title="Nothing is playing", description="Start music with `/music panel`.", color=discord.Color.dark_grey())
        track = player.current
        embed = discord.Embed(
            title="🎵 Now playing",
            description=f"🎵 [{track.title}]({track.uri})" if track.uri else f"🎵 {track.title}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Artist", value=track.author or "Unknown", inline=True)
        embed.add_field(name="Duration", value=f"{format_duration(player.position)} / {format_duration(track.length)}", inline=True)
        embed.add_field(name="Queue", value=str(len(player.queue)), inline=True)
        artwork = getattr(track, "artwork", None)
        if artwork:
            embed.set_thumbnail(url=artwork)
        embed.set_footer(text=f"{DISPLAY_NAME} • use the panel buttons to control playback")
        return embed

    def search_embed(self, query: str, tracks: list[wavelink.Playable]) -> discord.Embed:
        embed = discord.Embed(title="Music search", description=f"Results for **{query}**", color=discord.Color.blurple())
        for index, track in enumerate(tracks, start=1):
            embed.add_field(
                name=f"{index}. {track.title}",
                value=f"`{format_duration(track.length)}` | {track.author or 'Unknown artist'}",
                inline=False,
            )
        return embed

    @staticmethod
    def selected_track_embed(track: wavelink.Playable) -> discord.Embed:
        embed = discord.Embed(
            title="🎵 Song ready",
            description=f"**{track.title}**",
            color=discord.Color.green(),
        )
        embed.add_field(name="Artist", value=track.author or "Unknown artist", inline=True)
        embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
        if getattr(track, "artwork", None):
            embed.set_thumbnail(url=track.artwork)
        return embed

    @staticmethod
    def playlist_track_embed(playlist: dict, track: wavelink.Playable, title: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"{title} • 📚 {playlist['name']}",
            description=f"[🎵 {track.title}]({track.uri})" if track.uri else f"🎵 {track.title}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Artist", value=track.author or "Unknown artist", inline=True)
        embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
        artwork = getattr(track, "artwork", None)
        if artwork:
            embed.set_thumbnail(url=artwork)
        embed.set_footer(text=f"{DISPLAY_NAME} • saved to your playlist")
        return embed

    @staticmethod
    def playlist_embed(playlist: dict, title: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"📚 {title}",
            description=f"**{playlist['name']}**\n{playlist.get('description', '') or 'No description.'}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Find it", value="Use `/music playlist search`", inline=True)
        embed.set_footer(text=f"{DISPLAY_NAME} • playlist community features")
        return embed

    @app_commands.command(name="panel", description="Open your music control panel")
    async def panel_slash(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Music control panel",
            description="Search for music, inspect playback, or manage the queue with the buttons below.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=MusicPanel(self, interaction_member(interaction)),
            ephemeral=True,
        )

    @app_commands.command(name="settings", description="View or update your music settings")
    @app_commands.describe(
        playlist_name="Your playlist name used as the preferred destination",
        volume="Default player volume from 0 to 100",
        loop="Default loop mode",
        autoplay="Automatically continue the queue",
        announcements="Show now-playing announcements",
    )
    @app_commands.choices(loop=[app_commands.Choice(name=name, value=name) for name in LOOP_MODES])
    async def settings_slash(
        self,
        interaction: discord.Interaction,
        playlist_name: str | None = None,
        volume: app_commands.Range[int, 0, 100] | None = None,
        loop: app_commands.Choice[str] | None = None,
        autoplay: bool | None = None,
        announcements: bool | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        changes = {}
        if playlist_name is not None:
            playlist = await playlists.find_by_name(playlist_name, interaction.user.id)
            if playlist is None:
                await interaction.followup.send("That playlist does not exist in your library.", ephemeral=True)
                return
            changes["playlist_id"] = playlist["id"]
        if volume is not None:
            changes["default_volume"] = volume
        if loop is not None:
            changes["loop_mode"] = loop.value
        if autoplay is not None:
            changes["autoplay"] = autoplay
        if announcements is not None:
            changes["announce_now_playing"] = announcements
        values = await preferences.update(interaction.user.id, interaction_guild_id(interaction), **changes)
        embed = discord.Embed(title="🎵 Music settings", color=discord.Color.blurple())
        preferred_name = "Not set"
        if values.playlist_id:
            preferred = await playlists.find(values.playlist_id, interaction.user.id)
            if preferred:
                preferred_name = preferred["name"]
        embed.add_field(name="Preferred playlist", value=preferred_name, inline=False)
        embed.add_field(name="Volume", value=f"{values.default_volume}%", inline=True)
        embed.add_field(name="Loop", value=values.loop_mode, inline=True)
        embed.add_field(name="Autoplay", value="On" if values.autoplay else "Off", inline=True)
        embed.add_field(name="Announcements", value="On" if values.announce_now_playing else "Off", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def current(self, member: discord.Member) -> str:
        player = voice_player(member)
        if player is None or player.current is None:
            return "Nothing is playing."
        track = player.current
        return f"Now playing **{track.title}** (`{format_duration(player.position)} / {format_duration(track.length)}`)."

    async def queue_text(self, member: discord.Member) -> str:
        player = voice_player(member)
        if player is None or len(player.queue) == 0:
            return "The queue is empty."
        tracks = list(player.queue)[:15]
        lines = [f"{index}. {track.title}" for index, track in enumerate(tracks, start=1)]
        remaining = len(player.queue) - len(tracks)
        if remaining:
            lines.append(f"...and {remaining} more.")
        return "**Queue**\n" + "\n".join(lines)

    async def skip(self, member: discord.Member) -> str:
        player = voice_player(member)
        if player is None or not player.playing:
            return "Nothing is playing."
        self.cancel_transition(player)
        await player.skip()
        return "Skipped the current track."

    async def stop(self, member: discord.Member) -> str:
        player = voice_player(member)
        if player is None:
            return "I am not connected to a voice channel."
        self.cancel_transition(player)
        player.queue.clear()
        await player.stop()
        await self.update_voice_status(player, None)
        return "Stopped playback and cleared the queue."

    async def pause(self, member: discord.Member, paused: bool) -> str:
        player = voice_player(member)
        if player is None or not player.playing:
            return "Nothing is playing."
        await player.pause(paused)
        return "Paused playback." if paused else "Resumed playback."

    async def set_loop(self, member: discord.Member, mode: str) -> str:
        player = voice_player(member)
        if player is None:
            return "Join a voice channel first."
        player.queue.mode = LOOP_MODES[mode]
        await preferences.update(member.id, member.guild.id, loop_mode=mode)
        labels = {"off": "disabled", "track": "set to the current track", "queue": "set to the queue"}
        return f"Looping {labels[mode]}."

    async def shuffle(self, member: discord.Member) -> str:
        player = voice_player(member)
        if player is None or len(player.queue) < 2:
            return "Add at least two tracks before shuffling."
        player.queue.shuffle()
        return "Shuffled the queue."

    async def remove(self, member: discord.Member, position: int) -> str:
        player = voice_player(member)
        if player is None:
            return "The queue is empty."
        tracks = list(player.queue)
        if position < 1 or position > len(tracks):
            return "That queue position does not exist."
        removed = tracks.pop(position - 1)
        player.queue.clear()
        for track in tracks:
            await player.queue.put_wait(track)
        return f"Removed **{removed.title}** from the queue."

    async def clear(self, member: discord.Member) -> str:
        player = voice_player(member)
        if player is None or len(player.queue) == 0:
            return "The queue is already empty."
        player.queue.clear()
        return "Cleared the queue."

    async def volume(self, member: discord.Member, level: int) -> str:
        player = voice_player(member)
        if player is None:
            return "Join a voice channel first."
        await player.set_volume(level)
        await preferences.update(member.id, member.guild.id, default_volume=level)
        return f"Volume set to **{level}%**."

    async def seek(self, member: discord.Member, seconds: int) -> str:
        player = voice_player(member)
        if player is None or player.current is None:
            return "Nothing is playing."
        if seconds < 0 or (
            player.current.length is not None and seconds * 1000 > player.current.length
        ):
            return "That position is outside the current track."
        await player.seek(seconds * 1000)
        return f"Seeked to **{format_duration(seconds * 1000)}**."

    async def replay(self, member: discord.Member) -> str:
        player = voice_player(member)
        if player is None or player.current is None:
            return "Nothing is playing."
        self.cancel_transition(player)
        await player.seek(0)
        return "Restarted the current track."

    async def search_text(self, query: str) -> str:
        tracks = await self.search_tracks(query)
        if not tracks:
            return "I could not find anything for that search."
        lines = [f"{index}. {track.title}" for index, track in enumerate(tracks[:5], start=1)]
        return "**Search results**\n" + "\n".join(lines)

    def cancel_transition(self, player: wavelink.Player) -> None:
        guild_id = player.guild.id if player.guild else None
        if guild_id is None:
            return
        task = self._transition_tasks.pop(guild_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def fade_volume(self, player: wavelink.Player, start: int, end: int) -> None:
        step_delay = TRANSITION_FADE_MS / TRANSITION_STEPS / 1000
        for step in range(1, TRANSITION_STEPS + 1):
            volume = round(start + (end - start) * step / TRANSITION_STEPS)
            await player.set_volume(volume)
            await asyncio.sleep(step_delay)

    async def transition_to_next(
        self,
        player: wavelink.Player,
        current: wavelink.Playable,
    ) -> None:
        guild_id = player.guild.id if player.guild else None
        if guild_id is None or guild_id in self._transitioning:
            return
        self._transitioning.add(guild_id)
        try:
            if player.current is not current or len(player.queue) == 0:
                return
            target_volume = max(0, min(player.volume, 1000))
            await self.fade_volume(player, target_volume, 0)
            next_track = player.queue.get()
            starting_volume = 0
            await player.play(
                next_track,
                start=TRANSITION_START_MS,
                volume=starting_volume,
            )
            await self.fade_volume(player, starting_volume, target_volume)
            await self.update_voice_status(player, next_track)
            self._transition_tasks[guild_id] = asyncio.create_task(
                self.schedule_transition(player, next_track)
            )
        finally:
            self._transitioning.discard(guild_id)

    async def schedule_transition(self, player: wavelink.Player, track: wavelink.Playable) -> None:
        guild_id = player.guild.id if player.guild else None
        if guild_id is None or track.length is None:
            return
        try:
            while player.current is track and player.playing:
                remaining_ms = track.length - player.position
                if remaining_ms <= TRANSITION_FADE_MS:
                    await self.transition_to_next(player, track)
                    return
                await asyncio.sleep(min(remaining_ms - TRANSITION_FADE_MS, 250) / 1000)
        except asyncio.CancelledError:
            raise
        finally:
            if self._transition_tasks.get(guild_id) is asyncio.current_task():
                self._transition_tasks.pop(guild_id, None)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = payload.player
        if player is None:
            return
        guild_id = player.guild.id if player.guild else None
        if guild_id is None or guild_id in self._transitioning:
            return
        self.cancel_transition(player)
        self._transition_tasks[guild_id] = asyncio.create_task(
            self.schedule_transition(player, payload.track)
        )

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player
        if player is None or player.current is not payload.track or player.playing:
            return
        guild_id = player.guild.id if player.guild else None
        if guild_id in self._transitioning:
            return
        if len(player.queue) > 0:
            next_track = player.queue.get()
            await player.play(next_track, start=TRANSITION_START_MS)
            await self.update_voice_status(player, next_track)
        else:
            await self.update_voice_status(player, None)

    @commands.group(name="playlist", invoke_without_command=True)
    async def playlist_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(
            "Use `!playlist create`, `!playlist list`, `!playlist search`, `!playlist add`, `!playlist import`, `!playlist delete`, or `!playlist like`."
        )

    @playlist_prefix.command(name="create")
    async def playlist_create_prefix(
        self, ctx: commands.Context, name: str, *, description: str = ""
    ) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        playlist = await playlists.create(ctx.author.id, None, name, description)
        if playlist.get("id") is None:
            await ctx.send("I could not save that playlist because playlist storage is unavailable.")
            return
        await ctx.send(embed=self.playlist_embed(playlist, "Playlist created ✅"))

    @playlist_prefix.command(name="list")
    async def playlist_list_prefix(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        rows = await playlists.list(ctx.author.id)
        if not rows:
            await ctx.send("No playlists yet. Use `!playlist create <name>`.")
            return
        await ctx.send(
            "**Your playlists**\n"
            + "\n".join(f"**{row['name']}**" for row in rows[:15])
        )

    @playlist_prefix.command(name="search")
    async def playlist_search_prefix(self, ctx: commands.Context, *, query: str) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        rows = await playlists.search(ctx.author.id, query)
        if not rows:
            await ctx.send("No matching playlists found.")
            return
        await ctx.send("**Matching playlists**\n" + "\n".join(f"**{row['name']}**" for row in rows))

    @playlist_prefix.command(name="add")
    async def playlist_add_prefix(
        self, ctx: commands.Context, playlist_name: str, *, query: str
    ) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        playlist = await playlists.find_by_name(playlist_name, ctx.author.id)
        if playlist is None:
            await ctx.send("Playlist not found.")
            return
        tracks = await self.search_tracks(query)
        if len(tracks) != 1:
            await ctx.send("Use a direct track URL or `/music playlist add` to choose from multiple results.")
            return
        track = tracks[0]
        if not track.uri:
            await ctx.send("That result has no saveable source URL.")
            return
        await playlists.add_track(playlist["id"], ctx.author.id, track.title, track.uri, track.length)
        await ctx.send(f"Added **{track.title}** to **{playlist['name']}**.")

    @playlist_prefix.command(name="import")
    async def playlist_import_prefix(
        self,
        ctx: commands.Context,
        service: str,
        url: str,
        name: str | None = None,
        *,
        description: str = "",
    ) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        member = cast(discord.Member, ctx.author)
        if service.lower() == "spotify":
            await ctx.send(await self.import_lavalink_playlist(member, url, name, description))
            return
        await ctx.send(await self.import_youtube_playlist(member, url, name, description))

    @playlist_prefix.command(name="like")
    async def playlist_like_prefix(self, ctx: commands.Context, playlist_name: str) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        playlist = await playlists.find_by_name(playlist_name, ctx.author.id)
        if playlist is None:
            await ctx.send("Playlist not found.")
            return
        await playlists.like(playlist["id"], ctx.author.id)
        await ctx.send(f"Liked **{playlist['name']}**.")

    @playlist_prefix.command(name="delete", aliases=["remove"])
    async def playlist_delete_prefix(self, ctx: commands.Context, playlist_name: str) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        playlist = await playlists.find_by_name(playlist_name, ctx.author.id)
        if playlist is None or not await playlists.delete(playlist["id"], ctx.author.id):
            await ctx.send("Playlist not found in your library.")
            return
        await ctx.send(f"Deleted **{playlist_name}**.")

    @commands.command(name="musicpanel")
    async def music_panel_prefix(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return
        await ctx.send(
            embed=discord.Embed(
                title="Music control panel",
                description="Search for music, inspect playback, or manage the queue with the buttons below.",
                color=discord.Color.blurple(),
            ),
            view=MusicPanel(self, ctx.author),
        )

    @commands.command(name="play", aliases=["p"])
    async def play_prefix(self, ctx: commands.Context, *, query: str = "") -> None:
        if not query.strip():
            await ctx.send("Give me a song name, URL, or playlist URL.")
            return
        tracks = await self.search_tracks(query)
        if not tracks:
            await ctx.send("I could not find anything for that search.")
            return
        if is_url(query) or len(tracks) == 1:
            await ctx.send(await self.queue_tracks(cast(discord.Member, ctx.author), tracks))
            return
        view = TrackSelection(self, cast(discord.Member, ctx.author), tracks[:5])
        await ctx.send("I found several matches. Choose one before I add anything:", view=view)

    @commands.command(name="search", aliases=["find"])
    async def search_prefix(self, ctx: commands.Context, *, query: str = "") -> None:
        await ctx.send(await self.search_text(query) if query.strip() else "Give me something to search for.")

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.current(cast(discord.Member, ctx.author)))

    @commands.command(name="queue", aliases=["q"])
    async def queue_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.queue_text(cast(discord.Member, ctx.author)))

    @commands.command(name="skip", aliases=["next"])
    async def skip_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.skip(cast(discord.Member, ctx.author)))

    @commands.command(name="stop")
    async def stop_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.stop(cast(discord.Member, ctx.author)))

    @commands.command(name="pause")
    async def pause_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.pause(cast(discord.Member, ctx.author), True))

    @commands.command(name="resume")
    async def resume_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.pause(cast(discord.Member, ctx.author), False))

    @commands.command(name="volume", aliases=["vol"])
    async def volume_prefix(self, ctx: commands.Context, level: int) -> None:
        await ctx.send(await self.volume(cast(discord.Member, ctx.author), level))

    @commands.command(name="loop")
    async def loop_prefix(self, ctx: commands.Context, mode: str = "off") -> None:
        await ctx.send(await self.set_loop(cast(discord.Member, ctx.author), mode.lower()) if mode.lower() in LOOP_MODES else "Use `off`, `track`, or `queue`.")

    @commands.command(name="shuffle")
    async def shuffle_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.shuffle(cast(discord.Member, ctx.author)))

    @commands.command(name="remove", aliases=["rm"])
    async def remove_prefix(self, ctx: commands.Context, position: int) -> None:
        await ctx.send(await self.remove(cast(discord.Member, ctx.author), position))

    @commands.command(name="clear")
    async def clear_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.clear(cast(discord.Member, ctx.author)))

    @commands.command(name="seek")
    async def seek_prefix(self, ctx: commands.Context, seconds: int) -> None:
        await ctx.send(await self.seek(cast(discord.Member, ctx.author), seconds))

    @commands.command(name="replay", aliases=["restart"])
    async def replay_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.replay(cast(discord.Member, ctx.author)))

    @app_commands.command(name="play", description="Play a song, URL, or playlist")
    @app_commands.describe(query="Song name, URL, or playlist URL")
    async def play_slash(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()
        if not query.strip():
            await interaction.followup.send("Give me a song name, URL, or playlist URL.", ephemeral=True)
            return
        tracks = await self.search_tracks(query)
        if not tracks:
            await interaction.followup.send("I could not find anything for that search.", ephemeral=True)
            return
        if is_url(query) or len(tracks) == 1:
            await interaction.followup.send(await self.queue_tracks(interaction_member(interaction), tracks))
            return
        view = TrackSelection(self, interaction_member(interaction), tracks[:5])
        await interaction.followup.send(
            "I found several matches. Choose one before I add anything:",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="search", description="Search Lavalink without playing")
    @app_commands.describe(query="Song or artist to search for")
    async def search_slash(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True)
        tracks = await self.search_tracks(query)
        if not tracks:
            await interaction.followup.send("No matches found.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=self.search_embed(query, tracks[:5]),
            view=TrackSelection(self, interaction_member(interaction), tracks[:5]),
            ephemeral=True,
        )

    @app_commands.command(name="nowplaying", description="Show the current track")
    async def nowplaying_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=await self.now_playing_embed(interaction_member(interaction)),
            view=self.now_playing_view(interaction_member(interaction)),
        )

    @app_commands.command(name="queue", description="Show the upcoming tracks")
    async def queue_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.queue_text(interaction_member(interaction)))

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.skip(interaction_member(interaction)))

    @app_commands.command(name="stop", description="Stop and clear playback")
    async def stop_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.stop(interaction_member(interaction)))

    @app_commands.command(name="pause", description="Pause playback")
    async def pause_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.pause(interaction_member(interaction), True))

    @app_commands.command(name="resume", description="Resume playback")
    async def resume_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.pause(interaction_member(interaction), False))

    @app_commands.command(name="volume", description="Set volume from 0 to 100")
    @app_commands.describe(level="Volume percentage")
    async def volume_slash(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 100]) -> None:
        await interaction.response.send_message(await self.volume(interaction_member(interaction), level))

    @app_commands.command(name="loop", description="Set loop mode")
    @app_commands.describe(mode="Loop mode")
    @app_commands.choices(mode=[app_commands.Choice(name=name, value=name) for name in LOOP_MODES])
    async def loop_slash(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        await interaction.response.send_message(await self.set_loop(interaction_member(interaction), mode.value))

    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.shuffle(interaction_member(interaction)))

    @app_commands.command(name="remove", description="Remove a track from the queue")
    @app_commands.describe(position="Queue position starting at 1")
    async def remove_slash(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 1000]) -> None:
        await interaction.response.send_message(await self.remove(interaction_member(interaction), position))

    @app_commands.command(name="clear", description="Clear the upcoming queue")
    async def clear_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.clear(interaction_member(interaction)))

    @app_commands.command(name="seek", description="Seek to a position in seconds")
    @app_commands.describe(seconds="Position in the current track")
    async def seek_slash(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 86400]) -> None:
        await interaction.response.send_message(await self.seek(interaction_member(interaction), seconds))

    @app_commands.command(name="replay", description="Restart the current track")
    async def replay_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.replay(interaction_member(interaction)))

    @playlist.command(name="create", description="Create a personal playlist")
    @app_commands.describe(name="Playlist name", description="Optional playlist description")
    async def playlist_create(
        self, interaction: discord.Interaction, name: str, description: str = ""
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        playlist = await playlists.create(interaction.user.id, None, name, description)
        await interaction.followup.send(
            embed=self.playlist_embed(playlist, "Playlist created ✅"),
            ephemeral=True,
        )

    @playlist.command(name="list", description="Browse your playlists")
    async def playlist_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rows = await playlists.list(interaction.user.id)
        embed = discord.Embed(title="Your playlists", color=discord.Color.blurple())
        if not rows:
            embed.description = "No playlists yet. Create one with `/music playlist create`."
        else:
            embed.description = "\n".join(
                f"`{row['id']}` **{row['name']}**\n{row.get('description', '') or 'No description.'}"
                for row in rows[:15]
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @playlist.command(name="search", description="Search your playlists by name")
    @app_commands.describe(query="Part of a playlist name")
    async def playlist_search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True)
        rows = await playlists.search(interaction.user.id, query)
        embed = discord.Embed(title="Playlist search", color=discord.Color.blurple())
        embed.description = (
            "No matching playlists found."
            if not rows
            else "\n".join(f"**{row['name']}**\n{row.get('description', '') or 'No description.'}" for row in rows[:15])
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @playlist.command(name="add", description="Add a direct track URL to a playlist")
    @app_commands.describe(playlist_name="Your playlist name", query="Song title, artist, or URL")
    async def playlist_add(self, interaction: discord.Interaction, playlist_name: str, query: str) -> None:
        await interaction.response.defer(ephemeral=True)
        playlist = await playlists.find_by_name(playlist_name, interaction.user.id)
        if playlist is None:
            await interaction.followup.send("Playlist not found.", ephemeral=True)
            return
        tracks = await self.search_tracks(query)
        if not tracks:
            await interaction.followup.send("No songs found for that search.", ephemeral=True)
            return
        if len(tracks) == 1:
            track = tracks[0]
            if not track.uri:
                await interaction.followup.send("That result has no saveable source URL.", ephemeral=True)
                return
            await playlists.add_track(playlist["id"], interaction.user.id, track.title, track.uri, track.length)
            await interaction.followup.send(
                embed=self.playlist_track_embed(playlist, track, "✅ Song added"), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=self.search_embed(query, tracks[:5]),
            view=PlaylistTrackSelection(self, interaction_member(interaction), playlist, tracks[:5]),
            ephemeral=True,
        )

    @playlist.command(name="import", description="Import a playlist from a connected service")
    @app_commands.describe(
        service="The service to import from",
        url="A Spotify or YouTube playlist URL",
        name="Optional name for the imported playlist",
        description="Optional description for the imported playlist",
    )
    @app_commands.choices(
        service=[
            app_commands.Choice(name="Spotify", value="spotify"),
            app_commands.Choice(name="YouTube", value="youtube"),
        ]
    )
    async def playlist_import(
        self,
        interaction: discord.Interaction,
        service: app_commands.Choice[str],
        url: str | None = None,
        name: str | None = None,
        description: str = "",
    ) -> None:
        if service.value == "spotify":
            if not url:
                await interaction.response.send_message(
                    "Provide a Spotify playlist URL when importing from Spotify.", ephemeral=True
                )
                return
            await interaction.response.defer()
            await interaction.followup.send(
                await self.import_lavalink_playlist(
                    interaction_member(interaction), url, name, description
                )
            )
            return
        await self.playlist_import_youtube(interaction, url, name, description)

    async def import_youtube_playlist(
        self,
        member: discord.Member,
        url: str | None,
        name: str | None = None,
        description: str = "",
    ) -> str:
        if not url:
            return "Provide a YouTube playlist URL when importing from YouTube."
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
            or "list=" not in parsed.query
        ):
            return "Enter a valid YouTube playlist URL."
        try:
            result = await asyncio.wait_for(wavelink.Playable.search(url), timeout=12)
            if not isinstance(result, wavelink.Playlist):
                return "Lavalink did not return a playlist for that URL."
            tracks = [track for track in result.tracks if track is not None]
            if not tracks:
                return "No playable tracks were found in that playlist."
            return await self.save_imported_playlist(member, result, tracks, name, description)
        except (ValueError, wavelink.LavalinkException, asyncio.TimeoutError) as error:
            print(f"YouTube playlist import failed: {error}")
            return "I could not load that YouTube playlist."

    async def playlist_import_youtube(
        self,
        interaction: discord.Interaction,
        url: str | None,
        name: str | None = None,
        description: str = "",
    ) -> None:
        await interaction.response.defer()
        await interaction.followup.send(
            await self.import_youtube_playlist(
                interaction_member(interaction), url, name, description
            ),
            ephemeral=True,
        )

    @playlist.command(name="like", description="Like a playlist")
    @app_commands.describe(playlist_name="Your playlist name")
    async def playlist_like(self, interaction: discord.Interaction, playlist_name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        playlist = await playlists.find_by_name(playlist_name, interaction.user.id)
        if playlist is None:
            await interaction.followup.send("Playlist not found.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=self.playlist_embed(playlist, "Playlist ready to like 💜"),
            view=PlaylistLikeView(playlist, interaction_member(interaction)),
            ephemeral=True,
        )

    @playlist.command(name="delete", description="Delete one of your playlists")
    @app_commands.describe(playlist_name="Your playlist name")
    async def playlist_delete(self, interaction: discord.Interaction, playlist_name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        playlist = await playlists.find_by_name(playlist_name, interaction.user.id)
        if playlist is None or not await playlists.delete(playlist["id"], interaction.user.id):
            await interaction.followup.send(
                "Playlist not found or it does not belong to you.", ephemeral=True
            )
            return
        await interaction.followup.send("Playlist deleted.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
