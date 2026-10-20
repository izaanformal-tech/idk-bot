import asyncio
import os
import sys

sys.pycache_prefix = os.getenv("PYTHONPYCACHEPREFIX", ".pycache")

from aiohttp import web

from bot import bot
from config import settings

async def health(request):
    return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        # Panel hosts like bot-hosting.net assign the port in SERVER_PORT; others use PORT.
        port = int(os.environ.get("SERVER_PORT") or os.environ.get("PORT") or "8080")
        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
        print(f"HTTP health endpoint listening on port {port}", flush=True)

        async with bot:
            await bot.start(settings.token)
    finally:
        await runner.cleanup()

if __name__ == "__main__":
  asyncio.run(main())
