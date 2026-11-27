"""Server audit messages, including provider failures from the voice API thread."""

import asyncio
from collections import OrderedDict
import logging
import time

import discord

from database import get_logs_channel_id


LOGGER = logging.getLogger(__name__)
_bot = None
_loop = None
_last = OrderedDict()


def connect_audit(bot):
    global _bot, _loop
    _bot, _loop = bot, asyncio.get_running_loop()


async def send_audit(guild, title, detail, *, actor=None, source=None, failure=False, cooldown=0,
                     snapshot=None, attachment=None):
    if guild is None:
        return
    configured = await asyncio.to_thread(get_logs_channel_id, str(guild.id))
    if (not configured or not str(configured).isdigit()
            or (snapshot is None and str(configured) == str(getattr(source, "id", None)))):
        return
    if cooldown:
        key = (guild.id, title, detail.split("\n", 1)[0])
        now = time.monotonic()
        if now - _last.get(key, -cooldown) < cooldown:
            return
        _last[key] = now
        _last.move_to_end(key)
        if len(_last) > 512:
            _last.popitem(last=False)
    try:
        async with asyncio.timeout(5):
            channel = guild.get_channel_or_thread(int(configured)) or await guild.fetch_channel(int(configured))
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                return
            embed = discord.Embed(title=title[:256], description=detail[:3500],
                                  color=discord.Color.red() if failure else discord.Color.blurple(),
                                  timestamp=discord.utils.utcnow())
            if actor:
                embed.add_field(name="Requested by", value=f"{actor.mention} ({actor.id})", inline=False)
            if source and getattr(source, "mention", None):
                embed.add_field(name="Channel", value=source.mention, inline=False)
            embeds = [embed, snapshot] if snapshot is not None else [embed]
            # Ticket and role panel snapshots stay read-only in the logs channel.
            files = [discord.File(attachment)] if attachment else []
            await channel.send(embeds=embeds, files=files, allowed_mentions=discord.AllowedMentions.none())
    except (discord.DiscordException, OSError, TimeoutError) as error:
        LOGGER.warning("Could not write Discord audit for guild %s: %s", guild.id, type(error).__name__)


def provider_audit(guild_id, label, model, failure):
    if not guild_id or not _bot or not _loop or _loop.is_closed():
        return

    async def report():
        guild = _bot.get_guild(int(guild_id))
        await send_audit(guild, "Model provider issue", f"{label} · {model}\n{failure}",
                         failure=True, cooldown=120)

    _loop.call_soon_threadsafe(lambda: asyncio.create_task(report()))
