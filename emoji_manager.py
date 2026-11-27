"""Centralized custom-emoji mapping and rendering helpers.

emojis.txt is the single source of truth for every Telegram-facing UI path.
"""

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

    seen_ids: dict[str, str] = {}
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
        if emoji in mapping:
            logger.warning("Duplicate emoji %r at %s:%s; keeping first mapping", emoji, path, line_number)
            continue
        if custom_id in seen_ids:
            logger.warning(
                "Custom emoji ID %s is shared by %r and %r in %s; keeping both existing mappings",
                custom_id, seen_ids[custom_id], emoji, path,
            )
        else:
            seen_ids[custom_id] = emoji
        mapping[emoji] = custom_id

    logger.info("Loaded %d custom emoji mappings from %s", len(mapping), path)
    return mapping


CUSTOM_EMOJI_IDS = load_emoji_map()
_SORTED_EMOJIS = sorted(CUSTOM_EMOJI_IDS, key=len, reverse=True)


def custom_emoji_id_for(emoji: str) -> str | None:
    """Return the configured ID for one exact emoji glyph."""
    return CUSTOM_EMOJI_IDS.get(emoji)


def apply_custom_emojis(rich_html: str) -> str:
    """Convert mapped emoji in Telegram HTML into custom-emoji tags."""
    for emoji in _SORTED_EMOJIS:
        custom_id = CUSTOM_EMOJI_IDS[emoji]
        rich_html = rich_html.replace(
            emoji, f'<tg-emoji emoji-id="{custom_id}">{emoji}</tg-emoji>'
        )
    return rich_html


def rich_text_with_custom_emojis(value: str):
    """Return Telegram rich-text parts, replacing every mapped emoji occurrence."""
    parts = []
    remaining = str(value)
    while remaining:
        match = None
        match_pos = len(remaining)
        for emoji in _SORTED_EMOJIS:
            pos = remaining.find(emoji)
            if pos != -1 and pos < match_pos:
                match = (emoji, CUSTOM_EMOJI_IDS[emoji])
                match_pos = pos
        if match is None:
            parts.append(remaining)
            break
        emoji, custom_id = match
        if match_pos:
            parts.append(remaining[:match_pos])
        parts.append({
            "type": "custom_emoji",
            "custom_emoji_id": custom_id,
            "alternative_text": emoji,
        })
        remaining = remaining[match_pos + len(emoji):]
    return parts or [str(value)]


def convert_rich_blocks(blocks):
    """Recursively convert text in rich-message blocks, including nested cells."""
    def convert(value, parent_key=None):
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "button" and isinstance(item, dict):
                    button = dict(item)
                    label = button.get("text")
                    if isinstance(label, str):
                        leading, custom_id = get_button_custom_emoji(label)
                        if leading and custom_id:
                            button["text"] = label[len(leading):].lstrip()
                            button["icon_custom_emoji_id"] = custom_id
                    result[key] = button
                elif key == "text" and isinstance(item, str):
                    result[key] = rich_text_with_custom_emojis(item)
                else:
                    result[key] = convert(item, key)
            return result
        return value

    return convert(blocks)


def get_button_custom_emoji(text: str) -> tuple[str | None, str | None]:
    """Return (leading emoji, custom ID) for Telegram's button-icon field."""
    for emoji in _SORTED_EMOJIS:
        if text.startswith(emoji):
            return emoji, CUSTOM_EMOJI_IDS[emoji]
    return None, None
