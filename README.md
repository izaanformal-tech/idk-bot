# AuraCall

AuraCall is a Discord music and voice bot for listening to songs, managing queues,
sharing playlists, and controlling voice calls from Discord.

## What AuraCall does

| Area | Capabilities |
| --- | --- |
| Music | Search songs, play URLs and playlists, pause, resume, skip, replay, seek, and control volume |
| Queue | View, shuffle, remove, clear, and loop queued tracks |
| Playlists | Create personal playlists, add tracks, browse playlists, and like playlists |
| Voice | Call between server voice channels, inspect call status, and hang up |
| Controls | Private button panels, search forms, selectable search results, and playback cards |
| Status | Show uptime, latency, version, language, and the AuraCall logo with `info` |
| Updates | Publish versioned releases through GitHub Releases |

## Requirements

| Requirement | Details |
| --- | --- |
| Runtime | Python 3.11 or newer |
| Audio server | A reachable Lavalink v4 server |
| Discord intent | Message Content Intent enabled |
| Invite scopes | `bot` and `applications.commands` |
| Bot permissions | View Channels, Send Messages, Connect, and Speak |

AuraCall uses the hosted Lavalink v4 service configured by `LAVALINK_URI` and
`LAVALINK_PASSWORD`. The production host must have LavaSrc and Spotify enabled so
Spotify tracks and playlists can be imported and resolved to playable audio.

User limits: 15 playlists per user, 1000 songs per playlist, and 1000 songs in the queue.

## Configuration

Set these environment variables on the host running AuraCall. Never commit a real
Discord token or password.

| Variable | Purpose | Example |
| --- | --- | --- |
| `DISCORD_TOKEN` | Discord bot token | `your-production-bot-token` |
| `COMMAND_PREFIX` | Prefix for text commands | `!` |
| `LAVALINK_URI` | Hosted Lavalink server address | `https://lavalinkv4.serenetia.com:443` |
| `LAVALINK_PASSWORD` | Lavalink server password | `your-lavalink-password` |
| `SUPABASE_URL` | Supabase project URL | `https://your-project.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key used by the bot | `your-service-role-key` |

Spotify playlist URLs are resolved directly by Lavalink through LavaSrc. No
Spotify account linking, client credentials, callback, or redirect is required.

## Commands

### General

| Command | What it does |
| --- | --- |
| `/info` or `!info` | Show AuraCall's logo, uptime, latency, version, language, audio backend, and prefix |
| `/help` or `!help` | Browse the complete command guide with previous and next buttons |
| `/version` or `!version` | Show the current AuraCall version |

### Music and playlists

| Command | What it does |
| --- | --- |
| `/music play <query>` | Play a song, URL, or playlist |
| `/music search <query>` | Search for music and choose a result before queueing |
| `/music panel` | Open private music controls |
| `/music nowplaying` | Show the current track and position |
| `/music queue` | Show upcoming tracks |
| `/music skip` | Skip the current track |
| `/music stop` | Stop playback and clear the queue |
| `/music pause` or `/music resume` | Pause or resume playback |
| `/music volume <0-100>` | Set the player volume |
| `/music loop <off\|track\|queue>` | Set the loop mode |
| `/music shuffle` | Shuffle upcoming tracks |
| `/music remove <position>` | Remove a track from the queue |
| `/music clear` | Clear the upcoming queue |
| `/music seek <seconds>` | Jump to a position in the current track |
| `/music replay` | Restart the current track |
| `/music playlist create` | Create a personal playlist |
| `/music playlist list` | Browse your playlists |
| `/music playlist play <playlist_name>` | Play a saved playlist |
| `/music playlist search` | Find your playlists by name |
| `/music playlist add` | Add a direct track URL to a playlist by name |
| `/music playlist delete` | Delete one of your playlists by name |
| `/music playlist like` | Like a playlist |
| `/music playlist import service:spotify url:<url>` | Resolve a Spotify playlist through Lavalink and queue its tracks |
| `/music playlist import service:youtube url:<url>` | Load a YouTube playlist through Lavalink into the queue |
| `/music settings` | Save a preferred playlist and essential playback settings |

### Voice and preferences

| Command | What it does |
| --- | --- |
| `/vc panel` | Open private voice controls |
| `/vc call` or `/vc join` | Join a voice channel and connect it to other server calls |
| `/vc vcstatus` | Show server call status |
| `/vc leave` or `/vc hangup` | End the server call and leave the voice channel |
| `/settings` | Show or save music preferences |

Text command aliases include `!p`, `!q`, `!np`, `!next`, `!vol`, `!rm`, and
`!restart`. Slash commands are synced globally and may take a short time to appear
after a deployment.

## Running AuraCall

Install the dependencies and start the bot with your environment configured:

```sh
python3 -m pip install -r requirements.txt
python3 main.py
```

The bot connects to the hosted Lavalink service and registers its global slash
commands on startup. `/music play` accepts Spotify track and playlist URLs when
the configured host has the Spotify source plugin enabled.

## Legal

| Document | Purpose |
| --- | --- |
| [Terms of Service](TERMS_OF_SERVICE.md) | Rules for using AuraCall |
| [Privacy Policy](PRIVACY_POLICY.md) | How AuraCall handles information |