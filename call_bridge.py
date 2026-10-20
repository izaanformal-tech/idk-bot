from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass

try:
    import audioop
except ImportError:  # Python 3.13+ without audioop-lts
    audioop = None

import discord
import discord.ext.voice_recv as voice_recv
from discord.ext.voice_recv.opus import PacketDecoder
from discord.ext.voice_recv.reader import AudioReader
from discord.opus import OPUS_SILENCE, OpusError

try:
    import davey
except ImportError:
    davey = None


PCM_FRAME_BYTES = 3840
PCM_SILENCE = b"\x00" * PCM_FRAME_BYTES
# Per-speaker jitter buffer: wait for 3 frames (60 ms) before playing a speaker,
# and never hold more than 10 frames (200 ms) so latency cannot build up.
PREBUFFER_FRAMES = 3
MAX_STREAM_FRAMES = 10


# voice_recv stops listening for good when one packet fails to decode, which
# turns a single bad packet into a silent call. Decode failures become silence.
_original_decode_packet = PacketDecoder._decode_packet


def _safe_decode_packet(self: PacketDecoder, packet):
    try:
        return _original_decode_packet(self, packet)
    except OpusError:
        return packet, PCM_SILENCE


PacketDecoder._decode_packet = _safe_decode_packet


def _mix(frames: list[bytes]) -> bytes:
    mixed = frames[0]
    if audioop is None:
        return mixed
    for frame in frames[1:]:
        mixed = audioop.add(mixed, frame, 2)
    return mixed


class BridgeSource(discord.AudioSource):
    """Plays audio from the other servers, mixing each speaker on the 20 ms playback clock."""

    def __init__(self) -> None:
        self.streams: dict[tuple[int, int], deque[bytes]] = {}
        self.primed: set[tuple[int, int]] = set()
        self.lock = threading.Lock()
        self.closed = False

    def read(self) -> bytes:
        if self.closed:
            return b""
        frames: list[bytes] = []
        with self.lock:
            for stream_id, buffer in list(self.streams.items()):
                if stream_id not in self.primed:
                    if len(buffer) < PREBUFFER_FRAMES:
                        continue
                    self.primed.add(stream_id)
                if buffer:
                    frames.append(buffer.popleft())
                else:
                    # The speaker went quiet; re-prime when they talk again.
                    self.primed.discard(stream_id)
                    del self.streams[stream_id]
        return _mix(frames) if frames else PCM_SILENCE

    def is_opus(self) -> bool:
        return False

    def write(self, stream_id: tuple[int, int], pcm_frame: bytes) -> None:
        if self.closed or len(pcm_frame) != PCM_FRAME_BYTES:
            return
        with self.lock:
            buffer = self.streams.get(stream_id)
            if buffer is None:
                buffer = self.streams[stream_id] = deque(maxlen=MAX_STREAM_FRAMES)
            buffer.append(pcm_frame)

    def cleanup(self) -> None:
        self.closed = True
        with self.lock:
            self.streams.clear()
            self.primed.clear()


class BridgeSink(voice_recv.AudioSink):
    def __init__(self, bridge: "CallBridge", guild_id: int) -> None:
        super().__init__()
        self.bridge = bridge
        self.guild_id = guild_id

    def write(self, user: discord.Member | discord.User | None, data: voice_recv.VoiceData) -> None:
        # user is None until Discord maps the speaker's SSRC, so keep that audio.
        if (user is not None and user.bot) or not data.pcm:
            return
        self.bridge.broadcast(self.guild_id, (self.guild_id, data.packet.ssrc), data.pcm)

    def wants_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass


class BridgeVoiceClient(voice_recv.VoiceRecvClient):
    """VoiceRecvClient that also removes Discord's DAVE end-to-end encryption from received audio."""

    def listen(self, sink: voice_recv.AudioSink, *, after=None) -> None:
        if not self.is_connected():
            raise discord.ClientException("Not connected to voice.")
        if self.is_listening():
            raise discord.ClientException("Already receiving audio.")

        self._reader = AudioReader(sink, self, after=after)
        transport_decrypt = self._reader.decryptor.decrypt_rtp
        self._reader.decryptor.decrypt_rtp = lambda packet: self._dave_decrypt(
            packet.ssrc, transport_decrypt(packet)
        )
        self._reader.start()

    def _dave_decrypt(self, ssrc: int, data: bytes) -> bytes:
        connection = self._connection
        session = connection.dave_session
        if davey is None or session is None or connection.dave_protocol_version == 0:
            return data
        if data == OPUS_SILENCE:
            return data
        user_id = self._get_id_from_ssrc(ssrc)
        if user_id is None:
            return OPUS_SILENCE
        try:
            return session.decrypt(user_id, davey.MediaType.audio, data)
        except Exception:
            return OPUS_SILENCE


@dataclass
class CallConnection:
    voice_client: BridgeVoiceClient
    source: BridgeSource
    guild_name: str


class CallBridge:
    def __init__(self) -> None:
        self.connections: dict[int, CallConnection] = {}
        self.lock = threading.RLock()

    def broadcast(self, source_guild_id: int, stream_id: tuple[int, int], pcm: bytes) -> None:
        with self.lock:
            connections = list(self.connections.items())
        for guild_id, connection in connections:
            if guild_id != source_guild_id:
                connection.source.write(stream_id, pcm)

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

        if self.get(member.guild.id) is not None:
            await self.disconnect(member.guild.id)
        existing = member.guild.voice_client
        if existing is not None:
            await existing.disconnect(force=True)

        try:
            voice_client = await asyncio.wait_for(
                target.connect(cls=BridgeVoiceClient, reconnect=True, self_deaf=False),
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
        if connection.voice_client.is_listening():
            connection.voice_client.stop_listening()
        connection.source.cleanup()
        await connection.voice_client.disconnect(force=True)
        return "Disconnected from the server call."

    def get(self, guild_id: int) -> CallConnection | None:
        with self.lock:
            return self.connections.get(guild_id)


call_bridge = CallBridge()
