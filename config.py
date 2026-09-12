import os
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

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

        parsed_uri = urlparse(values["LAVALINK_URI"])
        if parsed_uri.scheme not in {"http", "https"} or not parsed_uri.netloc:
            raise RuntimeError("LAVALINK_URI must be an HTTP(S) Lavalink server URL")
        lavalink_path = parsed_uri.path.rstrip("/").removesuffix("/v4/websocket")
        lavalink_uri = urlunparse(parsed_uri._replace(path=lavalink_path))

        return cls(
            token=values["DISCORD_TOKEN"],
            prefix=os.getenv("COMMAND_PREFIX", "!").strip() or "!",
            lavalink_uri=lavalink_uri.rstrip("/"),
            lavalink_password=values["LAVALINK_PASSWORD"],
        )


settings = Settings.from_environment()
