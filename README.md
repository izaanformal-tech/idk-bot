# idk-bot

A small JavaScript Discord bot that stays online and responds to `!ping` and `!help`.

## Run locally

Install Node.js 18 or newer, then set the bot token and start the process:

```sh
export DISCORD_TOKEN="your-discord-bot-token"
npm install
npm start
```

The bot needs the **Message Content Intent** enabled in the Discord Developer Portal. Invite it with the `bot` scope and the permissions needed to view channels and send messages.

## Host on TopBot

1. Create or select the bot service on TopBot.
2. Connect the repository `izaanformal-tech/idk-bot`.
3. Set the environment variable `DISCORD_TOKEN` to the bot token. Do not put the token in this repository.
4. Use the start command `npm start`.
5. Deploy and check the service logs for `bot is online`.

The process is designed to run continuously and exits cleanly when TopBot stops or restarts it.# idk-bot