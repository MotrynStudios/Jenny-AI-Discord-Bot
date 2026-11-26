"""Moderation and lightweight community commands."""

import asyncio
from datetime import timedelta
from secrets import randbelow

import discord
from discord import app_commands


async def result(interaction, title, detail, *, error=False, private=False):
    """Send one visible result whether or not the command deferred."""
    embed = discord.Embed(
        title=title, description=detail,
        color=discord.Color.red() if error else discord.Color.blurple(),
    )
    send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await send(embed=embed, ephemeral=private, allowed_mentions=discord.AllowedMentions.none())


async def moderate(interaction, member, permission, title, action, reason):
    """Check both Discord role hierarchies before invoking a moderation action."""
    guild = interaction.guild
    actor = interaction.user
    me = guild.me
    effective = interaction.permissions
    if not getattr(effective, permission) or not me or not getattr(me.guild_permissions, permission):
        return await result(interaction, "Permission denied", "You and Jenny both need that permission.", error=True)
    if (member.id in {actor.id, guild.owner_id, me.id}
            or (actor.id != guild.owner_id and actor.top_role <= member.top_role)
            or me.top_role <= member.top_role):
        return await result(interaction, "Permission denied", "Check the server's role order and the selected member.", error=True)
    if permission == "moderate_members" and (member.bot or member.guild_permissions.administrator):
        return await result(interaction, "Timeout unavailable", "Discord cannot time out bots or administrators.", error=True)
    await interaction.response.defer()
    audit = f"{actor} ({actor.id}): {reason or 'No reason specified'}"[:512]
    try:
        await action(audit)
    except discord.HTTPException:
        return await result(interaction, "Moderation failed", "Discord rejected the action. Check roles and retry.", error=True)
    await result(interaction, title, f"{discord.utils.escape_markdown(member.display_name)} · {reason or 'No reason specified'}")


def dice_face(number):
    top = "*     *" if number >= 4 else "*      " if number >= 2 else "       "
    bottom = "*     *" if number >= 4 else "      *" if number >= 2 else "       "
    middle = "*     *" if number == 6 else "   *   " if number % 2 else "       "
    return "\n".join((" +---------+", f" | {top} |", f" | {middle} |", f" | {bottom} |", " +---------+"))


def register_community_commands(tree):
    @tree.command(description="Ban a member from the server")
    @app_commands.default_permissions(ban_members=True)
    async def ban(interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 0, 350] = ""):
        await moderate(interaction, member, "ban_members", "Member banned",
                       lambda audit: member.ban(reason=audit, delete_message_seconds=0), reason)

    @tree.command(description="Kick a member from the server")
    @app_commands.default_permissions(kick_members=True)
    async def kick(interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 0, 350] = ""):
        await moderate(interaction, member, "kick_members", "Member kicked",
                       lambda audit: member.kick(reason=audit), reason)

    @tree.command(description="Time out a member in minutes, or use zero to clear a timeout")
    @app_commands.default_permissions(moderate_members=True)
    async def timeout(interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 0, 40320], reason: app_commands.Range[str, 0, 350] = ""):
        await moderate(interaction, member, "moderate_members",
                       f"Timed out for {minutes} minutes" if minutes else "Timeout removed",
                       lambda audit: member.timeout(timedelta(minutes=minutes) if minutes else None, reason=audit), reason)

    @tree.command(description="Show answers to common Jenny questions")
    async def faq(interaction: discord.Interaction):
        await result(interaction, "Jenny FAQ", (
            "**How do I talk to Jenny?** Mention Jenny or say her name. DMs work too.\n"
            "**How does voice work?** Start the optional voice service, join a channel, and use /joinvc. "
            "Say Jenny's name to get her attention. Voice notes get text answers.\n"
            "**Where do pack answers come from?** /mods, /modcontents, and /recipe browse the imported pack. "
            "Extraction has been tested with 1.21.1 NeoForge. Other versions and loaders can have gaps.\n"
            "**How do I control my data?** /mymemory shows your saved summary. /forgetmydata clears Jenny's conversation memory for you.\n"
            "**How do I report a bug?** Use /report or follow the repository link in /invite."
        ))

    @tree.command(description="Flip a fair coin")
    async def coinflip(interaction: discord.Interaction):
        face = ("Heads", "Tails")[randbelow(2)]
        await interaction.response.send_message(embed=discord.Embed(title="🪙 Flipping…"))
        await asyncio.sleep(0.65)
        await interaction.edit_original_response(embed=discord.Embed(title=f"🪙 {face}", color=discord.Color.gold()))

    @tree.command(description="Roll up to five six-sided dice")
    async def roll(interaction: discord.Interaction, dice: app_commands.Range[int, 1, 5] = 1):
        values = [randbelow(6) + 1 for _ in range(dice)]
        rows = "\n".join(" ".join(row) for row in zip(*(dice_face(value).splitlines() for value in values)))
        await result(interaction, f"🎲 Total {sum(values)}", f"```\n{rows}\n```")
