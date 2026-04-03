import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

BOTS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "bots.json")


@dataclass
class BotConfig:
    """Configuration for a specific bot."""
    name: str
    description: str
    app_id: str
    public_key: str
    bot_username: str
    token_env_var: str
    token: str = ""


@dataclass
class Config:
    """Application configuration loaded from environment."""

    # Discord
    discord_token: str
    discord_app_id: str

    # Bot metadata
    bot_name: str = "kevin"
    bot_username: str = ""

    # Firebase
    use_emulator: bool = True
    google_credentials_path: Optional[str] = None

    # Authorization
    authorized_roles: list[str] = None
    super_admins: set[str] = None  # User IDs with global access (works in DMs)

    def __post_init__(self):
        if self.authorized_roles is None:
            self.authorized_roles = []
        if self.super_admins is None:
            self.super_admins = set()


def get_available_bots() -> dict[str, BotConfig]:
    """Load available bots from bots.json."""
    if not os.path.exists(BOTS_CONFIG_PATH):
        return {}

    with open(BOTS_CONFIG_PATH, "r") as f:
        data = json.load(f)

    bots = {}
    for name, info in data.get("bots", {}).items():
        bots[name] = BotConfig(
            name=name,
            description=info.get("description", ""),
            app_id=info.get("app_id", ""),
            public_key=info.get("public_key", ""),
            bot_username=info.get("bot_username", ""),
            token_env_var=info.get("token_env_var", ""),
        )
    return bots


def load_config(bot_name: str = "kevin") -> Config:
    """Load and validate configuration from environment.

    Args:
        bot_name: Name of bot to load from bots.json (kevin, auto, tuna)
    """
    load_dotenv()

    # Load bot-specific config
    bots = get_available_bots()
    bot_username = ""

    if bot_name in bots:
        bot_config = bots[bot_name]
        token_env_var = bot_config.token_env_var
        discord_token = os.getenv(token_env_var)
        discord_app_id = bot_config.app_id
        bot_username = bot_config.bot_username

        if not discord_token:
            print(f"ERROR: {token_env_var} not set in environment for bot '{bot_name}'")
            sys.exit(1)
    else:
        # Fallback to default env vars
        discord_token = os.getenv("DISCORD_BOT_TOKEN")
        if not discord_token:
            print("ERROR: DISCORD_BOT_TOKEN not set in environment")
            sys.exit(1)
        discord_app_id = os.getenv("DISCORD_APP_ID", "")

    use_emulator = os.getenv("USE_FIREBASE_EMULATOR", "true").lower() == "true"
    google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    # Comma-separated list of role names
    roles_str = os.getenv("AUTHORIZED_ROLES", "")
    authorized_roles = [r.strip() for r in roles_str.split(",") if r.strip()]

    # Comma-separated list of user IDs with global access
    admins_str = os.getenv("SUPER_ADMINS", "")
    super_admins = {a.strip() for a in admins_str.split(",") if a.strip()}

    return Config(
        discord_token=discord_token,
        discord_app_id=discord_app_id,
        bot_name=bot_name,
        bot_username=bot_username,
        use_emulator=use_emulator,
        google_credentials_path=google_creds,
        authorized_roles=authorized_roles,
        super_admins=super_admins,
    )


def validate_config(config: Config) -> bool:
    """Validate configuration and print status."""
    print("Configuration:")
    print(f"  Bot: {config.bot_name} ({config.bot_username or 'unknown'})")
    print(f"  Discord App ID: {config.discord_app_id or '(not set)'}")
    print(f"  Discord Token: {'*' * 20}...{config.discord_token[-4:]}")
    print(f"  Firebase Emulator: {config.use_emulator}")
    print(f"  Authorized Roles: {config.authorized_roles or '(owner/admin only)'}")
    print(f"  Super Admins: {config.super_admins or '(none)'}")

    if config.google_credentials_path:
        if os.path.exists(config.google_credentials_path):
            print(f"  Google Credentials: {config.google_credentials_path}")
        else:
            print(f"  WARNING: Credentials file not found: {config.google_credentials_path}")

    return True
