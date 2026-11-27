"""Per-server language for text replies and Whisper recognition."""

import os

import discord
from discord import app_commands

from database import get_setting, set_setting


LANGUAGES = {
    "en": "English", "ar": "Arabic", "zh": "Chinese", "nl": "Dutch",
    "fr": "French", "de": "German", "hi": "Hindi", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "pl": "Polish",
    "pt": "Portuguese", "ru": "Russian", "es": "Spanish", "sv": "Swedish",
    "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese",
}


def language_code(guild_id=None):
    default = os.getenv("GROQ_STT_LANGUAGE", "").strip().lower() or "en"
    selected = get_setting("language", default, str(guild_id)) if guild_id else default
    return selected if selected in LANGUAGES else "en"


def language_instruction(guild_id=None, *, spoken=False):
    label = LANGUAGES[language_code(guild_id)]
    if spoken:
        return f"Use {label} for written chat copies. Use English for spoken replies because Jenny has an English voice."
    return f"Write your reply in {label}, even if the message or old conversation uses a different language."


def register_language(tree):
    @tree.command(description="Choose Jenny's chat and speech-recognition language for this server")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(code=[app_commands.Choice(name=name, value=code) for code, name in LANGUAGES.items()])
    async def language(interaction: discord.Interaction, code: str):
        set_setting("language", code, str(interaction.guild_id))
        await interaction.response.send_message(embed=discord.Embed(
            title=f"Language set to {LANGUAGES[code]}",
            description="Jenny uses this for text replies and transcription. Her spoken voice remains English.",
            color=discord.Color.blurple(),
        ))
