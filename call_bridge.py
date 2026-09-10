from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass

import discord
import discord.ext.voice_recv as voice_recv


FRAME_SIZE = 3840
SILENCE = b"\x00" * FRAME_SIZE
MAX_BUFFERED_FRAMES = 100


class BridgeSource(discord.AudioSource):
    def __init__(self) -> None:
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=MAX_BUFFERED_FRAMES)
        self.closed = False

    def read(self) -> bytes:
        if self.closed:
            return b""
        try:
            return self.frames.get(timeout=0.02)
        except queue.Empty:
            return SILENCE

    def is_opus(self) -> bool:
        return False

    def write(self, pcm: bytes) -> None:
        if self.closed:
            return
        try:
            self.frames.put_nowait(pcm)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            self.frames.put_nowait(pcm)

    def cleanup(self) -> None:
        self.closed = True


class BridgeSink(voice_recv.AudioSink):
    def __init__(self, bridge: "CallBridge", guild_id: int) -> None:
        super().__init__()
        self.bridge = bridge
        self.guild_id = guild_id

    def write(self, user: discord.Member | discord.User | None, data: voice_recv.VoiceData) -> None:
        if data.pcm:
            self.bridge.broadcast(self.guild_id, data.pcm)

    def wants_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass


@dataclass
class CallConnection:
    voice_client: voice_recv.VoiceRecvClient
    source: BridgeSource
    guild_name: str


class CallBridge:
    def __init__(self) -> None:
        self.connections: dict[int, CallConnection] = {}
        self.lock = threading.RLock()

    def broadcast(self, source_guild_id: int, pcm: bytes) -> None:
        with self.lock:
            connections = list(self.connections.items())
        for guild_id, connection in connections:
            if guild_id != source_guild_id:
                connection.source.write(pcm)

    async def connect(
        self,
        member: discord.Member,
        channel: discord.VoiceChannel | discord.StageChannel | None = None,
    ) -> str:
        target = channel or (member.voice.channel if member.voice else None)
        if target is None:
            return "Join a voice channel first, or choose a channel with the slash command."

        permissions = target.permissions_for(member.guild.me) if member.guild.me else None
        if permissions and (not permissions.connect or not permissions.speak):
            return "I need Connect and Speak permissions in that voice channel."

        existing = member.guild.voice_client
        if existing is not None:
            await existing.disconnect(force=True)
            with self.lock:
                self.connections.pop(member.guild.id, None)

        try:
            voice_client = await asyncio.wait_for(
                target.connect(cls=voice_recv.VoiceRecvClient, reconnect=True, self_deaf=False),
                timeout=15,
            )
        except asyncio.TimeoutError:
            return "The voice connection timed out. Check that I have Connect and Speak permissions."
        except (discord.ClientException, OSError) as error:
            print(f"Voice call connection failed in {member.guild.name}: {error}")
            return "I could not connect to that voice channel. Check my permissions and try again."

        source = BridgeSource()
        try:
            voice_client.play(source)
            voice_client.listen(BridgeSink(self, member.guild.id))
        except Exception as error:
            source.cleanup()
            await voice_client.disconnect(force=True)
            print(f"Voice call setup failed in {member.guild.name}: {error}")
            return "I could not start the server call. Please try again."
        with self.lock:
            self.connections[member.guild.id] = CallConnection(voice_client, source, member.guild.name)

        with self.lock:
            peer_names = [
                connection.guild_name
                for guild_id, connection in self.connections.items()
                if guild_id != member.guild.id
            ]
        if peer_names:
            return f"Connected with **{peer_names[0]}**. Happy chatting!"
        return "Joined VC! Connecting to a server..."

    async def disconnect(self, guild_id: int) -> str:
        with self.lock:
            connection = self.connections.pop(guild_id, None)
        if connection is None:
            return "I am not connected to a server call."
        connection.voice_client.stop_listening()
        connection.source.cleanup()
        await connection.voice_client.disconnect(force=True)
        return "Disconnected from the server call."

    def get(self, guild_id: int) -> CallConnection | None:
        with self.lock:
            return self.connections.get(guild_id)


call_bridge = CallBridge()