import discord
import wavelink
from discord.ext import commands

from config import settings


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=settings.prefix, intents=intents, help_command=None)
        self.lavalink_ready = False

    async def setup_hook(self) -> None:
        await self.load_extension("commands.help")
        await self.load_extension("commands.voice")
        await self.load_extension("commands.music")
        await self.load_extension("commands.preferences")
        await wavelink.Pool.connect(
            nodes=[wavelink.Node(uri=settings.lavalink_uri, password=settings.lavalink_password)],
            client=self,
        )
        await self.tree.sync()
        print("Synced global slash commands")

    async def on_ready(self) -> None:
        print(f"Bot is online as {self.user} | Lavalink: {self.lavalink_ready}")

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        self.lavalink_ready = True
        print(f"Lavalink node ready: {payload.node.identifier}")


bot = MusicBot()
