"""Moderation and lightweight community commands."""

import asyncio
from collections import defaultdict
from datetime import timedelta
import json
import logging
from pathlib import Path
import re
from secrets import randbelow
import sqlite3
import time
from urllib.parse import urljoin
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import tasks

from database import get_setting, set_setting


PERIOD_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
MAX_TIMEOUT_PERIOD = 28 * PERIOD_SECONDS["d"]
TIMED_BANS_KEY = "timed_bans"
README = Path(__file__).resolve().parent.parent / "README.md"
README_URL = "https://github.com/MotrynStudios/Jenny-AI-Discord-Bot/blob/main/README.md"
LOGGER = logging.getLogger(__name__)


def parse_period(period, *, maximum=None):
    """Accept a whole number with an optional unit. Bare numbers mean minutes."""
    match = re.fullmatch(r"([0-9]+)\s*([mhdw])?", period.strip(), flags=re.I)
    if not match:
        raise ValueError("Enter a whole number with a unit, such as 30m, 2h, 3d, or 1w.")
    seconds = int(match[1]) * PERIOD_SECONDS[(match[2] or "m").lower()]
    if maximum is not None and seconds > maximum:
        raise ValueError("The maximum period for this command is 28 days (4w).")
    return seconds


def faq_pages():
    """Render the same FAQ sections readers see in the README."""
    markdown = README.read_text(encoding="utf-8")
    faq = markdown.partition("\n## FAQ\n")[2].partition("\n## ")[0]
    sections = re.split(r"(?m)^### (.+)\n", faq)
    pages = []
    for heading, body in zip(sections[1::2], sections[2::2]):
        description = re.sub(r"(?m)^#### (.+)$", r"**\1**", body).strip()
        # Discord has no base URL for links copied out of a GitHub Markdown file.
        description = re.sub(r"\]\(([^)\s]+)\)",
                             lambda match: f"]({urljoin(README_URL, match[1])})", description)
        pages.append(discord.Embed(title=f"Jenny FAQ · {heading}", description=description,
                                   color=discord.Color.blurple()))
    return pages


async def result(interaction, title, detail, *, error=False, private=False):
    """Send one visible result whether or not the command deferred."""
    embed = discord.Embed(
        title=title, description=detail,
        color=discord.Color.red() if error else discord.Color.blurple(),
    )
    send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await send(embed=embed, ephemeral=private, allowed_mentions=discord.AllowedMentions.none())


async def moderate(interaction, member, permission, title, action, reason, *, expires=None):
    """Check both Discord role hierarchies before invoking a moderation action."""
    guild = interaction.guild
    actor = interaction.user
    me = guild.me if guild else None
    effective = interaction.permissions
    if not getattr(effective, permission) or not me or not getattr(me.guild_permissions, permission):
        return await result(interaction, "Permission denied", "You and Jenny both need that permission.", error=True)
    if (member.id in {actor.id, guild.owner_id, me.id}
            or (actor.id != guild.owner_id and actor.top_role <= member.top_role)
            or me.top_role <= member.top_role):
        return await result(interaction, "Permission denied", "Check the server's role order and the selected member.", error=True)
    if permission == "moderate_members" and (member.bot or member.guild_permissions.administrator):
        return await result(interaction, "Timeout unavailable", "Discord cannot time out bots or administrators.", error=True)
    await interaction.response.defer(thinking=True)
    audit = f"{actor} ({actor.id}): {reason or 'No reason specified'}"[:512]
    try:
        await action(audit)
    except discord.HTTPException:
        return await result(interaction, "Moderation failed", "Discord rejected the action. Check roles and retry.", error=True)
    detail = f"{discord.utils.escape_markdown(member.display_name)} · {reason or 'No reason specified'}"
    if expires:
        detail += f"\nEnds <t:{expires}:R>."
    await result(interaction, title, detail)


async def load_timed_bans(guild_id):
    raw = await asyncio.to_thread(get_setting, TIMED_BANS_KEY, "{}", str(guild_id))
    return json.loads(raw)


async def save_timed_bans(guild_id, entries):
    await asyncio.to_thread(set_setting, TIMED_BANS_KEY, json.dumps(entries), str(guild_id))


async def expire_timed_bans(guild, entries, now):
    """Only remove the exact ban Jenny scheduled, including after a restart."""
    for member_id, entry in list(entries.items()):
        if entry["expires"] > now:
            continue
        target = discord.Object(id=int(member_id))
        try:
            ban = await guild.fetch_ban(target)
            if (ban.reason or "").startswith(entry["marker"]):
                await guild.unban(target, reason="Jenny timed ban expired")
        except discord.NotFound:
            pass  # Already removed by a moderator or an earlier attempt.
        except discord.HTTPException as error:
            LOGGER.warning("Could not expire timed ban in guild %s: HTTP %s", guild.id, error.status)
            continue
        del entries[member_id]


def dice_face(number):
    top = "*     *" if number >= 4 else "*      " if number >= 2 else "       "
    bottom = "*     *" if number >= 4 else "      *" if number >= 2 else "       "
    middle = "*     *" if number == 6 else "   *   " if number % 2 else "       "
    return ("+---------+", f"| {top} |", f"| {middle} |", f"| {bottom} |", "+---------+")


def register_community_commands(bot):
    tree = bot.tree
    ban_locks = defaultdict(asyncio.Lock)

    @tasks.loop(seconds=15)
    async def expire_bans():
        now = time.time()
        for guild in bot.guilds:
            if not guild.me or not guild.me.guild_permissions.ban_members:
                continue
            async with ban_locks[guild.id]:
                try:
                    entries = await load_timed_bans(guild.id)
                    count = len(entries)
                    await expire_timed_bans(guild, entries, now)
                    if len(entries) != count:
                        await save_timed_bans(guild.id, entries)
                except (sqlite3.Error, ValueError) as error:
                    LOGGER.warning("Could not read or save timed bans in guild %s: %s", guild.id, type(error).__name__)

    @bot.listen("on_ready")
    async def start_ban_expiration():
        if not expire_bans.is_running():
            expire_bans.start()

    @tree.command(description="Ban a member from the server")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(period="Ban length, such as 30m, 2h, 3d, or 1w. Leave blank for a permanent ban.")
    async def ban(interaction: discord.Interaction, member: discord.Member,
                  period: app_commands.Range[str, 1, 16] = "", reason: app_commands.Range[str, 0, 350] = ""):
        expires = None
        if period:
            try:
                seconds = parse_period(period)
                if not seconds:
                    raise ValueError("Enter a positive ban period or leave it blank for a permanent ban.")
                expires = int((discord.utils.utcnow() + timedelta(seconds=seconds)).timestamp())
            except (ValueError, OverflowError) as error:
                detail = "Enter a shorter ban period." if isinstance(error, OverflowError) else str(error)
                return await result(interaction, "Invalid period", detail, error=True)
        guild = interaction.guild

        async def apply_ban(audit):
            async with ban_locks[guild.id]:
                if expires is not None:
                    marker = f"[Jenny timed ban {uuid4().hex}]"
                    entries = await load_timed_bans(guild.id)
                    entries[str(member.id)] = {"expires": expires, "marker": marker}
                    # Write the timer before the Discord call so an interrupted request can still expire.
                    await save_timed_bans(guild.id, entries)
                    audit = f"{marker} {audit}"[:512]
                await member.ban(reason=audit, delete_message_seconds=0)

        await moderate(interaction, member, "ban_members", "Member banned", apply_ban, reason, expires=expires)

    @tree.command(description="Kick a member from the server")
    @app_commands.default_permissions(kick_members=True)
    async def kick(interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 0, 350] = ""):
        await moderate(interaction, member, "kick_members", "Member kicked",
                       lambda audit: member.kick(reason=audit), reason)

    @tree.command(description="Time out a member or clear a timeout")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(period="Timeout length, such as 30m, 2h, 3d, or 1w. Use 0 to clear.")
    async def timeout(interaction: discord.Interaction, member: discord.Member,
                      period: app_commands.Range[str, 1, 16], reason: app_commands.Range[str, 0, 350] = ""):
        try:
            seconds = parse_period(period, maximum=MAX_TIMEOUT_PERIOD)
        except ValueError as error:
            return await result(interaction, "Invalid period", str(error), error=True)
        deadline = discord.utils.utcnow() + timedelta(seconds=seconds) if seconds else None
        await moderate(interaction, member, "moderate_members",
                       "Member timed out" if deadline else "Timeout removed",
                       lambda audit: member.timeout(deadline, reason=audit), reason,
                       expires=int(deadline.timestamp()) if deadline else None)

    @tree.command(description="Flip a fair coin")
    async def coinflip(interaction: discord.Interaction):
        face = ("Heads", "Tails")[randbelow(2)]
        await interaction.response.send_message(embed=discord.Embed(title="🪙 Flipping…"))
        await asyncio.sleep(0.65)
        await interaction.edit_original_response(embed=discord.Embed(title=f"🪙 {face}", color=discord.Color.gold()))

    @tree.command(description="Roll up to five six-sided dice")
    @app_commands.describe(dice="Number of six-sided dice to roll (1 to 5)")
    async def roll(interaction: discord.Interaction, dice: app_commands.Range[int, 1, 5] = 1):
        values = [randbelow(6) + 1 for _ in range(dice)]
        # Keep a line short enough for a narrow Discord embed.
        rows = ["\n".join("  ".join(row) for row in zip(*(dice_face(value) for value in values[index:index + 2])))
                for index in range(0, len(values), 2)]
        await result(interaction, f"🎲 Total {sum(values)}", "```\n" + "\n\n".join(rows) + "\n```")
