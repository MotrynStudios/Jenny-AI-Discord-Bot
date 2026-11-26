"""Discord ownership checks and commands for the local supervisor."""

import asyncio
import json
import os

import discord
import requests
from discord import app_commands

from community_commands import result
from release_update import ROOT, REPOSITORY, ReleaseError, check_checkout, git, latest_release, local_version, release_commit


async def is_installation_owner(bot, user_id):
    """An owner of an unrelated server cannot restart or update the shared process."""
    application = await bot.application_info()
    owner_id = application.team.owner_id if application.team else application.owner.id
    return owner_id == user_id


def register_operations(bot):
    request_lock = asyncio.Lock()
    result_path = ROOT / "logs" / "supervisor-result.json"
    request_path = ROOT / "logs" / "supervisor-request.json"

    async def authorize_host_action(interaction):
        try:
            if await is_installation_owner(bot, interaction.user.id):
                return True
        except discord.HTTPException:
            await result(interaction, "Ownership check unavailable", "Discord could not verify the application owner.",
                         error=True, private=True)
            return False
        await result(interaction, "Permission denied", "This action requires ownership of the Discord server and Jenny application.",
                     error=True, private=True)
        return False

    async def schedule(interaction, action, **details):
        session = os.getenv("JENNY_SUPERVISOR_SESSION")
        if not session:
            return await result(interaction, "Supervisor unavailable",
                                "Start Jenny through tools/run_supervised.py to use this command.", error=True, private=True)
        async with request_lock:
            try:
                if request_path.is_file():
                    existing = json.loads(request_path.read_text(encoding="utf-8"))
                    if existing.get("session") == session:
                        return await result(interaction, "Action pending", "Wait for the existing request.", error=True, private=True)
                payload = {"session": session, "action": action, "guild": interaction.guild_id,
                           "channel": interaction.channel_id, **details}
                temp = request_path.with_suffix(".new")
                temp.write_text(json.dumps(payload), encoding="utf-8")
                temp.replace(request_path)
            except (OSError, ValueError, AttributeError):
                return await result(interaction, "Supervisor request unavailable",
                                    "Jenny could not write the supervisor request.", error=True, private=True)
            try:
                await result(interaction, "Update scheduled" if action == "update" else "Restart scheduled",
                             "I’ll confirm here after reconnecting.")
            finally:
                await bot.close()

    @bot.tree.command(description="Restart Jenny under the local supervisor")
    @app_commands.default_permissions(administrator=True)
    async def restart(interaction: discord.Interaction):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            return await result(interaction, "Permission denied", "Only the server owner can request a restart.",
                                error=True, private=True)
        await interaction.response.defer(thinking=True)
        if await authorize_host_action(interaction):
            await schedule(interaction, "restart")

    @bot.tree.command(description="Check the latest release, or install it with install set to true")
    @app_commands.default_permissions(administrator=True)
    async def update(interaction: discord.Interaction, install: bool = False):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            return await result(interaction, "Permission denied", "Only the server owner can request an update.",
                                error=True, private=True)
        await interaction.response.defer(thinking=True)
        if install and not await authorize_host_action(interaction):
            return
        try:
            release = await asyncio.to_thread(latest_release)
            tag = release["tag_name"]
            notes = str(release.get("body") or "See GitHub for release notes.")[:1300]
            if install:
                await asyncio.to_thread(check_checkout)
                target = await asyncio.to_thread(release_commit, tag)
                if await asyncio.to_thread(git, ROOT, "rev-parse", "HEAD") == target:
                    return await result(interaction, f"Already on {tag}", notes)
            else:
                local_label, local = await asyncio.to_thread(local_version)
        except (requests.RequestException, ValueError):
            return await result(interaction, "Could not check for updates", "GitHub did not return a valid published release.", error=True)
        except ReleaseError as error:
            return await result(interaction, "Could not install update", str(error), error=True, private=True)
        if install:
            await schedule(interaction, "update", tag=tag, target=target, notes=notes)
            return
        comparison = "Matches the latest release version." if local.removeprefix("v") == tag.removeprefix("v") else "The versions differ."
        await result(interaction, f"Latest release · {tag}",
                     f"**{local_label}** · {local}\n{comparison}\n\n{notes}"
                     f"\n\n[Release on GitHub](https://github.com/{REPOSITORY}/releases/latest)")

    @bot.listen("on_ready")
    async def announce_supervisor_result():
        async with request_lock:
            if not result_path.is_file():
                return
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                if payload["session"] != os.getenv("JENNY_SUPERVISOR_SESSION"):
                    return
                channel = bot.get_channel(payload["channel"]) or await bot.fetch_channel(payload["channel"])
                if getattr(channel, "guild", None) is None or channel.guild.id != payload["guild"]:
                    return
                embed = discord.Embed(title=payload["title"], description=payload["detail"][:3400],
                                      color=discord.Color.red() if payload["error"] else discord.Color.green())
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                result_path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, discord.HTTPException) as error:
                print(f"[SUPERVISOR] Announcement failed: {type(error).__name__}")
