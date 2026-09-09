import discord
import wavelink
from discord import app_commands
from discord.ext import commands


async def get_player(
    member: discord.Member,
    channel: discord.VoiceChannel | discord.StageChannel | None = None,
) -> wavelink.Player | None:
    target = channel or (member.voice.channel if member.voice else None)
    if target is None:
        return None

    permissions = target.permissions_for(member.guild.me) if member.guild.me else None
    if permissions and (not permissions.connect or not permissions.speak):
        raise PermissionError("I need Connect and Speak permissions in that voice channel.")

    player = member.guild.voice_client
    if player is None:
        return await target.connect(cls=wavelink.Player, reconnect=True, self_deaf=True)
    if player.channel != target:
        await player.move_to(target)
    return player


class Voice(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def voice_channel(member: discord.Member) -> discord.VoiceChannel | discord.StageChannel | None:
        return member.voice.channel if member.voice else None

    async def connect_message(
        self,
        member: discord.Member,
        channel: discord.VoiceChannel | discord.StageChannel | None = None,
    ) -> str:
        target = channel or self.voice_channel(member)
        if target is None:
            return "Join a voice channel first, or choose a channel with the slash command."
        try:
            await get_player(member, target)
        except PermissionError as error:
            return str(error)
        return f"Connected to **{target.name}** with music playback ready."

    async def disconnect_message(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None:
            return "I am not connected to a voice channel."
        await player.disconnect()
        return "Disconnected from the voice channel."

    async def status_message(self, member: discord.Member) -> str:
        player = member.guild.voice_client
        if player is None:
            return "Voice status: disconnected."
        channel_name = player.channel.name if player.channel else "unknown"
        track = player.current.title if player.current else "nothing"
        state = "playing" if player.playing else "idle"
        return f"Voice status: **{state}** in **{channel_name}** | Track: **{track}**"

    @commands.command(name="call", aliases=["join"])
    async def call_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.connect_message(ctx.author))

    @commands.command(name="leave", aliases=["disconnect"])
    async def leave_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.disconnect_message(ctx.author))

    @commands.command(name="vcstatus", aliases=["voice"])
    async def status_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.status_message(ctx.author))

    @app_commands.command(name="call", description="Call the bot into your voice channel")
    @app_commands.describe(channel="Optional voice channel to join")
    async def call_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        await interaction.response.send_message(await self.connect_message(interaction.user, channel))

    @app_commands.command(name="join", description="Join or move to a voice channel")
    @app_commands.describe(channel="Optional voice channel to join")
    async def join_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        await interaction.response.send_message(await self.connect_message(interaction.user, channel))

    @app_commands.command(name="leave", description="Leave the current voice channel")
    async def leave_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.disconnect_message(interaction.user))

    @app_commands.command(name="vcstatus", description="Show the bot's voice connection status")
    async def status_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self.status_message(interaction.user))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice(bot))
