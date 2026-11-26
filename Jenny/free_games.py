"""Opt-in alerts for active free-game promotions."""

import asyncio
import json
import logging
import re
from urllib.parse import urlsplit

import discord
import requests
from discord import app_commands
from discord.ext import tasks

from community_commands import result
from database import get_setting, set_setting


API = "https://www.gamerpower.com/api/giveaways"
PLATFORMS = ("Any", "PC", "Steam", "Epic Games Store", "GOG", "itch.io", "PlayStation", "Xbox", "Switch", "Android", "iOS")
logger = logging.getLogger(__name__)


def safe_url(value):
    if not isinstance(value, str) or any(character in value for character in "<>\n\r"):
        return None
    try:
        url = urlsplit(value)
        return value if url.scheme == "https" and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


def fetch_deals():
    response = requests.get(API, timeout=(4, 10))
    if response.status_code == 201:
        return []
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Expected the GamerPower giveaway list")
    return [entry for entry in data if isinstance(entry, dict) and isinstance(entry.get("id"), int)
            and str(entry.get("status", "")).casefold() == "active"]


def deal_matches(deal, config):
    platforms = str(deal.get("platforms", "")).casefold()
    platform = config["platform"]
    if platform != "Any" and platform.casefold() not in platforms:
        return False
    kind = str(deal.get("type", "")).casefold()
    return kind == "game" or (kind in {"loot", "dlc"} and config["include_addons"])


def steam_reviews(url):
    """Look up customer reviews only when the giveaway points at an exact Steam app."""
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.hostname == "www.gamerpower.com" and parsed.path.startswith("/open/"):
            # Inspect only the first public redirect. Never fetch a feed-controlled URL.
            with requests.get(url, allow_redirects=False, stream=True, timeout=(3, 4)) as hop:
                url = safe_url(hop.headers.get("Location")) if hop.is_redirect else None
            if not url:
                return None
            parsed = urlsplit(url)
        match = re.match(r"^/app/(\d+)(?:/|$)", parsed.path)
        if parsed.hostname != "store.steampowered.com" or not match:
            return None
        response = requests.get(
            "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/",
            params={"input_json": json.dumps({"appid": int(match[1]), "languages": ["all"], "day_range": 0, "num_per_page": 1})},
            timeout=(3, 4),
        )
        response.raise_for_status()
        data = response.json()
        summary = data.get("response", data).get("query_summary", {})
        total, positive = summary.get("total_reviews", 0), summary.get("total_positive", 0)
        if total > 0 and 0 <= positive <= total:
            return f"{positive / total:.0%} positive from {total:,} reviews"
    except (requests.RequestException, ValueError, AttributeError, TypeError):
        return None
    return None


async def deal_embed(deal, ratings):
    url = safe_url(deal.get("open_giveaway")) or safe_url(deal.get("open_giveaway_url"))
    source = safe_url(deal.get("gamerpower_url")) or "https://www.gamerpower.com/"
    embed = discord.Embed(
        title=str(deal.get("title") or "Free promotion")[:240], url=url or source,
        description=str(deal.get("description") or "See the listing for details.")[:1500],
        color=discord.Color.green(),
    )
    embed.add_field(name="Type", value="Game" if str(deal.get("type")).lower() == "game" else "DLC, add-on or loot")
    embed.add_field(name="Platforms", value=str(deal.get("platforms") or "Check the listing")[:256])
    expiry = str(deal.get("end_date") or "").strip()
    if expiry and expiry.casefold() not in {"n/a", "unknown"}:
        embed.add_field(name="Listed end date", value=expiry[:100])
    if "steam" in str(deal.get("platforms", "")).casefold() and url not in ratings:
        ratings[url] = await asyncio.to_thread(steam_reviews, url)
    if ratings.get(url):
        embed.add_field(name="Steam user reviews", value=ratings[url], inline=False)
    embed.add_field(name="Listing", value=f"[GamerPower]({source})", inline=False)
    image_url = safe_url(deal.get("thumbnail"))
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Check the store for region, eligibility and expiry.")
    return embed


def register_free_games(bot):
    async def settings(guild):
        raw = await asyncio.to_thread(get_setting, "free_games", None, str(guild.id))
        return json.loads(raw) if raw else None

    @tasks.loop(minutes=30)
    async def check_deals():
        try:
            subscriptions = [(guild, config) for guild in bot.guilds if (config := await settings(guild))]
            if not subscriptions:
                return
            deals = await asyncio.to_thread(fetch_deals)
        except (requests.RequestException, ValueError):
            logger.warning("Free games feed unavailable. The next scheduled poll will retry.")
            return
        ratings = {}
        for guild, config in subscriptions:
            channel = guild.get_channel(config["channel"])
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                key = f"free_games_seen:{channel.id}"
                saved = await asyncio.to_thread(get_setting, key, "[]", str(guild.id))
                seen = list(dict.fromkeys(json.loads(saved)))
                matching = [deal for deal in deals if deal["id"] not in seen and deal_matches(deal, config)]
                # Limit the initial backlog, but continue with unseen deals on later polls.
                for deal in matching[:5]:
                    if await settings(guild) != config:
                        break
                    embed = await deal_embed(deal, ratings)
                    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    seen.append(deal["id"])
                    await asyncio.to_thread(set_setting, key, json.dumps(seen[-1500:]), str(guild.id))
            except (discord.HTTPException, ValueError, OSError):
                logger.exception("Free games delivery failed in guild %s", guild.id)

    @bot.listen("on_ready")
    async def start_deal_checks():
        if not check_deals.is_running():
            check_deals.start()

    choices = [app_commands.Choice(name=platform, value=platform) for platform in PLATFORMS]

    @bot.tree.command(description="Enable, change or disable free-game alerts for this server")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(platform=choices)
    async def freegames(interaction: discord.Interaction, channel: discord.TextChannel = None,
                        platform: str = "Any", include_addons: bool = False, enabled: bool = True):
        if enabled:
            if not isinstance(channel, discord.TextChannel):
                return await result(interaction, "Choose a channel", "Select a text channel, or set enabled to False.", error=True)
            permissions = channel.permissions_for(interaction.guild.me)
            if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                return await result(interaction, "Channel inaccessible", "Jenny needs View Channel, Send Messages, and Embed Links.", error=True)
        await interaction.response.defer()
        config = {"channel": channel.id, "platform": platform, "include_addons": include_addons} if enabled else None
        await asyncio.to_thread(set_setting, "free_games", json.dumps(config), str(interaction.guild_id))
        await result(interaction, "Free-game alerts enabled" if enabled else "Free-game alerts disabled",
                     f"{channel.mention} · {platform}. Checked every 30 minutes." if enabled else "No more alerts will be sent.")
