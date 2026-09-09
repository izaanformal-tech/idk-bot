# IDK Music Bot

Production-ready Discord music bot built with Python, `discord.py`, and Lavalink.
It supports global slash commands and configurable prefix commands, playlist URLs,
queue management, playback controls, and voice-channel calling.

## Features

- Lavalink-backed audio playback
- Song, URL, and playlist queueing
- Global slash commands for production servers
- Prefix equivalents for every music command
- Queue, shuffle, remove, clear, loop, volume, seek, replay, pause, and resume
- Automatic playback of the next queued track
- Separate command cogs so features can be extended independently
- Voice-channel reconnect, move, permission checks, and status reporting

## Requirements

- Python 3.11 or newer
- A reachable Lavalink v4 server
- A Discord bot with the `Message Content Intent` enabled
- Discord invite scopes: `bot` and `applications.commands`
- Bot permissions: View Channels, Send Messages, Connect, and Speak

Lavalink runs separately from this bot. Configure the Lavalink source plugins you
need for your searches and URLs on the Lavalink server itself.

## Configuration

Set these variables in the production host. Never commit a real token or password.

```text
DISCORD_TOKEN=your-production-bot-token
COMMAND_PREFIX=!
LAVALINK_URI=https://your-lavalink-host:443
LAVALINK_PASSWORD=your-lavalink-password
```

Slash commands are synced globally. No test-server or guild ID is required. Discord
may take time to publish global command changes after deployment.

## Install and run

The project is auto-detectable by Python hosts because it contains `requirements.txt`
and `main.py`:

```sh
python3 -m pip install -r requirements.txt
python3 main.py
```

For local development:

```sh
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

The included Dev Container installs the requirements and enables the Python and
Pylance extensions automatically.

## Production deployment commands

Set the four environment variables from the configuration section in your hosting
provider, then use these build and start commands:

```sh
python3 -m pip install -r requirements.txt
python3 main.py
```

The bot connects to Lavalink, loads every command cog, and deploys the global slash
commands automatically during startup. There is no separate command-deployment
script to run. Check the logs for:

```text
Synced global slash commands
Lavalink node ready: ...
Bot is online as ...
```

Keep the bot process running continuously. If the host restarts it, the same start
command reconnects to Lavalink and re-registers the commands.

## Commands

Every command below works as a slash command and with the configured prefix. For
example, `/play never gonna give you up` and `!play never gonna give you up` do the
same thing.

| Command | What it does |
| --- | --- |
| `/play <query>` | Play a song, URL, or playlist and queue the results |
| `/search <query>` | Search Lavalink and show the top results without playing |
| `/join` or `/call [channel]` | Join or move to your voice channel |
| `/leave` | Leave the voice channel cleanly |
| `/vcstatus` | Show connection, channel, player state, and current track |
| `/nowplaying` | Show the current track and position |
| `/queue` | Show the upcoming tracks |
| `/skip` | Skip the current track |
| `/stop` | Stop playback and clear the queue |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/volume <0-100>` | Set the player volume |
| `/loop <off\|track\|queue>` | Disable looping, repeat the track, or repeat the queue |
| `/shuffle` | Shuffle upcoming tracks |
| `/remove <position>` | Remove one track from the queue |
| `/clear` | Clear upcoming tracks without disconnecting |
| `/seek <seconds>` | Jump to a position in the current track |
| `/replay` | Restart the current track |
| `/ping` | Measure gateway latency and real Discord round-trip latency |

Prefix shortcuts include `!p` for `!play`, `!q` for `!queue`, `!np` for
`!nowplaying`, `!next` for `!skip`, `!vol` for `!volume`, `!rm` for `!remove`, and
`!restart` for `!replay`.

## Project layout

```text
main.py                 Production entrypoint
config.py               Environment-backed settings
bot.py                  Discord and Lavalink lifecycle
commands/general.py     Ping and help commands
commands/help.py        Help and real latency ping commands
commands/voice.py       Voice calling, moving, reconnecting, and status
commands/music.py       Music and queue commands
requirements.txt        Python dependencies
.devcontainer/          VS Code Python development container
```
