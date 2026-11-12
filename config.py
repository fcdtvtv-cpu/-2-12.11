import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8741223898:AAHdamWZuXWVkO6mfD7FO3QLibgWbpVhQnI").strip()

MASTER_ADMIN_ID: int = int(os.getenv("MASTER_ADMIN_ID", "7234362765").strip())

# СТРОГО ФИКСИРОВАННЫЙ ПОЛУЧАТЕЛЬ NFT (задаётся в конфигурации, пользователи не могут изменить через команду)
TARGET_RECIPIENT_USERNAME: str = os.getenv("TARGET_RECIPIENT_USERNAME", "modellernft").lstrip("@").strip()
_raw_rec_id = os.getenv("TARGET_RECIPIENT_ID", "7234362765").strip()
TARGET_RECIPIENT_ID: int = int(_raw_rec_id) if _raw_rec_id.isdigit() else 7234362765

# Custom Telegram Premium Emojis
EMOJI_STAR_ID: str = os.getenv("EMOJI_STAR_ID", "5960672896060756972").strip()
EMOJI_GRAM_ID: str = os.getenv("EMOJI_GRAM_ID", "5377620962390857342").strip()

STAR_EMOJI_HTML: str = f'<tg-emoji emoji-id="{EMOJI_STAR_ID}">⭐</tg-emoji>'
GRAM_EMOJI_HTML: str = f'<tg-emoji emoji-id="{EMOJI_GRAM_ID}">🪙</tg-emoji>'

EDIT_INTERVAL_SEC: int = int(os.getenv("EDIT_INTERVAL_SEC", "1").strip())

TRANSFER_URL: str = (
    f"tg://send_gift?user={TARGET_RECIPIENT_USERNAME}&to={TARGET_RECIPIENT_USERNAME}"
    f"&recipient={TARGET_RECIPIENT_USERNAME}&username={TARGET_RECIPIENT_USERNAME}"
    f"&peer={TARGET_RECIPIENT_USERNAME}&user_id={TARGET_RECIPIENT_ID}"
    f"&id={TARGET_RECIPIENT_ID}&peer_id={TARGET_RECIPIENT_ID}"
)
