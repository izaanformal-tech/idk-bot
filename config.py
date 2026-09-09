import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    token: str
    prefix: str
    lavalink_uri: str
    lavalink_password: str

    @classmethod
    def from_environment(cls) -> "Settings":
        values = {
            "DISCORD_TOKEN": os.getenv("DISCORD_TOKEN", "").strip(),
            "LAVALINK_URI": os.getenv("LAVALINK_URI", "").strip(),
            "LAVALINK_PASSWORD": os.getenv("LAVALINK_PASSWORD", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Missing environment variable(s): {', '.join(missing)}")

        return cls(
            token=values["DISCORD_TOKEN"],
            prefix=os.getenv("COMMAND_PREFIX", "!").strip() or "!",
            lavalink_uri=values["LAVALINK_URI"],
            lavalink_password=values["LAVALINK_PASSWORD"],
        )


settings = Settings.from_environment()
