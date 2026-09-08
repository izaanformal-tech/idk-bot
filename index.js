const { Client, Events, GatewayIntentBits } = require('discord.js');

const token = process.env.DISCORD_TOKEN?.trim();

if (!token) {
  console.error('DISCORD_TOKEN is not set');
  process.exit(1);
}

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

client.once(Events.ClientReady, (readyClient) => {
  console.log(`Bot is online as ${readyClient.user.tag}`);
});

client.on(Events.MessageCreate, async (message) => {
  if (message.author.bot) return;

  const content = message.content.trim();
  if (!content.startsWith('!')) return;

  const command = content.slice(1).trim().toLowerCase();

  if (command === 'ping') {
    await message.channel.send('Pong!');
  } else if (command === 'help') {
    await message.channel.send('Commands: `!ping`, `!help`');
  }
});

client.login(token).catch((error) => {
  console.error('Failed to connect to Discord:', error.message);
  process.exit(1);
});