"""Discord controls for the optional voice service."""

import asyncio

import discord
import requests
from discord import app_commands

from community_commands import result


VOICE_API = "http://127.0.0.1:8000"


def register_voice_controls(bot):
    @bot.tree.command(description="Deafen or undeafen Jenny so she stops or resumes listening")
    @app_commands.default_permissions(administrator=True)
    async def deafen(interaction: discord.Interaction, deafened: bool = True):
        guild = interaction.guild
        member_voice = getattr(interaction.user, "voice", None)
        if not guild or not member_voice or not member_voice.channel:
            return await result(interaction, "Join voice first",
                                "You need to be in the same voice channel as Jenny.", error=True)
        if not interaction.user.guild_permissions.administrator:
            return await result(interaction, "Permission denied",
                                "Only an administrator can deafen or undeafen Jenny.", error=True)
        await interaction.response.defer(thinking=True)
        payload = {"guild_id": str(guild.id), "channel_id": str(member_voice.channel.id),
                   "action": "deafen", "deafened": deafened}
        try:
            reply = await asyncio.to_thread(requests.post, VOICE_API + "/voice/action", json=payload, timeout=12)
            data = reply.json()
            error = not reply.ok or data.get("error", False)
            detail = data.get("detail") or "The voice service did not return a result."
            if not isinstance(detail, str):
                detail = "The voice service could not validate that request."
        except (requests.RequestException, ValueError):
            error, detail = True, "The voice service did not respond. Verify that both Jenny processes are running."
        await result(interaction, "Voice action failed" if error else "Jenny voice", detail, error=error)
