import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from urllib.parse import urlparse

from commands.voice import get_player
from db import playlists, preferences


LOOP_MODES = {
    "off": wavelink.QueueMode.normal,
    "track": wavelink.QueueMode.loop,
    "queue": wavelink.QueueMode.loop_all,
}


class TrackSelection(discord.ui.View):
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
        track = self.tracks[int(interaction.data["values"][0])]
        message = await self.cog.queue_tracks(self.member, [track])
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=message, view=self)
        self.stop()


class PlaylistTrackSelection(discord.ui.View):
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
        track = self.tracks[int(interaction.data["values"][0])]
        await playlists.add_track(
            self.playlist["id"],
            self.member.id,
            track.title,
            track.uri,
            track.length,
        )
        for child in self.children:
            child.disabled = True
        embed = self.cog.playlist_track_embed(self.playlist, track, "✅ Song added")
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class PlaylistLikeView(discord.ui.View):
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


class SearchModal(discord.ui.Modal, title="Search music"):
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
        tracks = await self.cog.search_tracks(str(self.query))
        if not tracks:
            await interaction.response.send_message("No matches found.", ephemeral=True)
            return
        if is_url(str(self.query)) or len(tracks) == 1:
            await interaction.response.send_message(
                await self.cog.queue_tracks(self.member, tracks), ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Choose a result before adding it to the queue.",
            embed=self.cog.search_embed(str(self.query), tracks[:5]),
            view=TrackSelection(self.cog, self.member, tracks[:5]),
            ephemeral=True,
        )


class MusicPanel(discord.ui.View):
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
        player = self.member.guild.voice_client
        paused = bool(player and player.paused)
        await interaction.response.send_message(await self.cog.pause(self.member, not paused), ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.skip(self.member), ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, emoji="📜", row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.queue_text(self.member), ephemeral=True)


def is_url(query: str) -> bool:
    return urlparse(query).scheme in {"http", "https"}


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

    playlist = app_commands.Group(name="playlist", description="Create and share playlists")

    @staticmethod
    async def search_tracks(query: str) -> list[wavelink.Playable]:
        result = await wavelink.Playable.search(query.strip())
        if isinstance(result, wavelink.Playlist):
            return list(result.tracks)
        return list(result)

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
        player = await get_player(member)
        if player is None:
            return "Join a voice channel first."

        for track in tracks:
            await player.queue.put_wait(track)
        if not player.playing:
            first = await player.queue.get_wait()
            await player.play(first)
            await self.update_presence(first)
            added = len(tracks) - 1
            suffix = f" Added {added} more tracks to the queue." if added else ""
            return f"Now playing **{first.title}**.{suffix}"
        return f"Queued **{len(tracks)} track(s)**."

    async def update_presence(self, track: wavelink.Playable | None) -> None:
        activity = (
            discord.Activity(type=discord.ActivityType.listening, name=track.title)
            if track
            else None
        )
        await self.bot.change_presence(activity=activity)

    def now_playing_view(self, member: discord.Member) -> discord.ui.View:
        view = discord.ui.View(timeout=300)
        track = member.guild.voice_client.current if member.guild.voice_client else None
        if track and track.uri:
            view.add_item(discord.ui.Button(label="▶️ Play source", style=discord.ButtonStyle.link, url=track.uri))
        return view

    async def now_playing_embed(self, member: discord.Member) -> discord.Embed:
        player = member.guild.voice_client
        if player is None or player.current is None:
            return discord.Embed(title="Nothing is playing", description="Start music with `/music panel`.", color=discord.Color.dark_grey())
        track = player.current
        embed = discord.Embed(
            title="▶️ Now playing",
            description=f"[{track.title}]({track.uri})" if track.uri else track.title,
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Artist", value=track.author or "Unknown", inline=True)
        embed.add_field(name="Duration", value=f"{format_duration(player.position)} / {format_duration(track.length)}", inline=True)
        embed.add_field(name="Queue", value=str(len(player.queue)), inline=True)
        artwork = getattr(track, "artwork", None)
        if artwork:
            embed.set_thumbnail(url=artwork)
        embed.set_footer(text="IDK Music • use the panel buttons to control playback")
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
        embed.set_footer(text="IDK Music • saved to your server playlist")
        return embed

    @staticmethod
    def playlist_embed(playlist: dict, title: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"📚 {title}",
            description=f"**{playlist['name']}**\n{playlist.get('description', '') or 'No description.'}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Playlist ID", value=f"`{playlist['id']}`", inline=True)
        embed.add_field(name="Share", value="Use `/music playlist list`", inline=True)
        embed.set_footer(text="IDK Music • playlist community features")
        return embed

    @app_commands.command(name="panel", description="Open your music control panel")
    async def panel_slash(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Music control panel",
            description="Search for music, inspect playback, or manage the queue with the buttons below.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=MusicPanel(self, interaction.user), ephemeral=True)

    async def current(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None or player.current is None:
            return "Nothing is playing."
        track = player.current
        return f"Now playing **{track.title}** (`{format_duration(player.position)} / {format_duration(track.length)}`)."

    async def queue_text(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None or len(player.queue) == 0:
            return "The queue is empty."
        tracks = list(player.queue)[:15]
        lines = [f"{index}. {track.title}" for index, track in enumerate(tracks, start=1)]
        remaining = len(player.queue) - len(tracks)
        if remaining:
            lines.append(f"...and {remaining} more.")
        return "**Queue**\n" + "\n".join(lines)

    async def skip(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None or not player.playing:
            return "Nothing is playing."
        await player.skip()
        return "Skipped the current track."

    async def stop(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None:
            return "I am not connected to a voice channel."
        player.queue.clear()
        await player.stop()
        await self.update_presence(None)
        return "Stopped playback and cleared the queue."

    async def pause(self, member: discord.Member, paused: bool) -> str:
        player = member.guild.voice_client
        if player is None or not player.playing:
            return "Nothing is playing."
        await player.pause(paused)
        return "Paused playback." if paused else "Resumed playback."

    async def set_loop(self, member: discord.Member, mode: str) -> str:
        player = member.guild.voice_client
        if player is None:
            return "Join a voice channel first."
        player.queue.mode = LOOP_MODES[mode]
        await preferences.update(member.id, member.guild.id, loop_mode=mode)
        labels = {"off": "disabled", "track": "set to the current track", "queue": "set to the queue"}
        return f"Looping {labels[mode]}."

    async def shuffle(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None or len(player.queue) < 2:
            return "Add at least two tracks before shuffling."
        player.queue.shuffle()
        return "Shuffled the queue."

    async def remove(self, member: discord.Member, position: int) -> str:
        player = member.guild.voice_client
        tracks = list(player.queue) if player else []
        if position < 1 or position > len(tracks):
            return "That queue position does not exist."
        removed = tracks.pop(position - 1)
        player.queue.clear()
        for track in tracks:
            await player.queue.put_wait(track)
        return f"Removed **{removed.title}** from the queue."

    async def clear(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None or len(player.queue) == 0:
            return "The queue is already empty."
        player.queue.clear()
        return "Cleared the queue."

    async def volume(self, member: discord.Member, level: int) -> str:
        player = member.guild.voice_client
        if player is None:
            return "Join a voice channel first."
        await player.set_volume(level)
        await preferences.update(member.id, member.guild.id, default_volume=level)
        return f"Volume set to **{level}%**."

    async def seek(self, member: discord.Member, seconds: int) -> str:
        player = member.guild.voice_client
        if player is None or player.current is None:
            return "Nothing is playing."
        if seconds < 0 or (
            player.current.length is not None and seconds * 1000 > player.current.length
        ):
            return "That position is outside the current track."
        await player.seek(seconds * 1000)
        return f"Seeked to **{format_duration(seconds * 1000)}**."

    async def replay(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None or player.current is None:
            return "Nothing is playing."
        await player.seek(0)
        return "Restarted the current track."

    async def search_text(self, query: str) -> str:
        tracks = await self.search_tracks(query)
        if not tracks:
            return "I could not find anything for that search."
        lines = [f"{index}. {track.title}" for index, track in enumerate(tracks[:5], start=1)]
        return "**Search results**\n" + "\n".join(lines)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player
        if player is not None and not player.playing and len(player.queue) > 0:
            next_track = await player.queue.get_wait()
            await player.play(next_track)
            await self.update_presence(next_track)
        elif player is not None and not player.playing:
            await self.update_presence(None)

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
            await ctx.send(await self.queue_tracks(ctx.author, tracks))
            return
        view = TrackSelection(self, ctx.author, tracks[:5])
        await ctx.send("I found several matches. Choose one before I add anything:", view=view)

    @commands.command(name="search", aliases=["find"])
    async def search_prefix(self, ctx: commands.Context, *, query: str = "") -> None:
        await ctx.send(await self.search_text(query) if query.strip() else "Give me something to search for.")

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.current(ctx.author))

    @commands.command(name="queue", aliases=["q"])
    async def queue_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.queue_text(ctx.author))

    @commands.command(name="skip", aliases=["next"])
    async def skip_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.skip(ctx.author))

    @commands.command(name="stop")
    async def stop_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.stop(ctx.author))

    @commands.command(name="pause")
    async def pause_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.pause(ctx.author, True))

    @commands.command(name="resume")
    async def resume_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.pause(ctx.author, False))

    @commands.command(name="volume", aliases=["vol"])
    async def volume_prefix(self, ctx: commands.Context, level: int) -> None:
        await ctx.send(await self.volume(ctx.author, level))

    @commands.command(name="loop")
    async def loop_prefix(self, ctx: commands.Context, mode: str = "off") -> None:
        await ctx.send(await self.set_loop(ctx.author, mode.lower()) if mode.lower() in LOOP_MODES else "Use `off`, `track`, or `queue`.")

    @commands.command(name="shuffle")
    async def shuffle_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.shuffle(ctx.author))

    @commands.command(name="remove", aliases=["rm"])
    async def remove_prefix(self, ctx: commands.Context, position: int) -> None:
        await ctx.send(await self.remove(ctx.author, position))

    @commands.command(name="clear")
    async def clear_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.clear(ctx.author))

    @commands.command(name="seek")
    async def seek_prefix(self, ctx: commands.Context, seconds: int) -> None:
        await ctx.send(await self.seek(ctx.author, seconds))

    @commands.command(name="replay", aliases=["restart"])
    async def replay_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.replay(ctx.author))

    @app_commands.command(name="play", description="Play a song, URL, or playlist")
    @app_commands.describe(query="Song name, URL, or playlist URL")
    async def play_slash(self, interaction: discord.Interaction, query: str) -> None:
        if not query.strip():
            await interaction.response.send_message("Give me a song name, URL, or playlist URL.", ephemeral=True)
            return
        tracks = await self.search_tracks(query)
        if not tracks:
            await interaction.response.send_message("I could not find anything for that search.", ephemeral=True)
            return
        if is_url(query) or len(tracks) == 1:
            await interaction.response.send_message(await self.queue_tracks(interaction.user, tracks))
            return
        view = TrackSelection(self, interaction.user, tracks[:5])
        await interaction.response.send_message(
            "I found several matches. Choose one before I add anything:",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="search", description="Search Lavalink without playing")
    @app_commands.describe(query="Song or artist to search for")
    async def search_slash(self, interaction: discord.Interaction, query: str) -> None:
        tracks = await self.search_tracks(query)
        if not tracks:
            await interaction.response.send_message("No matches found.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self.search_embed(query, tracks[:5]),
            view=TrackSelection(self, interaction.user, tracks[:5]),
            ephemeral=True,
        )

    @app_commands.command(name="nowplaying", description="Show the current track")
    async def nowplaying_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=await self.now_playing_embed(interaction.user),
            view=self.now_playing_view(interaction.user),
        )

    @app_commands.command(name="queue", description="Show the upcoming tracks")
    async def queue_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.queue_text(interaction.user))

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.skip(interaction.user))

    @app_commands.command(name="stop", description="Stop and clear playback")
    async def stop_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.stop(interaction.user))

    @app_commands.command(name="pause", description="Pause playback")
    async def pause_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.pause(interaction.user, True))

    @app_commands.command(name="resume", description="Resume playback")
    async def resume_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.pause(interaction.user, False))

    @app_commands.command(name="volume", description="Set volume from 0 to 100")
    @app_commands.describe(level="Volume percentage")
    async def volume_slash(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 100]) -> None:
        await interaction.response.send_message(await self.volume(interaction.user, level))

    @app_commands.command(name="loop", description="Set loop mode")
    @app_commands.describe(mode="Loop mode")
    @app_commands.choices(mode=[app_commands.Choice(name=name, value=name) for name in LOOP_MODES])
    async def loop_slash(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        await interaction.response.send_message(await self.set_loop(interaction.user, mode.value))

    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.shuffle(interaction.user))

    @app_commands.command(name="remove", description="Remove a track from the queue")
    @app_commands.describe(position="Queue position starting at 1")
    async def remove_slash(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 1000]) -> None:
        await interaction.response.send_message(await self.remove(interaction.user, position))

    @app_commands.command(name="clear", description="Clear the upcoming queue")
    async def clear_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.clear(interaction.user))

    @app_commands.command(name="seek", description="Seek to a position in seconds")
    @app_commands.describe(seconds="Position in the current track")
    async def seek_slash(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 86400]) -> None:
        await interaction.response.send_message(await self.seek(interaction.user, seconds))

    @app_commands.command(name="replay", description="Restart the current track")
    async def replay_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.replay(interaction.user))

    @playlist.command(name="create", description="Create a personal server playlist")
    @app_commands.describe(name="Playlist name", description="Optional playlist description")
    async def playlist_create(
        self, interaction: discord.Interaction, name: str, description: str = ""
    ) -> None:
        playlist = await playlists.create(interaction.user.id, interaction.guild_id, name, description)
        await interaction.response.send_message(
            embed=self.playlist_embed(playlist, "Playlist created ✅"),
            ephemeral=True,
        )

    @playlist.command(name="list", description="Browse playlists in this server")
    async def playlist_list(self, interaction: discord.Interaction) -> None:
        rows = await playlists.list(interaction.guild_id)
        embed = discord.Embed(title="Server playlists", color=discord.Color.blurple())
        if not rows:
            embed.description = "No playlists yet. Create one with `/music playlist create`."
        else:
            embed.description = "\n".join(
                f"`{row['id']}` **{row['name']}**\n{row.get('description', '') or 'No description.'}"
                for row in rows[:15]
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @playlist.command(name="add", description="Add a direct track URL to a playlist")
    @app_commands.describe(playlist_id="ID shown by playlist list", query="Song title, artist, or URL")
    async def playlist_add(self, interaction: discord.Interaction, playlist_id: int, query: str) -> None:
        playlist = await playlists.find(playlist_id, interaction.guild_id)
        if playlist is None:
            await interaction.response.send_message("Playlist not found.", ephemeral=True)
            return
        tracks = await self.search_tracks(query)
        if not tracks:
            await interaction.response.send_message("No songs found for that search.", ephemeral=True)
            return
        if len(tracks) == 1:
            track = tracks[0]
            await playlists.add_track(playlist_id, interaction.user.id, track.title, track.uri, track.length)
            await interaction.response.send_message(
                embed=self.playlist_track_embed(playlist, track, "✅ Song added"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=self.search_embed(query, tracks[:5]),
            view=PlaylistTrackSelection(self, interaction.user, playlist, tracks[:5]),
            ephemeral=True,
        )

    @playlist.command(name="like", description="Like a playlist")
    @app_commands.describe(playlist_id="ID shown by playlist list")
    async def playlist_like(self, interaction: discord.Interaction, playlist_id: int) -> None:
        playlist = await playlists.find(playlist_id, interaction.guild_id)
        if playlist is None:
            await interaction.response.send_message("Playlist not found.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self.playlist_embed(playlist, "Playlist ready to like 💜"),
            view=PlaylistLikeView(playlist, interaction.user),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
