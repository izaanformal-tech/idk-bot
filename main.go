package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/bwmarrin/discordgo"
)

const commandPrefix = "!"

func main() {
	token := strings.TrimSpace(os.Getenv("DISCORD_TOKEN"))
	if token == "" {
		log.Fatal("DISCORD_TOKEN is not set")
	}

	bot, err := discordgo.New("Bot " + token)
	if err != nil {
		log.Fatalf("create Discord session: %v", err)
	}
	bot.Identify.Intents = discordgo.IntentsGuilds | discordgo.IntentsGuildMessages | discordgo.IntentMessageContent
	bot.AddHandler(handleReady)
	bot.AddHandler(handleMessage)

	if err := bot.Open(); err != nil {
		log.Fatalf("connect to Discord: %v", err)
	}
	defer bot.Close()

	log.Printf("bot is online as %s", bot.State.User.Username)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	log.Println("shutdown signal received")
}

func handleReady(session *discordgo.Session, event *discordgo.Ready) {
	log.Printf("connected to Discord in %d guild(s)", len(event.Guilds))
}

func handleMessage(session *discordgo.Session, message *discordgo.MessageCreate) {
	if message.Author == nil || message.Author.Bot {
		return
	}

	content := strings.TrimSpace(message.Content)
	if !strings.HasPrefix(content, commandPrefix) {
		return
	}

	command := strings.TrimSpace(strings.TrimPrefix(content, commandPrefix))
	switch strings.ToLower(command) {
	case "ping":
		_, err := session.ChannelMessageSend(message.ChannelID, "Pong!")
		if err != nil {
			log.Printf("send ping response: %v", err)
		}
	case "help":
		_, err := session.ChannelMessageSend(message.ChannelID, "Commands: `!ping`, `!help`")
		if err != nil {
			log.Printf("send help response: %v", err)
		}
	}
}
