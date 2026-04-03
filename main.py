#!/usr/bin/env python3
"""
K3V|N - Discord bot with hackathon API backend.

Single runtime:
  - Discord bot (admin commands, hackathon commands, homebase support)
  - FastAPI hackathon API (background thread)

Message routing by channel:
  - Hackathon channel  → hackathon commands (!h ...)
  - Homebase channel   → homebase support (AI-powered)
  - Everywhere else    → admin commands (!ping, !help, etc.)

Usage:
    python main.py [--bot NAME] [--no-emulator]
"""
import argparse
import logging
import os
import sys
import threading

from dotenv import load_dotenv

load_dotenv()

from config import load_config, validate_config, get_available_bots
from handlers.admin import AdminHandler
from persistence.firestore_store import FirestoreStore
from platforms.discord import DiscordBot, DiscordAuthProvider

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("k3vin")

# Channel-based routing
HACKATHON_CHANNEL_ID = os.environ.get("HACKATHON_CHANNEL_ID", "")


def start_hackathon_api():
    """Start the hackathon FastAPI server in a background thread."""
    try:
        import uvicorn
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "hackathon"))
        from hackathon.api import app as hackathon_app
        from hackathon import db as hackathon_db

        hackathon_db.init_db()
        logger.info("Hackathon DB initialized")

        host = os.environ.get("HACKATHON_API_HOST", "127.0.0.1")
        port = int(os.environ.get("HACKATHON_API_PORT", "8000"))

        uvicorn.run(hackathon_app, host=host, port=port, log_level="info")
    except Exception:
        logger.exception("Failed to start hackathon API")


def main():
    available_bots = get_available_bots()
    bot_choices = list(available_bots.keys()) if available_bots else ["kevin"]
    bot_help = "Bot to run: " + ", ".join(
        f"{name} ({b.bot_username})" for name, b in available_bots.items()
    ) if available_bots else "Bot to run"

    parser = argparse.ArgumentParser(description="K3V|N Discord Bot")
    parser.add_argument(
        "--bot", "-b",
        choices=bot_choices,
        default="kevin",
        help=bot_help,
    )
    parser.add_argument(
        "--no-emulator",
        action="store_true",
        help="Use production Firestore instead of emulator",
    )
    args = parser.parse_args()

    config = load_config(bot_name=args.bot)
    if args.no_emulator:
        config.use_emulator = False
    validate_config(config)

    # Start hackathon API in background thread
    api_thread = threading.Thread(target=start_hackathon_api, daemon=True)
    api_thread.start()
    logger.info("Hackathon API starting in background")

    # Initialize Discord components
    store = None  # Firestore disabled for now

    auth = DiscordAuthProvider(
        authorized_roles=config.authorized_roles,
        super_admins=config.super_admins,
    )

    handler = AdminHandler(
        auth_provider=auth,
        app_id=config.discord_app_id,
    )

    bot = DiscordBot(
        token=config.discord_token,
        handler=handler,
        store=store,
        bot_name=args.bot,
        app_id=config.discord_app_id,
        use_emulator=config.use_emulator,
    )

    handler.set_bot_client(bot.client)
    handler.set_presence(bot.presence)
    handler.set_users(bot.users)

    logger.info("Starting K3V|N...")
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
