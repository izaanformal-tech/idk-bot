import os
import sys

sys.pycache_prefix = os.getenv("PYTHONPYCACHEPREFIX", ".pycache")

from bot import bot
from config import settings


if __name__ == "__main__":
    bot.run(settings.token)
