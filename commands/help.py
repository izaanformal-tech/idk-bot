import time

import discord
from discord import app_commands
from discord.ext import commands


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ping")
    async def ping_prefix(self, ctx: commands.Context) -> None:
        started = time.perf_counter()
        reply = await ctx.send("Pinging...")
        round_trip = round((time.perf_counter() - started) * 1000)
        gateway = round(self.bot.latency * 1000)
        await reply.edit(content=f"Pong! Gateway: `{gateway}ms` | Round-trip: `{round_trip}ms`")

    @commands.command(name="help")
    async def help_prefix(self, ctx: commands.Context) -> None:
        prefix = ctx.clean_prefix
        await ctx.send(
            f"**IDK Music Bot**\n"
            f"Music: `{prefix}play`, `{prefix}search`, `{prefix}queue`, `{prefix}nowplaying`, "
            f"`{prefix}skip`, `{prefix}pause`, `{prefix}resume`, `{prefix}stop`\n"
            f"Queue: `{prefix}shuffle`, `{prefix}remove`, `{prefix}clear`, `{prefix}loop`\n"
            f"Playback: `{prefix}volume`, `{prefix}seek`, `{prefix}replay`\n"
            f"Voice: `{prefix}call`, `{prefix}join`, `{prefix}leave`, `{prefix}vcstatus`\n"
            "Every command also has a slash-command version."
        )

    @app_commands.command(name="ping", description="Measure gateway and Discord round-trip latency")
    async def ping_slash(self, interaction: discord.Interaction) -> None:
        started = time.perf_counter()
        await interaction.response.send_message("Pinging...")
        round_trip = round((time.perf_counter() - started) * 1000)
        gateway = round(self.bot.latency * 1000)
        await interaction.edit_original_response(
            content=f"Pong! Gateway: `{gateway}ms` | Round-trip: `{round_trip}ms`"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
