import discord
import wavelink
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import time

from config import settings


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=settings.prefix, intents=intents, help_command=None)
        self.lavalink_ready = False
        self.started_at = time.monotonic()
        self.status_index = 0

    rotating_statuses = (
        "music with your server",
        "your next favorite song",
        "shared playlists",
        "the queue move",
        "music in the voice channel",
        "your community soundtrack",
        "songs worth replaying",
        "Lavalink-powered audio",
        "the perfect track",
        "requests from the server",
        "late-night playlists",
        "your voice channel",
        "queue magic",
        "AuraCall radio",
        "music for everyone",
        "the playlist collection",
        "songs, queues, and good vibes",
        "what the server is playing",
        "your music controls",
        "new sounds for the server",
        "the next track",
        "community playlists",
        "voice channel adventures",
        "the queue playlist",
        "music on demand",
        "your server's soundtrack",
        "buttons, queues, and songs",
        "the rhythm of the server",
        "all your favorite tracks",
        "a better way to listen",
        "use !help for commands",
        "calling your voice channel",
        "waiting for your call",
        "AuraCall phone lines",
        "connecting your call",
        "your voice call hotline",
        "ringing the server",
        "on call for your community",
        "voice calls on demand",
        "your digital call assistant",
        "the next incoming call",
        "keeping the call connected",
        "your server call center",
        "ready to pick up",
        "voice channel calling",
        "the community phone line",
        "connecting friends in voice",
        "your call is important to us",
        "standing by for voice",
        "the AuraCall switchboard",
        "routing calls to voice",
        "your virtual phone booth",
        "taking calls in style",
        "voice support is online",
        "dial into your community",
        "the call connection",
        "your voice link is ready",
        "answering the server",
        "calling friends together",
        "voice channel reception",
        "your community call line",
    )

    async def setup_hook(self) -> None:
        await self.load_extension("commands.help")
        await self.load_extension("commands.voice")
        await self.load_extension("commands.music")
        await self.load_extension("commands.preferences")
        try:
            await asyncio.wait_for(
                wavelink.Pool.connect(
                    nodes=[
                        wavelink.Node(
                            identifier="primary",
                            uri=settings.lavalink_uri,
                            password=settings.lavalink_password,
                            retries=3,
                        )
                    ],
                    client=self,
                ),
                timeout=10,
            )
        except asyncio.TimeoutError:
            print("Lavalink connection timed out; music commands will report the unavailable backend.")
        except Exception as error:
            print(f"Lavalink connection failed: {error}")
        await self.tree.sync()
        print("Synced global slash commands")

    async def on_ready(self) -> None:
        print(f"Bot is online as {self.user} | Lavalink: {self.lavalink_ready}")
        await self.refresh_status()
        if not self.rotate_status.is_running():
            self.rotate_status.start()

    @property
    def user_count(self) -> int:
        return sum(guild.member_count or 0 for guild in self.guilds)

    @tasks.loop(minutes=2)
    async def rotate_status(self) -> None:
        await self.refresh_status()

    async def refresh_status(self) -> None:
        statuses = (
            (discord.ActivityType.watching, f"{self.user_count} users"),
            (discord.ActivityType.watching, f"{len(self.guilds)} servers"),
            (discord.ActivityType.watching, f"{round(self.latency * 1000)}ms ping"),
        )
        activity_type, status = statuses[self.status_index % len(statuses)]
        self.status_index += 1
        activity = discord.Activity(type=activity_type, name=status)
        await self.change_presence(
            activity=activity
        )

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        original = getattr(error, "original", error)
        print(f"Prefix command error in {ctx.command}: {original}")
        try:
            await ctx.send("I could not complete that command. Check my permissions and try again.")
        except discord.HTTPException:
            pass

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = getattr(error, "original", error)
        print(f"Slash command error: {original}")
        message = "I could not complete that command. Check my permissions and try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @rotate_status.before_loop
    async def before_rotate_status(self) -> None:
        await self.wait_until_ready()

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        self.lavalink_ready = True
        print(f"Lavalink node ready: {payload.node.identifier}")


bot = MusicBot()
