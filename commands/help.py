import time

import discord
from discord import app_commands
from discord.ext import commands

from branding import DISPLAY_NAME, LOGO_FILENAME, LOGO_PATH
from version import __version__


def format_uptime(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


class HelpPages(discord.ui.View):
    def __init__(self, pages: list[discord.Embed]) -> None:
        super().__init__(timeout=300)
        self.pages = pages
        self.page = 0
        self.previous_button.disabled = True
        self.next_button.disabled = len(pages) < 2

    async def show_page(self, interaction: discord.Interaction) -> None:
        self.previous_button.disabled = self.page == 0
        self.next_button.disabled = self.page == len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await self.show_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, emoji="▶️")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await self.show_page(interaction)


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def help_pages(prefix: str) -> list[discord.Embed]:
        pages = [
            discord.Embed(
                title=f"{DISPLAY_NAME} commands",
                description="Use the buttons to browse every command and subcommand.",
                color=discord.Color.blurple(),
            ).add_field(
                name="General",
                value=(
                    f"`{prefix}info` / `/info` - bot details\n"
                    f"`{prefix}version` / `/version` - current release\n"
                    f"`{prefix}help` / `/help` - this guide"
                ),
                inline=False,
            ),
            discord.Embed(
                title="Music",
                description=(
                    "`/music play <query>` - play a song, URL, or playlist\n"
                    "`/music search <query>` - search without playing\n"
                    "`/music panel` - open music controls\n"
                    f"`{prefix}play`, `{prefix}search`, `{prefix}queue`, `{prefix}nowplaying`\n"
                    f"`{prefix}skip`, `{prefix}stop`, `{prefix}pause`, `{prefix}resume`\n"
                    f"`{prefix}volume`, `{prefix}loop`, `{prefix}shuffle`, `{prefix}remove`\n"
                    f"`{prefix}clear`, `{prefix}seek`, `{prefix}replay`"
                ),
                color=discord.Color.blurple(),
            ),
            discord.Embed(
                title="Music subcommands",
                description=(
                    "`/music playlist create` - create a server playlist\n"
                    "`/music playlist list` - browse your playlists\n"
                    "`/music playlist search` - find your playlists by name\n"
                    "`/music playlist add` - add a track URL by playlist name\n"
                    "`/music playlist delete` - delete one by playlist name\n"
                    "`/music playlist like` - like a playlist\n\n"
                    f"Prefix: `{prefix}playlist create|list|add|import|like`, `{prefix}musicpanel`\n"
                    f"Prefix aliases: `{prefix}p`, `{prefix}q`, `{prefix}np`, `{prefix}next`, "
                    f"`{prefix}vol`, `{prefix}rm`, `{prefix}restart`"
                ),
                color=discord.Color.blurple(),
            ),
            discord.Embed(
                title="Voice and preferences",
                description=(
                    "`/vc call`, `/vc join` - join or move voice\n"
                    "`/vc bridge` - connect voice channels across servers\n"
                    "`/vc endbridge` - end the cross-server bridge\n"
                    "`/vc leave`, `/vc hangup` - leave voice\n"
                    "`/vc skip`, `/vc vcstatus`, `/vc panel` - voice controls\n"
                    f"`{prefix}call`, `{prefix}join`, `{prefix}leave`, `{prefix}vcstatus`\n\n"
                    f"`{prefix}vcpanel` - open voice controls\n\n"
                    "`/settings` - show or save music preferences\n"
                    f"`{prefix}settings`, `{prefix}setvolume`, `{prefix}setloop`"
                ),
                color=discord.Color.blurple(),
            ),
        ]
        for index, page in enumerate(pages, start=1):
            page.set_footer(text=f"Page {index}/{len(pages)}")
        return pages

    async def send_help(self, destination: discord.Interaction | commands.Context) -> None:
        prefix = self.bot.command_prefix if isinstance(destination, discord.Interaction) else destination.clean_prefix
        pages = self.help_pages(str(prefix))
        view = HelpPages(pages)
        if isinstance(destination, discord.Interaction):
            await destination.response.send_message(embed=pages[0], view=view, ephemeral=True)
        else:
            await destination.send(embed=pages[0], view=view)

    @commands.command(name="help")
    async def help_prefix(self, ctx: commands.Context) -> None:
        await self.send_help(ctx)

    @app_commands.command(name="help", description="Browse every AuraCall command")
    async def help_slash(self, interaction: discord.Interaction) -> None:
        await self.send_help(interaction)

    def info_payload(self) -> tuple[discord.Embed, discord.File]:
        embed = discord.Embed(title=DISPLAY_NAME, description="A Discord music and voice bot.", color=discord.Color.blurple())
        embed.set_thumbnail(url=f"attachment://{LOGO_FILENAME}")
        embed.add_field(name="Version", value=f"`{__version__}`", inline=True)
        embed.add_field(name="Language", value="Python", inline=True)
        embed.add_field(name="Uptime", value=format_uptime(time.monotonic() - self.bot.started_at), inline=True)
        embed.add_field(name="Ping", value=f"`{round(self.bot.latency * 1000)}ms`", inline=True)
        embed.add_field(name="Users", value=f"`{self.bot.user_count}`", inline=True)
        embed.add_field(name="Servers", value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.add_field(name="Audio", value="Lavalink", inline=True)
        embed.add_field(name="Prefix", value=f"`{self.bot.command_prefix}`", inline=True)
        embed.set_footer(text="AuraCall is open for music, playlists, and voice calls.")
        return embed, discord.File(LOGO_PATH, filename=LOGO_FILENAME)

    @commands.command(name="info")
    async def info_prefix(self, ctx: commands.Context) -> None:
        embed, logo = self.info_payload()
        await ctx.send(embed=embed, file=logo)

    @app_commands.command(name="info", description="Show AuraCall status, uptime, version, and details")
    async def info_slash(self, interaction: discord.Interaction) -> None:
        embed, logo = self.info_payload()
        await interaction.response.send_message(embed=embed, file=logo)

    @commands.command(name="version")
    async def version_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(f"**{DISPLAY_NAME}** version `{__version__}`")

    @app_commands.command(name="version", description="Show the current AuraCall release")
    async def version_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"**{DISPLAY_NAME}** version `{__version__}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))