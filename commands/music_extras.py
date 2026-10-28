"""Lyrics, the now-playing card, 24/7 radio and member song requests.

- /lyrics            lyrics for the current song (or any song) from lrclib.net
- now-playing card   an image with artwork and a progress bar, used by /music nowplaying
- /radio             keeps playing a playlist or genre around the clock
- /request           members ask for songs; DJs approve or deny them
- /requests          set up the request channel, DJ role and "requests only" mode

Radio and request settings are saved with storage.JsonStore so they survive restarts.
"""

from __future__ import annotations

import asyncio
import io
import random
import re
import time

import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from storage import JsonStore

radio_store = JsonStore("radio")
request_store = JsonStore("song_requests")

LYRICS_API = "https://lrclib.net/api/search"
CARD_SIZE = (900, 300)


# ---------------------------------------------------------------- lyrics

def clean_title(title: str) -> str:
    """Strip video noise like '(Official Video)' or '[Lyrics]' so lyric searches match."""
    title = re.sub(r"[\(\[][^\)\]]*(official|lyric|video|audio|visualizer|remaster|hd|4k)[^\)\]]*[\)\]]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+-\s+topic$", "", title, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", title).strip(" -")


async def fetch_lyrics(title: str, artist: str | None) -> tuple[str, str, str] | None:
    """Returns (track name, artist, plain lyrics) or None."""
    title = clean_title(title)
    artist = re.sub(r"\s+-\s+topic$", "", artist or "", flags=re.IGNORECASE).strip() or None
    queries: list[dict[str, str]] = []
    # YouTube titles often look like "Artist - Song"; try that split first.
    if " - " in title:
        left, right = (part.strip() for part in title.split(" - ", 1))
        queries.append({"track_name": right, "artist_name": left})
    if artist:
        queries.append({"track_name": title, "artist_name": artist})
    queries.append({"q": f"{artist or ''} {title}".strip() if artist and artist.lower() not in title.lower() else title})
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "AuraCall Discord bot"}) as session:
        for query in queries:
            async with session.get(LYRICS_API, params=query) as response:
                if response.status != 200:
                    continue
                results = await response.json()
            for result in results:
                if result.get("plainLyrics"):
                    return result["trackName"], result["artistName"], result["plainLyrics"]
    return None


def lyrics_pages(lyrics: str, limit: int = 3900) -> list[str]:
    pages, current = [], ""
    for line in lyrics.splitlines():
        if len(current) + len(line) + 1 > limit:
            pages.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        pages.append(current)
    return pages or ["(no lyrics)"]


class LyricsView(discord.ui.View):
    def __init__(self, title: str, pages: list[str]) -> None:
        super().__init__(timeout=300)
        self.title, self.pages, self.index = title, pages, 0
        self._sync()

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.title[:256], description=self.pages[self.index], color=discord.Color.blurple())
        embed.set_footer(text=f"Page {self.index + 1}/{len(self.pages)} • Lyrics from lrclib.net")
        return embed

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index += 1
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)


# ---------------------------------------------------------------- now-playing card

def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _clock(ms: int | None) -> str:
    if ms is None:
        return "LIVE"
    seconds = max(ms, 0) // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def render_card(title: str, artist: str, artwork: bytes | None, position_ms: int, length_ms: int | None, paused: bool) -> bytes:
    width, height = CARD_SIZE
    card = Image.new("RGB", CARD_SIZE, (24, 22, 38))
    art = None
    if artwork:
        try:
            art = Image.open(io.BytesIO(artwork)).convert("RGB")
        except Exception:
            art = None
    if art is not None:
        # Blurred, darkened artwork as the background.
        background = art.resize((width, width)).crop((0, (width - height) // 2, width, (width + height) // 2))
        background = ImageEnhance.Brightness(background.filter(ImageFilter.GaussianBlur(24))).enhance(0.35)
        card.paste(background, (0, 0))
        cover = art.resize((240, 240))
        mask = Image.new("L", (240, 240), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 240, 240), 18, fill=255)
        card.paste(cover, (30, 30), mask)
    draw = ImageDraw.Draw(card)
    if art is None:
        draw.rounded_rectangle((30, 30, 270, 270), 18, fill=(60, 52, 96))
        # A simple drawn music note (the default font has no ♪ glyph).
        note = (220, 210, 255)
        draw.ellipse((112, 172, 152, 204), fill=note)
        draw.rectangle((144, 92, 152, 188), fill=note)
        draw.polygon([(152, 92), (190, 108), (190, 124), (152, 110)], fill=note)

    left, right = 300, width - 40
    draw.text((left, 40), "PAUSED" if paused else "NOW PLAYING", font=_font(20), fill=(170, 160, 220))
    draw.text((left, 72), _fit(draw, title, _font(38), right - left), font=_font(38), fill=(255, 255, 255))
    draw.text((left, 124), _fit(draw, artist or "Unknown artist", _font(26), right - left), font=_font(26), fill=(200, 196, 220))

    bar_top = 210
    draw.rounded_rectangle((left, bar_top, right, bar_top + 10), 5, fill=(80, 74, 110))
    if length_ms:
        filled = left + int((right - left) * min(1.0, max(0.0, position_ms / length_ms)))
        draw.rounded_rectangle((left, bar_top, max(left + 10, filled), bar_top + 10), 5, fill=(160, 120, 255))
        draw.ellipse((filled - 9, bar_top - 4, filled + 9, bar_top + 14), fill=(255, 255, 255))
    draw.text((left, bar_top + 24), _clock(position_ms), font=_font(20), fill=(200, 196, 220))
    draw.text((right, bar_top + 24), _clock(length_ms), font=_font(20), fill=(200, 196, 220), anchor="ra")

    output = io.BytesIO()
    card.save(output, format="PNG")
    return output.getvalue()


async def now_playing_card(player: wavelink.Player) -> discord.File | None:
    track = player.current
    if track is None:
        return None
    artwork = None
    url = getattr(track, "artwork", None)
    if url:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        artwork = await response.read()
        except Exception:
            artwork = None
    png = await asyncio.to_thread(
        render_card, clean_title(track.title), track.author, artwork, player.position, None if track.is_stream else track.length, player.paused
    )
    return discord.File(io.BytesIO(png), filename="nowplaying.png")


# ---------------------------------------------------------------- song requests

def is_dj(member: discord.Member, settings: dict | None = None) -> bool:
    settings = settings if settings is not None else request_store.get(member.guild.id, {})
    if member.guild_permissions.manage_guild:
        return True
    role_id = settings.get("dj_role_id")
    return bool(role_id and any(role.id == role_id for role in member.roles))


def requests_only(guild_id: int) -> bool:
    settings = request_store.get(guild_id, {})
    return bool(settings.get("enabled") and settings.get("requests_only"))


def request_buttons(request_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Approve", style=discord.ButtonStyle.success, emoji="✅", custom_id=f"songreq:approve:{request_id}"))
    view.add_item(discord.ui.Button(label="Deny", style=discord.ButtonStyle.danger, emoji="✖️", custom_id=f"songreq:deny:{request_id}"))
    return view


# ---------------------------------------------------------------- the cog

class MusicExtras(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._refilling: set[int] = set()

    def music(self):
        return self.bot.get_cog("Music")

    # ----- lyrics -----

    @app_commands.command(name="lyrics", description="Show lyrics for the current song, or search any song")
    @app_commands.describe(song="Leave empty for the song that's playing")
    @app_commands.guild_only()
    async def lyrics(self, interaction: discord.Interaction, song: str | None = None) -> None:
        await interaction.response.defer()
        if song:
            title, artist = song, None
        else:
            player = interaction.guild.voice_client
            track = getattr(player, "current", None) if isinstance(player, wavelink.Player) else None
            if track is None:
                await interaction.followup.send("Nothing is playing. Try `/lyrics song:<name>`.")
                return
            title, artist = track.title, track.author
        try:
            found = await fetch_lyrics(title, artist)
        except Exception as error:
            print(f"[Lyrics] lookup failed: {error}", flush=True)
            found = None
        if not found:
            await interaction.followup.send(f"Couldn't find lyrics for **{clean_title(title)}**.")
            return
        name, artist_name, text = found
        view = LyricsView(f"🎤 {name} — {artist_name}", lyrics_pages(text))
        await interaction.followup.send(embed=view.embed(), view=view if len(view.pages) > 1 else discord.utils.MISSING)

    # ----- 24/7 radio -----

    radio = app_commands.Group(name="radio", description="24/7 radio that keeps playing in a voice channel",
                               default_permissions=discord.Permissions(manage_guild=True), guild_only=True)

    @radio.command(name="start", description="Play a playlist or genre around the clock in your voice channel")
    @app_commands.describe(source="A playlist URL, or a genre/search like 'lofi hip hop'")
    async def radio_start(self, interaction: discord.Interaction, source: str) -> None:
        await interaction.response.defer()
        member = interaction.user
        channel = member.voice.channel if member.voice else None
        if channel is None:
            await interaction.followup.send("Join the voice channel you want the radio in first.")
            return
        radio_store.set(interaction.guild_id, {"channel_id": channel.id, "text_channel_id": interaction.channel_id, "source": source, "started_by": member.id})
        music = self.music()
        player = interaction.guild.voice_client
        if player is not None and not isinstance(player, wavelink.Player):
            await interaction.followup.send("I'm in a server call right now. Hang up with `/vc hangup` first.")
            return
        try:
            if player is None:
                player = await channel.connect(cls=wavelink.Player, reconnect=True, self_deaf=True)
            elif player.channel != channel:
                await player.move_to(channel)
        except Exception as error:
            await interaction.followup.send(f"I couldn't join {channel.mention}: {error}")
            return
        added = await self.refill(player, force=True)
        if not added:
            radio_store.delete(interaction.guild_id)
            await interaction.followup.send("I couldn't find anything to play for that. Try a playlist URL or another genre.")
            return
        if not player.playing and music is not None:
            await music.advance_after_track_end(player)
        await interaction.followup.send(f"📻 24/7 radio is on in {channel.mention}, playing **{source}**. I'll keep going until `/radio stop`.")

    @radio.command(name="stop", description="Turn off 24/7 radio")
    async def radio_stop(self, interaction: discord.Interaction) -> None:
        if radio_store.get(interaction.guild_id) is None:
            await interaction.response.send_message("Radio isn't on.", ephemeral=True)
            return
        radio_store.delete(interaction.guild_id)
        player = interaction.guild.voice_client
        if isinstance(player, wavelink.Player):
            player.queue.clear()
            await player.disconnect()
        await interaction.response.send_message("📻 Radio stopped.")

    @radio.command(name="status", description="See what the radio is playing")
    async def radio_status(self, interaction: discord.Interaction) -> None:
        settings = radio_store.get(interaction.guild_id)
        if settings is None:
            await interaction.response.send_message("Radio is off. Start it with `/radio start`.", ephemeral=True)
            return
        await interaction.response.send_message(f"📻 Radio is on in <#{settings['channel_id']}>, playing **{settings['source']}**.", ephemeral=True)

    async def refill(self, player: wavelink.Player, force: bool = False) -> int:
        """Called when the queue runs dry. Adds a shuffled batch from the radio source."""
        guild_id = player.guild.id if player.guild else None
        settings = radio_store.get(guild_id) if guild_id else None
        if settings is None or guild_id in self._refilling or (len(player.queue) and not force):
            return 0
        self._refilling.add(guild_id)
        try:
            source = settings["source"]
            result = await asyncio.wait_for(wavelink.Playable.search(source), timeout=15)
            tracks = list(result.tracks) if isinstance(result, wavelink.Playlist) else list(result)
            if not tracks:
                return 0
            random.shuffle(tracks)
            for track in tracks[:50]:
                await player.queue.put_wait(track)
            return min(len(tracks), 50)
        except Exception as error:
            print(f"[Radio] refill failed in {guild_id}: {error}", flush=True)
            return 0
        finally:
            self._refilling.discard(guild_id)

    async def resume_radios(self) -> None:
        """After a restart (or Lavalink reconnect), rejoin every radio channel."""
        music = self.music()
        for guild_id, settings in radio_store.items():
            guild = self.bot.get_guild(int(guild_id))
            channel = guild.get_channel(settings["channel_id"]) if guild else None
            if channel is None:
                continue
            player = guild.voice_client
            if player is not None and not isinstance(player, wavelink.Player):
                continue  # a server call is using voice
            try:
                if player is None:
                    player = await channel.connect(cls=wavelink.Player, reconnect=True, self_deaf=True)
                if not player.playing:
                    await self.refill(player, force=True)
                    if music is not None:
                        await music.advance_after_track_end(player)
                print(f"[Radio] Resumed in {guild.name}", flush=True)
            except Exception as error:
                print(f"[Radio] Couldn't resume in {guild_id}: {error}", flush=True)

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        await self.bot.wait_until_ready()
        await self.resume_radios()

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        # 24/7 radio never leaves for being idle; other players are left alone too.
        if player.guild and radio_store.get(player.guild.id) is not None and not player.playing:
            await self.refill(player, force=True)
            music = self.music()
            if music is not None:
                await music.advance_after_track_end(player)

    # ----- song requests -----

    requests_group = app_commands.Group(name="requests", description="Set up member song requests",
                                        default_permissions=discord.Permissions(manage_guild=True), guild_only=True)

    @requests_group.command(name="setup", description="Turn on song requests")
    @app_commands.describe(
        channel="Where requests are posted for DJs to approve",
        dj_role="Who can approve requests (people with Manage Server always can)",
        requests_only="If on, non-DJs can't use /music play and must /request instead",
    )
    async def requests_setup(self, interaction: discord.Interaction, channel: discord.TextChannel,
                             dj_role: discord.Role | None = None, requests_only: bool = False) -> None:
        request_store.set(interaction.guild_id, {
            "enabled": True, "channel_id": channel.id,
            "dj_role_id": dj_role.id if dj_role else None, "requests_only": requests_only,
            "pending": request_store.get(interaction.guild_id, {}).get("pending", {}),
        })
        who = f"{dj_role.mention} and anyone with Manage Server" if dj_role else "anyone with Manage Server"
        mode = " Non-DJs must use `/request` instead of `/music play`." if requests_only else ""
        await interaction.response.send_message(f"🎶 Song requests are on. They'll be posted in {channel.mention} for {who} to approve.{mode}",
                                                allowed_mentions=discord.AllowedMentions.none())

    @requests_group.command(name="off", description="Turn off song requests")
    async def requests_off(self, interaction: discord.Interaction) -> None:
        request_store.delete(interaction.guild_id)
        await interaction.response.send_message("Song requests are off.")

    @app_commands.command(name="request", description="Ask the DJs to play a song")
    @app_commands.describe(song="Song name or URL")
    @app_commands.guild_only()
    async def request(self, interaction: discord.Interaction, song: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.create_request(interaction.user, song), ephemeral=True)

    async def create_request(self, member: discord.Member, query: str) -> str:
        settings = request_store.get(member.guild.id, {})
        if not settings.get("enabled"):
            return "Song requests aren't set up here. A server manager can turn them on with `/requests setup`."
        if member.voice is None or member.voice.channel is None:
            return "Join a voice channel first so the song can play where you are."
        channel = member.guild.get_channel(settings["channel_id"])
        if channel is None:
            return "The request channel no longer exists. Ask a server manager to run `/requests setup` again."
        try:
            result = await asyncio.wait_for(wavelink.Playable.search(query), timeout=12)
        except Exception:
            return "Couldn't search for that right now. Try again in a moment."
        track = (result.tracks[0] if isinstance(result, wavelink.Playlist) and result.tracks else
                 result[0] if isinstance(result, list) and result else None)
        if track is None or not track.uri:
            return "I couldn't find that song."
        request_id = f"{int(time.time() * 1000)}{random.randint(100, 999)}"
        pending = settings.setdefault("pending", {})
        pending[request_id] = {"uri": track.uri, "title": track.title, "author": track.author,
                               "user_id": member.id, "voice_channel_id": member.voice.channel.id}
        # Keep the pending list from growing forever.
        for old in list(pending)[:-100]:
            pending.pop(old, None)
        request_store.set(member.guild.id, settings)
        embed = discord.Embed(title="🎶 Song request", description=f"**[{track.title}]({track.uri})**\nby {track.author}",
                              color=discord.Color.blurple())
        embed.add_field(name="Requested by", value=member.mention)
        if getattr(track, "artwork", None):
            embed.set_thumbnail(url=track.artwork)
        await channel.send(embed=embed, view=request_buttons(request_id), allowed_mentions=discord.AllowedMentions.none())
        return f"📨 Requested **{track.title}**. A DJ will approve or deny it."

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        custom_id = (interaction.data or {}).get("custom_id", "") if interaction.type == discord.InteractionType.component else ""
        if not custom_id.startswith("songreq:"):
            return
        _, action, request_id = custom_id.split(":", 2)
        settings = request_store.get(interaction.guild_id, {})
        if not isinstance(interaction.user, discord.Member) or not is_dj(interaction.user, settings):
            await interaction.response.send_message("Only DJs can approve or deny requests.", ephemeral=True)
            return
        request = settings.get("pending", {}).pop(request_id, None)
        if request is None:
            await interaction.response.send_message("That request was already handled.", ephemeral=True)
            return
        request_store.set(interaction.guild_id, settings)
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed()
        if action == "deny":
            embed.color = discord.Color.red()
            embed.set_footer(text=f"Denied by {interaction.user.display_name}")
            await interaction.response.edit_message(embed=embed, view=None)
            return
        await interaction.response.defer()
        message = await self.play_request(interaction.guild, request)
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Approved by {interaction.user.display_name} • {message}"[:2048])
        await interaction.message.edit(embed=embed, view=None)

    async def play_request(self, guild: discord.Guild, request: dict) -> str:
        music = self.music()
        player = guild.voice_client
        if player is not None and not isinstance(player, wavelink.Player):
            return "I'm in a server call, so it couldn't be queued"
        try:
            result = await asyncio.wait_for(wavelink.Playable.search(request["uri"]), timeout=12)
            track = result.tracks[0] if isinstance(result, wavelink.Playlist) else result[0]
        except Exception:
            return "the song couldn't be loaded"
        if player is None:
            channel = guild.get_channel(request["voice_channel_id"])
            if channel is None:
                return "the requester's voice channel is gone"
            player = await channel.connect(cls=wavelink.Player, reconnect=True, self_deaf=True)
        await player.queue.put_wait(track)
        if not player.playing and music is not None:
            await music.advance_after_track_end(player)
            return "now playing"
        return f"queued at #{len(player.queue)}"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicExtras(bot))
