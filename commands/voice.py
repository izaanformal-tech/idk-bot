import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from call_bridge import call_bridge


def format_duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "live"
    total_seconds = max(milliseconds, 0) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


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


class VoicePanel(discord.ui.View):
    def __init__(self, cog: "Voice", member: discord.Member) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.member = member

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Run `/vc panel` to get your own controls.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Call", style=discord.ButtonStyle.success, emoji="📞")
    async def call_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.connect_message(self.member), ephemeral=True)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.primary, emoji="📡")
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=await self.cog.status_embed(self.member), ephemeral=True)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger, emoji="📴")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.disconnect_message(self.member), ephemeral=True)


class VoiceStatusView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)


class Voice(commands.GroupCog, group_name="vc"):
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
        return await call_bridge.connect(member, channel)

    async def disconnect_message(self, member: discord.Member) -> str:
        message = await call_bridge.disconnect(member.guild.id)
        await self.bot.change_presence(activity=None)
        return message

    async def status_message(self, member: discord.Member) -> str:
        connection = call_bridge.get(member.guild.id)
        if connection is None:
            return "Call status: disconnected."
        channel_name = connection.voice_client.channel.name if connection.voice_client.channel else "unknown"
        return f"Call status: connected in **{channel_name}**."

    async def status_embed(self, member: discord.Member) -> discord.Embed:
        connection = call_bridge.get(member.guild.id)
        embed = discord.Embed(title="Server call", color=discord.Color.blurple())
        if connection is None:
            embed.description = "Disconnected. Use the Call button to start a server call."
            return embed
        channel_name = connection.voice_client.channel.name if connection.voice_client.channel else "unknown"
        embed.add_field(name="Channel", value=channel_name, inline=True)
        embed.add_field(name="Status", value="Connected", inline=True)
        embed.description = "Ready for a server call. Happy chatting!"
        embed.set_footer(text="Audio is bridged between connected server calls.")
        return embed

    @commands.command(name="call", aliases=["join"])
    async def call_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.connect_message(ctx.author))

    @commands.command(name="leave", aliases=["disconnect"])
    async def leave_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.disconnect_message(ctx.author))

    @commands.command(name="vcstatus", aliases=["voice"])
    async def status_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.status_message(ctx.author))

    @commands.command(name="vcpanel")
    async def panel_prefix(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member):
            return
        embed = discord.Embed(
            title="Voice control panel",
            description="Call the bot, inspect the server call status, or disconnect it.",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, view=VoicePanel(self, ctx.author))

    @app_commands.command(name="call", description="Call the bot into your voice channel")
    @app_commands.describe(channel="Optional voice channel to join")
    async def call_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self.connect_message(interaction.user, channel))

    @app_commands.command(name="join", description="Join or move to a voice channel")
    @app_commands.describe(channel="Optional voice channel to join")
    async def join_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self.connect_message(interaction.user, channel))

    @app_commands.command(name="leave", description="Leave the current voice channel")
    async def leave_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self.disconnect_message(interaction.user))

    @app_commands.command(name="hangup", description="End the voice call and leave the channel")
    async def hangup_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self.disconnect_message(interaction.user))

    @app_commands.command(name="vcstatus", description="Show the bot's voice connection status")
    async def status_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=await self.status_embed(interaction.user),
            view=VoiceStatusView(),
        )

    @app_commands.command(name="panel", description="Open voice connection controls")
    async def panel_slash(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Voice control panel",
            description="Call the bot, inspect the server call status, or disconnect it.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=VoicePanel(self, interaction.user), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice(bot))
