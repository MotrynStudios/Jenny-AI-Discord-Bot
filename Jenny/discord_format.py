"""Shared Discord emoji and color parsing for forms."""

import re
import unicodedata

import discord


EMOJI_ALIASES = {
    "sob": "😭", "cry": "😢", "joy": "😂", "smile": "😄", "grin": "😁",
    "heart": "❤️", "broken_heart": "💔", "skull": "💀", "fire": "🔥", "sparkles": "✨",
    "wave": "👋", "tada": "🎉", "eyes": "👀", "ticket": "🎫", "bug": "🐛",
    "white_check_mark": "✅", "x": "❌", "question": "❓", "warning": "⚠️",
    "thumbsup": "👍", "+1": "👍", "thumbsdown": "👎", "-1": "👎",
    "thinking": "🤔", "heart_eyes": "😍", "pray": "🙏", "skull_crossbones": "☠️",
    "computer": "💻", "laptop": "💻", "desktop": "🖥️", "desktop_computer": "🖥️",
}
SHORTCODE = re.compile(r"(?<![<\w]):([a-zA-Z0-9_+-]+):(?![\w\d])")
CHANNEL_TOKEN = re.compile(r"\{(?:(?:channel|channel_id):)?(\d{15,22})\}")


def expand_channel_mentions(text, guild):
    """Resolve braced IDs against this server, including threads."""
    def replace(match):
        target = guild.get_channel_or_thread(int(match[1]))
        return target.mention if target else match[0]

    return CHANNEL_TOKEN.sub(replace, text) if guild else text


def expand_emojis(text, guild):
    available = {emoji.name: str(emoji) for emoji in guild.emojis if emoji.is_usable()} if guild else {}

    def replace(match):
        name = match[1]
        if name in available:
            return available[name]
        if name in EMOJI_ALIASES:
            return EMOJI_ALIASES[name]
        try:
            char = unicodedata.lookup(name.replace("_", " ").upper())
            return char if unicodedata.category(char) == "So" else match[0]
        except KeyError:
            return match[0]

    return SHORTCODE.sub(replace, text)


def button_emoji(text, guild):
    value = expand_emojis(text.strip(), guild)
    if not value:
        return None
    emoji = discord.PartialEmoji.from_str(value)
    if emoji.id:
        available = guild.get_emoji(emoji.id) if guild else None
        if available and available.is_usable():
            return str(available)
        raise ValueError("Choose a custom emoji from this server.")
    if re.fullmatch(r"(?:[0-9#*]\uFE0F?\u20E3|[\U0001F1E6-\U0001F1FF]{2})", value):
        return value
    parts = value.split("\u200d")
    if all(part and unicodedata.category(part[0]) == "So" and re.fullmatch(
        r"[\uFE0E\uFE0F]?[\U0001F3FB-\U0001F3FF]?(?:[\U000E0020-\U000E007E]+\U000E007F)?", part[1:]
    ) for part in parts):
        return value
    raise ValueError("Enter one emoji, a shortcode such as :sob:, or leave the emoji blank.")


def embed_color_picker(current):
    """Choose a named embed color while allowing old custom colors to survive edits."""
    colors = [
        ("Blurple", "🔷", 0x5865F2), ("Blue", "🔵", 0x3498DB), ("Teal", "💠", 0x1ABC9C),
        ("Green", "🟢", 0x2ECC71), ("Gold", "🟡", 0xF1C40F), ("Orange", "🟠", 0xE67E22),
        ("Red", "🔴", 0xED4245), ("Pink", "🌸", 0xEB79B4), ("Purple", "🟣", 0x9B59B6),
        ("Slate", "🔘", 0x607D8B), ("Black", "⚫", 0x23272A), ("White", "⚪", 0xFFFFFF),
    ]
    if all(value != current for _, _, value in colors):
        colors.insert(0, ("Keep existing color", "🎨", current))
    options = [discord.SelectOption(label=label, value=str(value), emoji=icon, default=value == current)
               for label, icon, value in colors]
    return discord.ui.Label(text="Embed color", component=discord.ui.Select(options=options))
