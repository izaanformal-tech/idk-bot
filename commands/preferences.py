import discord
from discord import app_commands
from discord.ext import commands

from db import preferences


class Preferences(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def render(values) -> str:
        return (
            "**Your music preferences**\n"
            f"Volume: `{values.default_volume}%`\n"
            f"Loop: `{values.loop_mode}`\n"
            f"Autoplay: `{'on' if values.autoplay else 'off'}`\n"
            f"Now-playing announcements: `{'on' if values.announce_now_playing else 'off'}`\n"
            f"Preferred playlist: `{values.playlist_id or 'not set'}`"
        )

    @commands.command(name="settings", aliases=["prefs", "preferences"])
    async def settings_prefix(self, ctx: commands.Context) -> None:
        values = await preferences.get(ctx.author.id, ctx.guild.id)
        await ctx.send(self.render(values))

    @commands.command(name="setvolume")
    async def set_volume_prefix(self, ctx: commands.Context, level: int) -> None:
        if level < 0 or level > 100:
            await ctx.send("Volume must be between 0 and 100.")
            return
        values = await preferences.update(ctx.author.id, ctx.guild.id, default_volume=level)
        await ctx.send(f"Saved your default volume.\n{self.render(values)}")

    @commands.command(name="setloop")
    async def set_loop_prefix(self, ctx: commands.Context, mode: str) -> None:
        if mode not in {"off", "track", "queue"}:
            await ctx.send("Loop must be `off`, `track`, or `queue`.")
            return
        values = await preferences.update(ctx.author.id, ctx.guild.id, loop_mode=mode)
        await ctx.send(f"Saved your loop preference.\n{self.render(values)}")

    @app_commands.command(name="settings", description="Show or save your music preferences")
    async def settings_slash(
        self,
        interaction: discord.Interaction,
        volume: app_commands.Range[int, 0, 100] | None = None,
        loop: str | None = None,
        autoplay: bool | None = None,
        announcements: bool | None = None,
    ) -> None:
        changes = {}
        if volume is not None:
            changes["default_volume"] = volume
        if loop is not None:
            changes["loop_mode"] = loop
        if autoplay is not None:
            changes["autoplay"] = autoplay
        if announcements is not None:
            changes["announce_now_playing"] = announcements
        values = await preferences.update(interaction.user.id, interaction.guild_id, **changes)
        await interaction.response.send_message(self.render(values), ephemeral=True)

    @settings_slash.autocomplete("loop")
    async def loop_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=mode, value=mode)
            for mode in ("off", "track", "queue")
            if current.lower() in mode
        ]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Preferences(bot))
