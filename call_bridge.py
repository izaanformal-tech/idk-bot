from __future__ import annotations

import asyncio
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

try:
    import audioop
except ImportError:  # Python 3.13+ without audioop-lts
    audioop = None

import discord
import discord.ext.voice_recv as voice_recv
from discord.ext.voice_recv.opus import PacketDecoder
from discord.ext.voice_recv.reader import AudioReader
from discord.opus import OPUS_SILENCE, OpusError

from storage import JsonStore

try:
    import davey
except ImportError:
    davey = None


PCM_FRAME_BYTES = 3840
PCM_SILENCE = b"\x00" * PCM_FRAME_BYTES
# Per-speaker jitter buffer. Start a speaker once 4 frames (80 ms) are queued.
# A late packet no longer counts as "stopped talking": the last frame is
# repeated quietly for up to 2 frames, and the speaker only resets after 200 ms
# of nothing. If the queue grows past 10 frames, one frame is skipped per tick
# to catch back up, so delay can't build up.
PREBUFFER_FRAMES = 4
MAX_STREAM_FRAMES = 25
CATCHUP_FRAMES = 10
CONCEAL_FRAMES = 2
HOLD_FRAMES = 10
STATS_INTERVAL_S = 60


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


class _Stream:
    __slots__ = ("frames", "primed", "misses", "last")

    def __init__(self) -> None:
        self.frames: deque[bytes] = deque(maxlen=MAX_STREAM_FRAMES)
        self.primed = False
        self.misses = 0
        self.last: bytes | None = None


class BridgeSource(discord.AudioSource):
    """Plays audio from the other servers, mixing each speaker on the 20 ms playback clock."""

    def __init__(self, label: str = "call") -> None:
        self.streams: dict[tuple[int, int], _Stream] = {}
        self.lock = threading.Lock()
        self.closed = False
        self.volume = 1.0  # incoming volume set with /vc volume (0.0 - 2.0)
        # Call quality diagnostics, logged once a minute when something is off.
        self.label = label
        self.stats = {"frames": 0, "gaps": 0, "concealed": 0, "skipped": 0, "late_ticks": 0}
        self.last_read: float | None = None
        self.stats_since = time.monotonic()

    def _track_timing(self) -> None:
        now = time.monotonic()
        # read() should run every 20 ms. Much longer means the host's CPU is
        # too busy to keep up, which makes audio stutter for everyone.
        if self.last_read is not None and now - self.last_read > 0.04:
            self.stats["late_ticks"] += 1
        self.last_read = now
        if now - self.stats_since >= STATS_INTERVAL_S:
            self._report()
            self.stats = {key: 0 for key in self.stats}
            self.stats_since = now

    def _report(self) -> None:
        st = self.stats
        problems = []
        if st["late_ticks"] > 25:
            problems.append(
                f"the bot fell behind {st['late_ticks']} times; the host's CPU is likely too slow or overloaded"
            )
        if st["gaps"] + st["concealed"] > max(10, st["frames"] // 50):
            problems.append(
                f"{st['gaps'] + st['concealed']} late or missing audio frames; the connection to Discord is unstable"
            )
        if problems:
            print(f"[Call audio] {self.label}: choppy audio in the last minute: " + "; ".join(problems), flush=True)

    def read(self) -> bytes:
        if self.closed:
            return b""
        self._track_timing()
        frames: list[bytes] = []
        with self.lock:
            for stream_id, stream in list(self.streams.items()):
                if not stream.primed:
                    if len(stream.frames) < PREBUFFER_FRAMES:
                        continue
                    stream.primed = True
                if stream.frames:
                    if len(stream.frames) > CATCHUP_FRAMES:
                        stream.frames.popleft()
                        self.stats["skipped"] += 1
                    stream.last = stream.frames.popleft()
                    stream.misses = 0
                    frames.append(stream.last)
                    self.stats["frames"] += 1
                    continue
                stream.misses += 1
                if stream.misses <= CONCEAL_FRAMES and stream.last is not None and audioop is not None:
                    # A packet is late: repeat the last frame quietly instead of a hard gap.
                    frames.append(audioop.mul(stream.last, 2, 0.5))
                    self.stats["concealed"] += 1
                elif stream.misses <= HOLD_FRAMES:
                    self.stats["gaps"] += 1
                else:
                    # A real pause in speech; buffer up again when they talk next.
                    del self.streams[stream_id]
        if not frames:
            return PCM_SILENCE
        mixed = _mix(frames)
        if self.volume != 1.0 and audioop is not None:
            mixed = audioop.mul(mixed, 2, self.volume)
        return mixed

    def is_opus(self) -> bool:
        return False

    def write(self, stream_id: tuple[int, int], pcm_frame: bytes) -> None:
        if self.closed or len(pcm_frame) != PCM_FRAME_BYTES:
            return
        with self.lock:
            stream = self.streams.get(stream_id)
            if stream is None:
                stream = self.streams[stream_id] = _Stream()
            stream.frames.append(pcm_frame)

    def cleanup(self) -> None:
        self.closed = True
        with self.lock:
            self.streams.clear()


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
    guild_id: int = 0
    text_channel: Any = None  # where call notices are posted
    partner_id: int | None = None
    last_partner_id: int | None = None
    matched_at: float | None = None
    muted: bool = False  # /vc mute: stop hearing the other server
    hangup_at: float | None = None  # /vc timer
    empty_since: float | None = None


REPORT_WINDOW_S = 24 * 3600
REPORTS_TO_SUSPEND = 3  # reports from this many different servers within a day suspend matching
EMPTY_HANGUP_S = 60  # hang up when nobody is left in the voice channel for this long


class CallBridge:
    """One-to-one server calls with random matching.

    Every server that starts a call waits in a queue and is paired with another
    waiting server. Audio only flows between partners. Servers can skip to a new
    partner, block or report a partner, mute or turn down incoming audio, and set
    an auto hang-up timer. Blocks and reports are saved to disk.
    """

    def __init__(self) -> None:
        self.connections: dict[int, CallConnection] = {}
        self.waiting: list[int] = []
        self.lock = threading.RLock()
        self.safety = JsonStore("call_safety")
        self._watcher: asyncio.Task | None = None

    # ---------- audio ----------

    def broadcast(self, source_guild_id: int, stream_id: tuple[int, int], pcm: bytes) -> None:
        with self.lock:
            me = self.connections.get(source_guild_id)
            partner = self.connections.get(me.partner_id) if me and me.partner_id else None
        if partner is not None and not partner.muted:
            partner.source.write(stream_id, pcm)

    # ---------- safety ----------

    def _blocks(self, guild_id: int) -> set[int]:
        return set(self.safety.get(f"blocks:{guild_id}", []))

    def _is_suspended(self, guild_id: int) -> bool:
        now = time.time()
        reports = [r for r in self.safety.get(f"reports:{guild_id}", []) if now - r["at"] < REPORT_WINDOW_S]
        return len({r["by"] for r in reports}) >= REPORTS_TO_SUSPEND

    def _can_pair(self, a: CallConnection, b: CallConnection) -> bool:
        if a.guild_id == b.guild_id:
            return False
        if b.guild_id in self._blocks(a.guild_id) or a.guild_id in self._blocks(b.guild_id):
            return False
        if self._is_suspended(a.guild_id) or self._is_suspended(b.guild_id):
            return False
        return True

    # ---------- matching ----------

    async def _notify(self, connection: CallConnection | None, message: str) -> None:
        if connection is not None and connection.text_channel is not None:
            try:
                await connection.text_channel.send(message, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    def _find_partner(self, me: CallConnection) -> CallConnection | None:
        candidates = [self.connections[g] for g in self.waiting if g in self.connections and g != me.guild_id]
        candidates = [c for c in candidates if self._can_pair(me, c)]
        # Prefer anyone other than the server it just left, unless nobody else is waiting.
        fresh = [c for c in candidates if c.guild_id != me.last_partner_id and c.last_partner_id != me.guild_id]
        pool = fresh or candidates
        return random.choice(pool) if pool else None

    async def _match(self, guild_id: int) -> None:
        with self.lock:
            me = self.connections.get(guild_id)
            if me is None or me.partner_id is not None:
                return
            if self._is_suspended(guild_id):
                suspended = True
                partner = None
                if guild_id in self.waiting:
                    self.waiting.remove(guild_id)
            else:
                suspended = False
                partner = self._find_partner(me)
                if partner is None:
                    if guild_id not in self.waiting:
                        self.waiting.append(guild_id)
                else:
                    for g in (guild_id, partner.guild_id):
                        if g in self.waiting:
                            self.waiting.remove(g)
                    me.partner_id, partner.partner_id = partner.guild_id, guild_id
                    me.matched_at = partner.matched_at = time.time()
        if suspended:
            await self._notify(me, "🚫 This server was reported by several servers recently, so random calls are paused for 24 hours.")
            return
        if partner is None:
            await self._notify(me, "⏳ Waiting for another server to pick up...")
            return
        await self._notify(me, f"📞 Connected with **{partner.guild_name}**! Say hi. Use `/vc skip` for someone else, `/vc report` if anything's wrong.")
        await self._notify(partner, f"📞 Connected with **{me.guild_name}**! Say hi. Use `/vc skip` for someone else, `/vc report` if anything's wrong.")

    def _unpair(self, guild_id: int) -> CallConnection | None:
        """Splits a pair. Returns the former partner (if any)."""
        with self.lock:
            me = self.connections.get(guild_id)
            if me is None or me.partner_id is None:
                return None
            partner = self.connections.get(me.partner_id)
            me.last_partner_id = me.partner_id
            me.partner_id = None
            me.matched_at = None
            if partner is not None:
                partner.last_partner_id = guild_id
                partner.partner_id = None
                partner.matched_at = None
            # Drop audio still queued from the old partner.
            with me.source.lock:
                me.source.streams.clear()
            if partner is not None:
                with partner.source.lock:
                    partner.source.streams.clear()
            return partner

    async def skip(self, guild_id: int, reason: str = "skipped") -> str:
        if self.get(guild_id) is None:
            return "I'm not in a server call. Start one with `/vc call`."
        partner = self._unpair(guild_id)
        # The server that skipped gets first pick of whoever is waiting.
        await self._match(guild_id)
        if partner is not None:
            await self._notify(partner, f"👋 The other server {reason}. Finding you a new server...")
            await self._match(partner.guild_id)
        return "⏭️ Looking for a new server..."

    async def report(self, guild_id: int, reporter_id: int, reason: str) -> str:
        me = self.get(guild_id)
        if me is None or me.partner_id is None:
            return "You're not connected to another server right now."
        target = me.partner_id
        target_name = self.connections[target].guild_name if target in self.connections else str(target)
        reports = self.safety.get(f"reports:{target}", [])
        reports.append({"by": guild_id, "user": reporter_id, "reason": reason[:300], "at": time.time()})
        self.safety.set(f"reports:{target}", reports[-50:])
        self._add_block(guild_id, target)
        print(f"[Call report] {me.guild_name} ({guild_id}) reported {target_name} ({target}): {reason}", flush=True)
        if self._is_suspended(target):
            # Reported by enough different servers: end their current call too.
            await self._notify(self.connections.get(target), "🚫 This server was reported by several servers, so random calls are paused for 24 hours.")
        await self.skip(guild_id, reason="hung up")
        return f"🚩 Reported **{target_name}**. You won't be matched with them again, and you're being connected to someone new."

    def _add_block(self, guild_id: int, target: int) -> None:
        blocks = self._blocks(guild_id)
        blocks.add(target)
        self.safety.set(f"blocks:{guild_id}", sorted(blocks))

    async def block(self, guild_id: int) -> str:
        me = self.get(guild_id)
        if me is None or me.partner_id is None:
            return "You're not connected to another server right now."
        target = me.partner_id
        name = self.connections[target].guild_name if target in self.connections else str(target)
        self._add_block(guild_id, target)
        await self.skip(guild_id, reason="hung up")
        return f"⛔ Blocked **{name}**. You won't be matched with them again."

    def clear_blocks(self, guild_id: int) -> str:
        count = len(self._blocks(guild_id))
        self.safety.delete(f"blocks:{guild_id}")
        return f"✅ Cleared {count} blocked server(s)."

    # ---------- controls ----------

    def set_muted(self, guild_id: int, muted: bool) -> str:
        me = self.get(guild_id)
        if me is None:
            return "I'm not in a server call."
        me.muted = muted
        if muted:
            with me.source.lock:
                me.source.streams.clear()
        return "🔇 The other server is muted for you." if muted else "🔊 You can hear the other server again."

    def set_volume(self, guild_id: int, percent: int) -> str:
        me = self.get(guild_id)
        if me is None:
            return "I'm not in a server call."
        me.source.volume = max(0, min(200, percent)) / 100
        return f"🔉 Incoming volume set to **{max(0, min(200, percent))}%**."

    def set_timer(self, guild_id: int, minutes: int) -> str:
        me = self.get(guild_id)
        if me is None:
            return "I'm not in a server call."
        if minutes <= 0:
            me.hangup_at = None
            return "⏲️ Auto hang-up timer turned off."
        me.hangup_at = time.time() + minutes * 60
        return f"⏲️ I'll hang up in **{minutes} minute(s)**."

    # ---------- connect / disconnect ----------

    async def connect(
        self,
        member: discord.Member,
        channel: discord.VoiceChannel | discord.StageChannel | None = None,
        text_channel: Any = None,
    ) -> str:
        target = channel or (member.voice.channel if member.voice else None)
        if target is None:
            return "Join a voice channel first, or choose a channel with the slash command."

        permissions = target.permissions_for(member.guild.me) if member.guild.me else None
        if permissions and (not permissions.connect or not permissions.speak):
            return "I need Connect and Speak permissions in that voice channel."
        if self._is_suspended(member.guild.id):
            return "🚫 This server was reported by several servers recently, so random calls are paused for 24 hours."

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

        source = BridgeSource(member.guild.name)
        try:
            voice_client.play(source)
            voice_client.listen(BridgeSink(self, member.guild.id))
        except Exception as error:
            source.cleanup()
            await voice_client.disconnect(force=True)
            print(f"Voice call setup failed in {member.guild.name}: {error}")
            return "I could not start the server call. Please try again."

        with self.lock:
            self.connections[member.guild.id] = CallConnection(
                voice_client, source, member.guild.name, member.guild.id, text_channel
            )
        self._ensure_watcher()
        await self._match(member.guild.id)
        return f"☎️ Joined **{target.name}**. I'll post here when another server picks up."

    async def disconnect(self, guild_id: int) -> str:
        partner = self._unpair(guild_id)
        with self.lock:
            connection = self.connections.pop(guild_id, None)
            if guild_id in self.waiting:
                self.waiting.remove(guild_id)
        if connection is None:
            return "I am not connected to a server call."
        if connection.voice_client.is_listening():
            connection.voice_client.stop_listening()
        connection.source.cleanup()
        await connection.voice_client.disconnect(force=True)
        if partner is not None:
            await self._notify(partner, "📴 The other server hung up. Finding you a new server...")
            await self._match(partner.guild_id)
        return "📴 Hung up and left the voice channel."

    def get(self, guild_id: int) -> CallConnection | None:
        with self.lock:
            return self.connections.get(guild_id)

    def status(self, guild_id: int) -> dict | None:
        me = self.get(guild_id)
        if me is None:
            return None
        partner = self.get(me.partner_id) if me.partner_id else None
        return {
            "channel": me.voice_client.channel.name if me.voice_client.channel else "unknown",
            "partner": partner.guild_name if partner else None,
            "since": me.matched_at,
            "muted": me.muted,
            "volume": round(me.source.volume * 100),
            "hangup_at": me.hangup_at,
            "waiting": len(self.waiting),
        }

    # ---------- background checks ----------

    def _ensure_watcher(self) -> None:
        if self._watcher is None or self._watcher.done():
            self._watcher = asyncio.get_running_loop().create_task(self._watch())

    async def _watch(self) -> None:
        """Every 5 seconds: run hang-up timers and leave channels everyone has left."""
        while True:
            await asyncio.sleep(5)
            with self.lock:
                connections = list(self.connections.values())
            if not connections:
                return
            now = time.time()
            for connection in connections:
                try:
                    if connection.hangup_at and now >= connection.hangup_at:
                        await self._notify(connection, "⏲️ Time's up, hanging up the call.")
                        await self.disconnect(connection.guild_id)
                        continue
                    channel = connection.voice_client.channel
                    humans = [m for m in getattr(channel, "members", []) if not m.bot]
                    if humans:
                        connection.empty_since = None
                    elif connection.empty_since is None:
                        connection.empty_since = now
                    elif now - connection.empty_since >= EMPTY_HANGUP_S:
                        await self._notify(connection, "📴 Everyone left the voice channel, so I hung up.")
                        await self.disconnect(connection.guild_id)
                except Exception as error:
                    print(f"[Call watcher] {error}", flush=True)


call_bridge = CallBridge()
