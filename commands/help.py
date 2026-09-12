import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from branding import DISPLAY_NAME, LOGO_FILENAME, LOGO_PATH
from version import __version__


OWNER_USER_ID = 889540845269823559


class GuildInviteView(discord.ui.View):
    def __init__(self, bot: commands.Bot, owner_id: int, guilds: list[discord.Guild]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.owner_id = owner_id
        self.guilds = guilds
        self.page = 0
        self.page_size = 25
        self.guild_select: discord.ui.Select[Any] | None = None
        self.refresh_controls()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.guilds) + self.page_size - 1) // self.page_size)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This owner panel is private.", ephemeral=True)
            return False
        return True

    def refresh_controls(self) -> None:
        if self.guild_select is not None:
            self.remove_item(self.guild_select)
        start = self.page * self.page_size
        page_guilds = self.guilds[start : start + self.page_size]
        self.guild_select = discord.ui.Select(
            placeholder=f"Choose a guild ({self.page + 1}/{self.page_count})...",
            options=[
                discord.SelectOption(
                    label=guild.name[:100],
                    description=f"Guild ID: {guild.id}"[:100],
                    value=str(guild.id),
                )
                for guild in page_guilds
            ],
        )
        self.guild_select.callback = self.guild_selected
        self.add_item(self.guild_select)
        self.previous_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.page_count - 1

    async def guild_selected(self, interaction: discord.Interaction) -> None:
        guild_id = int(self.guild_select.values[0]) if self.guild_select else 0
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            await interaction.response.send_message("That guild is no longer available.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        invite = await self.create_invite(guild)
        if invite is None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Could not create invite",
                    description=f"I could not create an invite for **{guild.name}**.\n"
                    "The bot needs Create Instant Invite permission in a text channel.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Guild invite ready",
            description=f"Invite for **{guild.name}** is ready.",
            color=discord.Color.green(),
        )
        await interaction.followup.send(
            embed=embed,
            view=GuildInviteLinkView(invite.url, self.owner_id),
            ephemeral=True,
        )

    async def create_invite(self, guild: discord.Guild) -> discord.Invite | None:
        me = guild.me
        channels = list(guild.text_channels)
        if guild.system_channel is not None:
            channels.sort(key=lambda channel: channel != guild.system_channel)
        for channel in channels:
            if me is not None and not channel.permissions_for(me).create_instant_invite:
                continue
            try:
                return await channel.create_invite(max_age=0, max_uses=0, unique=False)
            except (discord.Forbidden, discord.HTTPException):
                continue
        return None

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        self.refresh_controls()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        self.refresh_controls()
        await interaction.response.edit_message(view=self)


class GuildInviteLinkView(discord.ui.View):
    def __init__(self, invite_url: str, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.add_item(
            discord.ui.Button(
                label="Join Guild",
                style=discord.ButtonStyle.link,
                url=invite_url,
                emoji="🔗",
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This invite is private.", ephemeral=True)
            return False
        return True


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
                    "`/music playlist create` - create a personal playlist\n"
                    "`/music playlist list` - browse your playlists\n"
                    "`/music playlist play` - play a saved playlist\n"
                    "`/music playlist search` - find your playlists by name\n"
                    "`/music playlist add` - add a track URL by playlist name\n"
                    "`/music playlist delete` - delete one by playlist name\n"
                    "`/music playlist like` - like a playlist\n\n"
                    f"Prefix: `{prefix}playlist create|list|play|search|add|import|delete|like`, `{prefix}musicpanel`\n"
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

    @commands.command(name="guilds")
    async def guilds_prefix(self, ctx: commands.Context) -> None:
        if ctx.author.id != OWNER_USER_ID:
            await ctx.send("This command is owner-only.")
            return
        guilds = sorted(self.bot.guilds, key=lambda guild: guild.name.casefold())
        if not guilds:
            await ctx.send("The bot is not in any guilds.")
            return
        view = GuildInviteView(self.bot, OWNER_USER_ID, guilds)
        embed = discord.Embed(
            title="Guild invite manager",
            description=(
                f"Choose a guild below to generate an invite.\n"
                f"Showing {len(guilds)} guild(s) across {view.page_count} page(s)."
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, view=view)

    @app_commands.command(name="help", description="Browse every AuraCall command")
    async def help_slash(self, interaction: discord.Interaction) -> None:
        await self.send_help(interaction)

    def info_payload(self) -> tuple[discord.Embed, discord.File]:
        started_at = getattr(self.bot, "started_at", time.monotonic())
        user_count = getattr(self.bot, "user_count", 0)
        embed = discord.Embed(title=DISPLAY_NAME, description="A Discord music and voice bot.", color=discord.Color.blurple())
        embed.set_thumbnail(url=f"attachment://{LOGO_FILENAME}")
        embed.add_field(name="Version", value=f"`{__version__}`", inline=True)
        embed.add_field(name="Language", value="Python", inline=True)
        embed.add_field(name="Uptime", value=format_uptime(time.monotonic() - started_at), inline=True)
        embed.add_field(name="Ping", value=f"`{round(self.bot.latency * 1000)}ms`", inline=True)
        embed.add_field(name="Users", value=f"`{user_count}`", inline=True)
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