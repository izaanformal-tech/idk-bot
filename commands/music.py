import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from commands.voice import get_player


LOOP_MODES = {
    "off": wavelink.QueueMode.normal,
    "track": wavelink.QueueMode.loop,
    "queue": wavelink.QueueMode.loop_all,
}


def format_duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "live"
    total_seconds = max(milliseconds, 0) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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

        tracks = await self.search_tracks(query)
        if not tracks:
            return "I could not find anything for that search."

        for track in tracks:
            await player.queue.put_wait(track)
        if not player.playing:
            first = await player.queue.get_wait()
            await player.play(first)
            added = len(tracks) - 1
            suffix = f" Added {added} more tracks to the queue." if added else ""
            return f"Now playing **{first.title}**.{suffix}"
        return f"Queued **{len(tracks)} track(s)**."

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
            await player.play(await player.queue.get_wait())

    @commands.command(name="play", aliases=["p"])
    async def play_prefix(self, ctx: commands.Context, *, query: str = "") -> None:
        await ctx.send(await self.play_query(ctx.author, query))

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
        await interaction.response.send_message(await self.play_query(interaction.user, query))

    @app_commands.command(name="search", description="Search Lavalink without playing")
    @app_commands.describe(query="Song or artist to search for")
    async def search_slash(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.send_message(await self.search_text(query))

    @app_commands.command(name="nowplaying", description="Show the current track")
    async def nowplaying_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.current(interaction.user))

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
