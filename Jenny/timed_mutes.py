"""Timed, server-wide communication mutes stored independently of Discord timeouts."""

import asyncio
import json
import logging
import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import tasks

from community_commands import result
from database import get_setting, set_setting


LOGGER = logging.getLogger(__name__)
SETTINGS_KEY = "timed_mutes"
ROLE_NAMES = {"text": "Mute (Text)", "voice": "Mute (Voice)", "both": "Mute"}
TEXT_FLAGS = ("send_messages", "send_messages_in_threads", "create_public_threads", "create_private_threads")
VOICE_FLAGS = ("speak", "send_voice_messages")


def mute_flags(mode):
    return {"text": TEXT_FLAGS, "voice": VOICE_FLAGS, "both": TEXT_FLAGS + VOICE_FLAGS}[mode]


async def load_state(guild):
    raw = await asyncio.to_thread(get_setting, SETTINGS_KEY, None, str(guild.id))
    return json.loads(raw) if raw else {"roles": {}, "members": {}}


async def save_state(guild, state):
    await asyncio.to_thread(set_setting, SETTINGS_KEY, json.dumps(state), str(guild.id))


def permission_scopes(guild):
    # Synced children inherit category changes. Leave them synced.
    return [channel for channel in guild.channels if isinstance(channel, discord.CategoryChannel)
            or not channel.permissions_synced]


def overrides_to_save(channel, member, flags):
    """Role and member allows can override a deny on the mute role."""
    own = channel.overwrites_for(member)
    role_overwrites = [channel.overwrites_for(role) for role in member.roles if not role.is_default()]
    return {name: getattr(own, name) for name in flags if getattr(own, name) is not False
            and (getattr(own, name) is True or any(getattr(item, name) is True for item in role_overwrites))}


async def write_flags(channel, target, updates, reason):
    overwrite = channel.overwrites_for(target)
    if all(getattr(overwrite, name) == value for name, value in updates.items()):
        return
    overwrite.update(**updates)
    await channel.set_permissions(target, overwrite=None if overwrite.is_empty() else overwrite, reason=reason)


async def mute_role(guild, state, mode):
    role = guild.get_role(state["roles"].get(mode))
    if role and (role.managed or role >= guild.me.top_role or role.permissions.value):
        raise ValueError(f"The configured {ROLE_NAMES[mode]} role must be below Jenny and have no server-wide permissions.")
    if not role:
        role = await guild.create_role(name=ROLE_NAMES[mode], permissions=discord.Permissions.none(),
                                       reason="Create Jenny's timed mute role")
        state["roles"][mode] = role.id
        await save_state(guild, state)
    return role


async def enforce_member(guild, state, member, entry, channel, reason):
    changes = overrides_to_save(channel, member, mute_flags(entry["mode"]))
    if not changes:
        return
    snapshots = entry["overwrites"].setdefault(str(channel.id), {})
    for name, value in changes.items():
        snapshots.setdefault(name, value)
    await save_state(guild, state)
    await write_flags(channel, member, dict.fromkeys(changes, False), reason)


async def enforce(guild, state, member, role, entry, channels, reason):
    flags = mute_flags(entry["mode"])
    denied = dict.fromkeys(flags, False)
    # Save only fields changed by Jenny, so unmute preserves unrelated permissions.
    for channel in channels:
        if not channel.permissions_for(member).view_channel and not channel.permissions_for(guild.me).manage_roles:
            continue  # The target cannot communicate in this inaccessible channel.
        await write_flags(channel, role, denied, reason)
        await enforce_member(guild, state, member, entry, channel, reason)


async def release(bot, guild, state, member_id, reason, member=None):
    entry = state["members"][member_id]
    if not member:
        try:
            member = await guild.fetch_member(int(member_id))
        except discord.NotFound:
            member = None
    role = guild.get_role(entry["role"])
    if member and role:
        await member.remove_roles(role, reason=reason)
    if entry["overwrites"]:
        target = member
        if not target:
            try:
                target = await bot.fetch_user(int(member_id))
            except discord.NotFound:
                target = None
        if target:
            # Use fresh overwrites so an immediate unmute cannot restore stale values.
            channels = {str(channel.id): channel for channel in await guild.fetch_channels()}
            for channel_id, prior in entry["overwrites"].items():
                channel = channels.get(channel_id)
                if channel:
                    current = channel.overwrites_for(target)
                    restore = {name: value for name, value in prior.items() if getattr(current, name) is False}
                    await write_flags(channel, target, restore, reason)
    del state["members"][member_id]
    await save_state(guild, state)


def register_mute(bot):
    locks = defaultdict(asyncio.Lock)

    @tasks.loop(seconds=15)
    async def expire_mutes():
        now = time.time()
        for guild in bot.guilds:
            async with locks[guild.id]:
                state = await load_state(guild)
                for member_id, entry in list(state["members"].items()):
                    if entry["expires"] > now:
                        continue
                    try:
                        await release(bot, guild, state, member_id, "Jenny timed mute expired")
                    except discord.HTTPException as error:
                        LOGGER.warning("Could not expire timed mute in guild %s: status %s", guild.id, error.status)

    @bot.listen("on_ready")
    async def start_mute_expiration():
        if not expire_mutes.is_running():
            expire_mutes.start()

    async def refresh_mute_channel(channel):
        guild = channel.guild
        async with locks[guild.id]:
            channel = guild.get_channel(channel.id)
            if not channel or channel.permissions_synced:
                return
            state = await load_state(guild)
            try:
                for mode, role_id in state["roles"].items():
                    role = guild.get_role(role_id)
                    if role:
                        await write_flags(channel, role, dict.fromkeys(mute_flags(mode), False), "Sync Jenny mute roles")
                for member_id, entry in state["members"].items():
                    member = guild.get_member(int(member_id))
                    if member and entry["expires"] > time.time():
                        await enforce_member(guild, state, member, entry, channel, "Sync Jenny mute overrides")
            except discord.HTTPException as error:
                LOGGER.warning("Could not sync mute roles in channel %s: status %s", channel.id, error.status)

    @bot.listen("on_guild_channel_create")
    async def mute_new_channel(channel):
        await refresh_mute_channel(channel)

    @bot.listen("on_guild_channel_update")
    async def mute_changed_channel(before, after):
        if before.overwrites != after.overwrites:
            await refresh_mute_channel(after)


    @bot.listen("on_member_join")
    async def restore_active_mute(member):
        guild = member.guild
        async with locks[guild.id]:
            state = await load_state(guild)
            entry = state["members"].get(str(member.id))
            if not entry or entry["expires"] <= time.time():
                return
            try:
                role = await mute_role(guild, state, entry["mode"])
                entry["role"] = role.id
                await enforce(guild, state, member, role, entry, permission_scopes(guild), "Restore Jenny timed mute")
                await save_state(guild, state)
                await member.add_roles(role, reason="Restore Jenny timed mute after rejoining")
            except (discord.HTTPException, ValueError) as error:
                LOGGER.warning("Could not restore timed mute in guild %s: %s", guild.id, type(error).__name__)

    @bot.tree.command(description="Mute text, voice or both for a number of minutes. Use zero to unmute.")
    @app_commands.default_permissions(manage_roles=True, moderate_members=True)
    @app_commands.choices(mode=[app_commands.Choice(name=name.title(), value=name) for name in ROLE_NAMES])
    async def mute(interaction: discord.Interaction, member: discord.Member,
                   minutes: app_commands.Range[int, 0, 40320],
                   mode: str = "both", reason: app_commands.Range[str, 0, 350] = ""):
        guild, actor = interaction.guild, interaction.user
        perms = interaction.permissions
        if (not guild or not guild.me or not perms.manage_roles or not perms.moderate_members
                or not guild.me.guild_permissions.manage_roles):
            return await result(interaction, "Permission denied",
                                "You need Manage Roles and Moderate Members. Jenny needs Manage Roles.", error=True)
        if (member.id in {actor.id, guild.owner_id, guild.me.id}
                or member.guild_permissions.administrator
                or (actor.id != guild.owner_id and actor.top_role <= member.top_role)
                or guild.me.top_role <= member.top_role):
            return await result(interaction, "Mute unavailable", "Check the selected member and the server role order.", error=True)

        await interaction.response.defer(thinking=True)
        audit = f"{actor} ({actor.id}): {reason or 'Jenny timed mute'}"[:512]
        member_id = str(member.id)
        async with locks[guild.id]:
            state = await load_state(guild)
            active = state["members"].get(member_id)
            try:
                if not minutes:
                    if not active:
                        return await result(interaction, "No active mute", "Jenny has no timed mute for that member.")
                    await release(bot, guild, state, member_id, audit, member)
                    return await result(interaction, "Mute removed", discord.utils.escape_markdown(member.display_name))

                if active and active["mode"] != mode:
                    await release(bot, guild, state, member_id, "Replace timed mute", member)
                    active = None
                role = await mute_role(guild, state, mode)
                entry = active or {"mode": mode, "overwrites": {}}
                entry.update(role=role.id, expires=0)
                state["members"][member_id] = entry
                await save_state(guild, state)
                await enforce(guild, state, member, role, entry, permission_scopes(guild), audit)
                await member.add_roles(role, reason=audit)
                entry["expires"] = int(time.time()) + minutes * 60
                await save_state(guild, state)
            except ValueError as error:
                return await result(interaction, "Mute unavailable", str(error), error=True)
            except discord.HTTPException as error:
                LOGGER.warning("Timed mute request failed in guild %s: status %s", guild.id, error.status)
                return await result(interaction, "Mute action incomplete",
                                    "Check Jenny's role order and Manage Roles access in the affected channels. "
                                    "Jenny will retry cleanup for any partial mute.", error=True)
        label = discord.utils.escape_markdown(member.display_name)
        await result(interaction, f"{mode.title()} mute active", f"{label} · Ends <t:{entry['expires']}:R>.")
