import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from typing import Any, cast

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
    if not isinstance(player, wavelink.Player):
        raise PermissionError("I am in a server call right now. Use `/vc hangup` before playing music.")
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
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.cog.connect_message(self.member, None, interaction.channel), ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.guarded(self.member, call_bridge.skip(self.member.guild.id)), ephemeral=True)

    @discord.ui.button(label="Mute / unmute", style=discord.ButtonStyle.secondary, emoji="🔇")
    async def mute_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        connection = call_bridge.get(self.member.guild.id)
        muted = bool(connection and connection.muted)
        await interaction.response.send_message(call_bridge.set_muted(self.member.guild.id, not muted), ephemeral=True)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, emoji="📡")
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=await self.cog.status_embed(self.member), ephemeral=True)

    @discord.ui.button(label="Hang up", style=discord.ButtonStyle.danger, emoji="📴")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(await self.cog.disconnect_message(self.member), ephemeral=True)


class VoiceStatusView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)


@app_commands.guild_only()
class Voice(commands.GroupCog, group_name="vc"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        # Forget the call if the bot was kicked or the channel was deleted.
        if self.bot.user is not None and member.id == self.bot.user.id and after.channel is None:
            if call_bridge.get(member.guild.id) is not None:
                await call_bridge.disconnect(member.guild.id)

    @staticmethod
    def voice_channel(member: discord.Member) -> discord.VoiceChannel | discord.StageChannel | None:
        return member.voice.channel if member.voice else None

    async def connect_message(
        self,
        member: discord.Member,
        channel: discord.VoiceChannel | discord.StageChannel | None = None,
        text_channel: Any = None,
    ) -> str:
        return await call_bridge.connect(member, channel, text_channel)

    @staticmethod
    def can_control(member: discord.Member) -> bool:
        """Call controls are for people in the call's voice channel, or moderators."""
        connection = call_bridge.get(member.guild.id)
        if connection is None:
            return True
        in_channel = member.voice is not None and member.voice.channel == connection.voice_client.channel
        return in_channel or member.guild_permissions.move_members or member.guild_permissions.manage_guild

    async def guarded(self, member: discord.Member, action) -> str:
        if not self.can_control(member):
            if hasattr(action, "close"):
                action.close()
            return "Join the call's voice channel to use call controls."
        return await action

    async def disconnect_message(self, member: discord.Member) -> str:
        voice_client = member.guild.voice_client
        channel = getattr(voice_client, "channel", None)
        if channel is not None:
            try:
                await cast(Any, channel.edit)(
                    status=None,
                    reason="Clear voice channel status when leaving",
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        if call_bridge.get(member.guild.id) is not None:
            return await call_bridge.disconnect(member.guild.id)
        if voice_client is not None:
            await voice_client.disconnect(force=True)
            return "Left the voice channel."
        return "I am not connected to a voice channel."

    async def status_message(self, member: discord.Member) -> str:
        info = call_bridge.status(member.guild.id)
        if info is None:
            return "Call status: not in a call."
        if info["partner"]:
            return f"Call status: talking with **{info['partner']}** in **{info['channel']}**."
        return f"Call status: waiting for another server in **{info['channel']}**."

    async def status_embed(self, member: discord.Member) -> discord.Embed:
        info = call_bridge.status(member.guild.id)
        embed = discord.Embed(title="Server call", color=discord.Color.blurple())
        if info is None:
            embed.description = "Not in a call. Use `/vc call` to be matched with a random server."
            return embed
        embed.add_field(name="Channel", value=info["channel"], inline=True)
        embed.add_field(name="Talking with", value=info["partner"] or "Waiting for a server...", inline=True)
        if info["since"]:
            embed.add_field(name="Connected", value=f"<t:{int(info['since'])}:R>", inline=True)
        embed.add_field(name="Incoming audio", value="Muted" if info["muted"] else f"{info['volume']}%", inline=True)
        if info["hangup_at"]:
            embed.add_field(name="Auto hang-up", value=f"<t:{int(info['hangup_at'])}:R>", inline=True)
        embed.set_footer(text="/vc skip for someone new • /vc report if anything's wrong")
        return embed

    @commands.command(name="call", aliases=["join"])
    async def call_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.connect_message(ctx.author, None, ctx.channel))

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

    @app_commands.command(name="call", description="Get matched with a random server for a voice call")
    @app_commands.describe(channel="Optional voice channel to join")
    async def call_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self.connect_message(interaction.user, channel, interaction.channel))

    @app_commands.command(name="join", description="Join or move to a voice channel")
    @app_commands.describe(channel="Optional voice channel to join")
    async def join_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self.connect_message(interaction.user, channel, interaction.channel))

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


    @app_commands.command(name="skip", description="Hang up on this server and get matched with a new one")
    async def skip_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self.guarded(interaction.user, call_bridge.skip(interaction.guild_id)))

    @app_commands.command(name="report", description="Report the server you're talking to and never match with them again")
    @app_commands.describe(reason="What happened")
    async def report_slash(self, interaction: discord.Interaction, reason: app_commands.Range[str, 1, 300]) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            await self.guarded(interaction.user, call_bridge.report(interaction.guild_id, interaction.user.id, reason)),
            ephemeral=True,
        )

    @app_commands.command(name="block", description="Block the server you're talking to and move on")
    async def block_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.guarded(interaction.user, call_bridge.block(interaction.guild_id)), ephemeral=True)

    @app_commands.command(name="unblockall", description="Clear this server's list of blocked servers")
    @app_commands.default_permissions(manage_guild=True)
    async def unblockall_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(call_bridge.clear_blocks(interaction.guild_id), ephemeral=True)

    @app_commands.command(name="mute", description="Stop hearing the other server (they can still hear you)")
    async def mute_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(call_bridge.set_muted(interaction.guild_id, True))

    @app_commands.command(name="unmute", description="Hear the other server again")
    async def unmute_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(call_bridge.set_muted(interaction.guild_id, False))

    @app_commands.command(name="volume", description="Set how loud the other server is (0-200%)")
    @app_commands.describe(percent="100 is normal")
    async def volume_slash(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 200]) -> None:
        await interaction.response.send_message(call_bridge.set_volume(interaction.guild_id, percent))

    @app_commands.command(name="timer", description="Hang up automatically after some minutes (0 turns it off)")
    @app_commands.describe(minutes="Minutes until hang-up")
    async def timer_slash(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 0, 600]) -> None:
        await interaction.response.send_message(call_bridge.set_timer(interaction.guild_id, minutes))

    @commands.command(name="vcskip")
    async def skip_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(await self.guarded(ctx.author, call_bridge.skip(ctx.guild.id)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice(bot))
