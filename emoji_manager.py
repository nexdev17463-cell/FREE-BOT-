"""Centralized custom-emoji mapping and rendering helpers."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)
_EMOJI_FILE = Path(__file__).with_name("emojis.txt")


def load_emoji_map(path: Path = _EMOJI_FILE) -> dict[str, str]:
    """Load emoji=custom_id entries once; malformed lines are ignored."""
    mapping: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        logger.warning("Emoji mapping file %s not found; using normal emojis", path)
        return mapping
    except OSError:
        logger.exception("Could not read emoji mapping file %s", path)
        return mapping

    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            logger.warning("Ignoring malformed emoji mapping at %s:%s", path, line_number)
            continue
        emoji, custom_id = (part.strip() for part in line.split("=", 1))
        if not emoji or not custom_id.isdigit():
            logger.warning("Ignoring invalid emoji mapping at %s:%s", path, line_number)
            continue
        mapping[emoji] = custom_id
    logger.info("Loaded %d custom emoji mappings from %s", len(mapping), path)
    return mapping


CUSTOM_EMOJI_IDS = load_emoji_map()
_SORTED_EMOJIS = sorted(CUSTOM_EMOJI_IDS, key=len, reverse=True)


def apply_custom_emojis(rich_html: str) -> str:
    """Convert mapped emoji in rich HTML into Telegram custom-emoji tags."""
    for emoji in _SORTED_EMOJIS:
        custom_id = CUSTOM_EMOJI_IDS[emoji]
        rich_html = rich_html.replace(emoji, f'<tg-emoji emoji-id="{custom_id}">{emoji}</tg-emoji>')
    return rich_html


def get_button_custom_emoji(text: str) -> tuple[str | None, str | None]:
    """Return (leading emoji, custom ID) for Telegram's button-icon field."""
    for emoji in _SORTED_EMOJIS:
        if text.startswith(emoji):
            return emoji, CUSTOM_EMOJI_IDS[emoji]
    return None, None
