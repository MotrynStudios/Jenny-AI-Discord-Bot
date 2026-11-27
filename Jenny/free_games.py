"""Scheduled alerts and on-demand listings for active free-game promotions."""

import asyncio
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import logging
import re
from time import monotonic
from urllib.parse import urlsplit

import discord
import requests
from discord import app_commands
from discord.ext import tasks

from community_commands import result
from command_runtime import MultiChoiceView
from database import (
    due_free_game_alerts, get_free_game_alerts, get_setting, mark_free_game_ended,
    save_free_game_alert, set_setting, tracked_giveaway_stock, update_free_game_expiry, update_giveaway_stock,
)
from giveaway_ratings import fetch_rating


API = "https://www.gamerpower.com/api/giveaways"
GAMERPOWER_SITE = "https://www.gamerpower.com/"
EPIC_STORE = "https://store.epicgames.com/en-US"
EPIC_API = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
PLATFORMS = ("Any", "PC", "Steam", "Epic Games Store", "GOG", "itch.io", "PlayStation", "Xbox", "Switch", "Android", "iOS")
ADD_ON_ROLE = "Add-Ons"
ADD_ON_TYPES = {"loot", "dlc"}
STORES = {"epicgames.com": "Epic Games Store", "steampowered.com": "Steam", "gog.com": "GOG",
          "indiegala.com": "IndieGala", "fanatical.com": "Fanatical", "itch.io": "itch.io",
          "ubisoft.com": "Ubisoft", "humblebundle.com": "Humble Bundle", "playstation.com": "PlayStation",
          "xbox.com": "Xbox", "nintendo.com": "Nintendo", "apple.com": "App Store", "play.google.com": "Google Play"}
STORE_EMOJIS = {"steam": "steam", "epic games store": "epicgames", "epic games": "epicgames", "gog": "gog",
                "itch.io": "itchio", "playstation": "playstation", "xbox": "xbox", "nintendo": "switch",
                "switch": "switch", "nintendo switch": "switch", "google play": "android", "android": "android",
                "app store": "ios", "ios": "ios"}
STORE_ICONS = {
    "GOG": "https://store-static-modular.gog-statics.com/en/assets/favicons/apple-touch-icon-120x120.png",
    "Fanatical": "https://cdn.fanatical.com/production/icons/fanatical-icon-android-chrome-192x192.png",
    "Xbox": "https://cms-assets.xboxservices.com/assets/b1/ed/b1ed23d2-019e-4e67-a870-0f40825289f9.png",
    "Nintendo": "https://assets.nintendo.com/image/upload/ncom/icons/fav-icons/apple-touch-icon.png",
    "App Store": "https://apps.apple.com/assets/favicon/favicon-180.png",
    "GamerPower": "https://www.gamerpower.com/favicon/apple-touch-icon.png",
}
logger = logging.getLogger(__name__)
STOCK_FIELDS = ("remaining_keys", "keys_remaining", "remaining_quantity", "stock_remaining", "remaining")
UNRATED = "No verified rating for this offer"


def stock_number(value):
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"(?:[0-9]{1,10}|[0-9]{1,3}(?:,[0-9]{3}){1,3})", text):
        return None
    return int(text.replace(",", ""))


def listed_stock(deal):
    for name in STOCK_FIELDS:
        if (value := stock_number(deal.get(name))) is not None:
            return value
    return None


class _StockLabel(HTMLParser):
    """Read explicit GamerPower claim-button labels, never estimated claim totals."""
    def __init__(self):
        super().__init__()
        self.button_text = None
        self.remaining = None

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.button_text = []

    def handle_data(self, data):
        if self.button_text is not None:
            self.button_text.append(data)

    def handle_endtag(self, tag):
        if tag != "button" or self.button_text is None:
            return
        label = " ".join(" ".join(self.button_text).split())
        self.button_text = None
        if re.fullmatch(r"(?:no (?:keys|codes|copies) left|out of stock|sold out)", label, re.I):
            self.remaining = 0
        elif match := re.fullmatch(r"([\d,]+)\s+(?:keys?|codes?|copies)\s+(?:left|remaining)", label, re.I):
            self.remaining = stock_number(match[1])


def source_stock_url(deal):
    url = safe_url(deal.get("gamerpower_url"))
    parsed = urlsplit(url) if url else None
    if (parsed and parsed.netloc in {"www.gamerpower.com", "www.gamerpower.net"}
            and re.fullmatch(r"/[a-zA-Z0-9-]+/?", parsed.path) and not parsed.query and not parsed.fragment):
        return url
    return None


def fetch_listing_stock(url):
    if source_stock_url({"gamerpower_url": url}) is None:
        return None
    parser = _StockLabel()
    deadline = monotonic() + 6
    try:
        with requests.get(url, timeout=(2, 3), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                return None
            read = 0
            for chunk in response.iter_content(32768):
                read += len(chunk)
                if read > 512 * 1024 or monotonic() > deadline:
                    break
                parser.feed(chunk.decode("utf-8", errors="replace"))
                if parser.remaining is not None:
                    return parser.remaining
    except requests.RequestException:
        pass
    return None


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
    selected = config.get("platforms", [config.get("platform", "Any")])
    if selected and "Any" not in selected and not any(platform.casefold() in platforms for platform in selected):
        return False
    kind = str(deal.get("type", "")).casefold()
    return kind == "game" or (kind in ADD_ON_TYPES and config.get("dlcs_and_addons", config.get("include_addons", False)))


def claim_link(deal):
    return safe_url(deal.get("direct_link")) or safe_url(deal.get("open_giveaway")) or safe_url(deal.get("open_giveaway_url"))


def direct_claim_link(url):
    """Inspect one redirect on GamerPower's own host without fetching its destination."""
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.netloc.casefold() != "www.gamerpower.com" or not parsed.path.startswith("/open/"):
        return url
    try:
        with requests.get(url, allow_redirects=False, stream=True, timeout=(2, 3)) as hop:
            return safe_url(hop.headers.get("Location")) if hop.is_redirect else None
    except requests.RequestException:
        return None


def giveaway_provider(deal, link):
    if deal.get("giveaway_provider"):
        return str(deal["giveaway_provider"])
    hostname = urlsplit(link).hostname if link else None
    if hostname and hostname not in {"gamerpower.com", "www.gamerpower.com"}:
        return next((name for domain, name in STORES.items() if hostname == domain or hostname.endswith("." + domain)),
                    hostname.removeprefix("www."))
    # The platform can be Steam for a key offered by a different retailer.
    return "See the original listing"


def giveaway_icon(provider, guild, platforms, *, epic_only=False):
    """Prefer the distributor's badge, then a listed platform's badge."""
    emojis = {emoji.name.casefold(): emoji for emoji in guild.emojis if emoji.is_usable()} if guild else {}
    candidates = [provider, *(value.strip() for value in platforms.split(","))]
    for name in candidates:
        if (emoji := emojis.get(STORE_EMOJIS.get(name.casefold()))) is not None:
            return str(emoji.url)
        if icon := STORE_ICONS.get(name):
            return icon
    return None if epic_only else STORE_ICONS["GamerPower"]


def feed_timestamp(value):
    """Parse an explicit date; the feed's date strings without an offset are read as UTC."""
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.strip())
        return int((date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def offer_key(deal):
    started = feed_timestamp(deal.get("published_date"))
    return f"gamerpower:{deal['id']}:{started if started is not None else ''}"


def ended_deal_embed(embed, expires_at):
    ended = embed.copy()
    ended.title = f"Offer ended · {embed.title or 'Free promotion'}"[:256]
    ended.color = discord.Color.greyple()
    if ended.description:
        ended.description = re.sub(r"(?m)^(?:~~[$€£][0-9.,]+~~ )?\*\*Free(?: offer)?\*\*", "**Offer ended**", ended.description)
    ended.set_footer(text="This promotion has expired. Check the store for its current price.")
    ended.timestamp = datetime.fromtimestamp(expires_at, timezone.utc)
    return ended


def giveaway_card(embed):
    """Use a Text Display so Discord can deliver an in-card role mention."""
    title = discord.utils.escape_markdown(discord.utils.escape_mentions(embed.title or "Free promotion"))
    details = "\n".join(f"**{field.name}** · {field.value}" for field in embed.fields)
    heading = discord.ui.TextDisplay(f"## {title}")
    header = (discord.ui.Section(heading, accessory=discord.ui.Thumbnail(embed.thumbnail.url))
              if embed.thumbnail.url else heading)
    children = [header, discord.ui.TextDisplay(embed.description or ""),
                discord.ui.TextDisplay(details)]
    if embed.image.url:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(embed.image.url)))
    if embed.footer.text:
        children.append(discord.ui.TextDisplay(f"-# {embed.footer.text}"))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=embed.colour))
    return view


def update_giveaway_card(message, expires_at=None, *, remaining=None):
    view = discord.ui.LayoutView.from_message(message, timeout=None)
    card = view.children[0] if view.children else None
    if not isinstance(card, discord.ui.Container) or len(card.children) < 3:
        return None
    heading, body = card.children[:2]
    if isinstance(heading, discord.ui.Section):
        heading = next((child for child in heading.children if isinstance(child, discord.ui.TextDisplay)), None)
    if not (isinstance(heading, discord.ui.TextDisplay) and isinstance(body, discord.ui.TextDisplay)):
        return None
    if remaining is not None:
        details = card.children[2]
        if not isinstance(details, discord.ui.TextDisplay):
            return None
        lines = [line for line in details.content.splitlines() if not line.startswith("**Remaining** · ")]
        lines.insert(max(0, len(lines) - 1), f"**Remaining** · {remaining:,}")
        details.content = "\n".join(lines)
        if remaining > 0:
            return view
    title = "Out of stock" if remaining == 0 else "Offer ended"
    card.accent_colour = discord.Color.greyple()
    if not heading.content.startswith(f"## {title}"):
        heading.content = f"## {title} · " + heading.content.removeprefix("## ")
    status = f"**{title}**" + (f" · <t:{expires_at}:F>" if expires_at is not None else "")
    body.content = re.sub(r"(?m)^(?:~~[$€£][0-9.,]+~~ )?\*\*Free(?: offer)?\*\*", status, body.content)
    return view


def fetch_epic_deals():
    """Read the US storefront's active 100%-off, limited-time promotions."""
    response = requests.get(EPIC_API, params={"locale": "en-US", "country": "US", "allowCountries": "US"},
                            timeout=(8, 8))
    response.raise_for_status()
    elements = response.json()["data"]["Catalog"]["searchStore"]["elements"]
    if not isinstance(elements, list):
        raise ValueError("Expected the Epic promotion list")
    now = datetime.now(timezone.utc)
    deals = []
    for offer in elements:
        kind = {"BASE_GAME": "Game", "ADD_ON": "DLC"}.get(offer.get("offerType"))
        price = offer.get("price", {}).get("totalPrice", {})
        if not kind or not price.get("originalPrice") or price.get("discountPrice") != 0:
            continue
        end = None
        for group in (offer.get("promotions") or {}).get("promotionalOffers", []):
            for promotion in group.get("promotionalOffers", []):
                if promotion.get("discountSetting", {}).get("discountPercentage") != 0:
                    continue
                try:
                    starts = datetime.fromisoformat(promotion["startDate"])
                    expires = datetime.fromisoformat(promotion["endDate"])
                    if starts <= now < expires and (end is None or expires < end):
                        end = expires
                except (KeyError, TypeError, ValueError):
                    continue
        if not end:
            continue
        mappings = offer.get("offerMappings") or (offer.get("catalogNs") or {}).get("mappings") or []
        slug = (mappings[0].get("pageSlug") if mappings else None) or offer.get("productSlug") or ""
        slug = slug.removesuffix("/home")
        link = f"{EPIC_STORE}/p/{slug}" if re.fullmatch(r"[a-zA-Z0-9_-]+", slug) else f"{EPIC_STORE}/free-games"
        images = {item.get("type"): item.get("url") for item in offer.get("keyImages", [])}
        deals.append({"title": offer.get("title"), "description": offer.get("description"),
                      "type": kind, "platforms": "PC, Epic Games Store", "open_giveaway": link,
                      "end_date": end.isoformat(), "giveaway_provider": "Epic Games Store",
                      "image": images.get("OfferImageWide") or images.get("DieselStoreFrontWide") or images.get("Thumbnail")})
    return deals


def free_game_pages(deals, platform, *, epic_only=False, guild=None):
    footer = f"{platform} · {len(deals)} offers"
    if epic_only:
        footer += " · US Epic Games Store fallback"
    return [deal_embed(deal, epic_only=epic_only, footer=footer, guild=guild) for deal in deals]


def deal_embed(deal, *, epic_only=False, footer=None, notification_roles=(), guild=None):
    url = claim_link(deal)
    source = f"{EPIC_STORE}/free-games" if epic_only else safe_url(deal.get("gamerpower_url")) or GAMERPOWER_SITE
    source_name = "Epic Games Store" if epic_only else "GamerPower"
    claim = f"[Open offer ↗](<{url}>) · " if url and url != source else ""
    worth = str(deal.get("worth") or "")
    price = f"~~{worth}~~ **Free**" if re.fullmatch(r"[$€£][0-9.,]+", worth) else "**Free offer**"
    info = discord.utils.escape_mentions(str(deal.get("description") or "See the listing for details."))[:1300]
    embed = discord.Embed(
        title=str(deal.get("title") or "Free promotion")[:240], url=url or source,
        description=f"{info}\n\n{price}\n{claim}[View on {source_name}](<{source}>)",
        color=discord.Color.green(),
    )
    raw_platforms = str(deal.get("platforms") or "Check the listing")
    platforms = " · ".join(value.strip() for value in raw_platforms.split(",") if value.strip())
    kind = {"game": "Game", "dlc": "DLC", "loot": "Add-on or loot"}.get(str(deal.get("type")).casefold(), "Promotion")
    end = feed_timestamp(deal.get("end_date"))
    if notification_roles:
        embed.add_field(name="Alert roles", value=" · ".join(role.mention for role in notification_roles), inline=False)
    provider = giveaway_provider(deal, url)
    embed.add_field(name="Giveaway by", value=provider[:220], inline=False)
    embed.add_field(name="Rating", value=UNRATED, inline=False)
    embed.add_field(name="Type", value=kind, inline=False)
    embed.add_field(name="Platforms", value=platforms[:400], inline=False)
    if (remaining := listed_stock(deal)) is not None:
        embed.add_field(name="Remaining", value=f"{remaining:,}", inline=False)
    embed.add_field(name="Ends", value=f"<t:{end}:F> · <t:{end}:R>" if end is not None else "No end date listed", inline=False)
    image_url = safe_url(deal.get("image")) or safe_url(deal.get("thumbnail"))
    if image_url:
        embed.set_image(url=image_url)
    if icon := giveaway_icon(provider, guild, raw_platforms, epic_only=epic_only):
        embed.set_thumbnail(url=icon)
    embed.set_footer(text=footer or "Availability and eligibility vary by store and region.")
    return embed


async def enrich_giveaway_embed(embed, guild):
    """Resolve and enrich only the giveaway card someone will actually see."""
    fields = {field.name: (index, field.value) for index, field in enumerate(embed.fields)}
    if not {"Platforms", "Type", "Giveaway by"} <= fields.keys():
        return False
    rating_index, current_rating = fields.get("Rating", (len(fields), UNRATED))
    if current_rating != UNRATED:
        return False
    changed = "Rating" not in fields
    if changed:
        embed.add_field(name="Rating", value=UNRATED, inline=False)
    platforms = fields["Platforms"][1].replace(" · ", ",")
    kind = {"Game": "game", "DLC": "dlc"}.get(fields["Type"][1], "loot")
    original = safe_url(embed.url)
    url = original
    try:
        async with asyncio.timeout(8):
            if direct := await asyncio.to_thread(direct_claim_link, original):
                url = direct
            if url != original and embed.description:
                embed.url = url
                embed.description = embed.description.replace(f"[Open offer ↗](<{original}>)", f"[Open offer ↗](<{url}>)")
                provider = giveaway_provider({}, url)
                embed.set_field_at(fields["Giveaway by"][0], name="Giveaway by", value=provider[:220], inline=False)
                if icon := giveaway_icon(provider, guild, platforms):
                    embed.set_thumbnail(url=icon)
                changed = True
            if rating := await asyncio.to_thread(fetch_rating, url, embed.title or "", kind, platforms):
                embed.set_field_at(rating_index, name="Rating", value=rating, inline=False)
                changed = True
    except TimeoutError:
        pass  # A slow store must never prevent the giveaway card from being sent.
    return changed


def alert_ping_roles(guild, deal, config, role_ids):
    if str(deal.get("type", "")).casefold() in ADD_ON_TYPES:
        role_id = role_ids.get(ADD_ON_ROLE)
        role = guild.get_role(role_id) if role_id else None
        return [role] if role and not role.is_default() else []
    platforms = str(deal.get("platforms", "")).casefold()
    selected = config.get("platforms", [config.get("platform", "Any")])
    matches = [role_id for platform, role_id in role_ids.items() if platform in PLATFORMS[1:]
               and (not selected or "Any" in selected or platform in selected) and platform.casefold() in platforms]
    roles = [role for role_id in dict.fromkeys(matches) if (role := guild.get_role(role_id)) and not role.is_default()]
    if not roles and role_ids.get("Any"):
        fallback = guild.get_role(role_ids["Any"])
        if fallback and not fallback.is_default():
            roles.append(fallback)
    return roles


def register_free_games(bot, send_pages):
    feed_lock = asyncio.Lock()
    feeds = {}

    async def get_feed(fetch):
        async with feed_lock:
            expires, deals = feeds.get(fetch, (0, None))
            if monotonic() < expires:
                return deals
            try:
                deals = await asyncio.to_thread(fetch)
            except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
                logger.warning("Free-game source %s unavailable: %s", fetch.__name__, type(error).__name__)
                deals = None
            feeds[fetch] = (monotonic() + (60 if deals is not None else 25), deals)
            return deals

    async def settings(guild):
        raw = await asyncio.to_thread(get_setting, "free_games", None, str(guild.id))
        return json.loads(raw) if raw else None

    async def ping_roles(guild_id):
        raw = await asyncio.to_thread(get_setting, "free_game_ping_roles", "{}", str(guild_id))
        return json.loads(raw)

    async def stock_for(deal, url, checks):
        remaining = listed_stock(deal)
        if remaining is None and url:
            if url not in checks:
                checks[url] = await asyncio.to_thread(fetch_listing_stock, url)
            remaining = checks[url]
        if remaining is not None:
            deal["remaining"] = remaining
        return remaining

    async def refresh_stock(watches, current, checks):
        for alert in watches:
            guild = bot.get_guild(int(alert["guild_id"]))
            if not guild or guild.unavailable:
                continue
            remaining = await stock_for(current.get(alert["offer_key"], {}), alert["stock_url"], checks)
            if remaining is None or remaining == alert["remaining"]:
                continue
            channel_id = int(alert["channel_id"])
            try:
                async with asyncio.timeout(8):
                    channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
                    message = await channel.fetch_message(int(alert["message_id"]))
                    if message.author.id != bot.user.id or not message.flags.components_v2:
                        continue
                    view = update_giveaway_card(message, remaining=remaining)
                    if view is None:
                        continue
                    await message.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
            except discord.NotFound:
                await asyncio.to_thread(mark_free_game_ended, alert["channel_id"], alert["offer_key"])
                continue
            except (discord.DiscordException, TimeoutError) as error:
                logger.warning("Could not update remaining stock for giveaway %s: %s", alert["message_id"], type(error).__name__)
                continue
            await asyncio.to_thread(update_giveaway_stock, alert["channel_id"], alert["offer_key"], remaining)

    @tasks.loop(minutes=1)
    async def expire_alerts():
        now = int(datetime.now(timezone.utc).timestamp())
        for alert in await asyncio.to_thread(due_free_game_alerts, now):
            guild = bot.get_guild(int(alert["guild_id"]))
            if guild is None or guild.unavailable:
                continue
            try:
                async with asyncio.timeout(8):
                    channel_id = int(alert["channel_id"])
                    channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
                    message = await channel.fetch_message(int(alert["message_id"]))
                    if message.author.id == bot.user.id:
                        if message.flags.components_v2:
                            view = update_giveaway_card(message, alert["expires_at"])
                            if view is not None:
                                await message.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
                        elif message.embeds:
                            await message.edit(embed=ended_deal_embed(message.embeds[0], alert["expires_at"]),
                                               allowed_mentions=discord.AllowedMentions.none())
            except discord.NotFound:
                pass  # The message or its channel was deleted.
            except (discord.DiscordException, TimeoutError) as error:
                logger.warning("Could not expire giveaway message %s: %s", alert["message_id"], type(error).__name__)
                continue
            await asyncio.to_thread(mark_free_game_ended, alert["channel_id"], alert["offer_key"])

    @tasks.loop(minutes=30)
    async def check_deals():
        watches = await asyncio.to_thread(tracked_giveaway_stock)
        try:
            subscriptions = [(guild, config) for guild in bot.guilds if (config := await settings(guild))]
            if not subscriptions and not watches:
                return
        except ValueError:
            logger.warning("Could not read free-game alert settings. The next scheduled poll will retry.")
            return
        deals = await get_feed(fetch_deals)
        checks = {}
        await refresh_stock(watches, {offer_key(deal): deal for deal in deals or []}, checks)
        if deals is None:
            return
        new_stock_checks = 0
        for guild, config in subscriptions:
            try:
                destinations = {}
                for channel_id in {config["channel"], config.get("addons_channel") or config["channel"]}:
                    channel = guild.get_channel(channel_id)
                    if not isinstance(channel, discord.TextChannel):
                        logger.warning("Free-game destination %s is unavailable in guild %s", channel_id, guild.id)
                        continue
                    saved = await asyncio.to_thread(get_setting, f"free_games_seen:{channel_id}", "[]", str(guild.id))
                    recorded = await asyncio.to_thread(get_free_game_alerts, channel_id)
                    destinations[channel_id] = (channel, set(json.loads(saved)), recorded)
                role_ids = await ping_roles(guild.id)
                now = int(datetime.now(timezone.utc).timestamp())
                sent = 0
                for deal in deals:
                    addon = str(deal.get("type", "")).casefold() in ADD_ON_TYPES
                    channel_id = (config.get("addons_channel") or config["channel"]) if addon else config["channel"]
                    if channel_id not in destinations:
                        continue
                    channel, legacy_seen, recorded = destinations[channel_id]
                    key = offer_key(deal)
                    expires = feed_timestamp(deal.get("end_date"))
                    if key in recorded:
                        old_end, ended = recorded[key]
                        if not ended and expires is not None and expires != old_end:
                            await asyncio.to_thread(update_free_game_expiry, channel.id, key, expires)
                        continue
                    if (sent >= 5 or deal["id"] in legacy_seen or (expires is not None and expires <= now)
                            or not deal_matches(deal, config)):
                        continue
                    if await settings(guild) != config:
                        break
                    remaining = listed_stock(deal)
                    stock_url = source_stock_url(deal) if expires is None else None
                    if stock_url and (stock_url in checks or (remaining is None and new_stock_checks < 8)):
                        if stock_url not in checks:
                            new_stock_checks += 1
                        remaining = await stock_for(deal, stock_url, checks)
                    if remaining == 0:
                        continue
                    roles = alert_ping_roles(guild, deal, config, role_ids)
                    can_mention = channel.permissions_for(guild.me).mention_everyone
                    roles = [role for role in roles if role.mentionable or can_mention]
                    embed = deal_embed(deal, notification_roles=roles, guild=guild)
                    await enrich_giveaway_embed(embed, guild)
                    message = await channel.send(view=giveaway_card(embed), allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=roles, replied_user=False,
                    ))
                    await asyncio.to_thread(save_free_game_alert, guild.id, channel.id, key, message.id, expires,
                                            stock_url if remaining is not None else None, remaining)
                    recorded[key] = (expires, False)
                    sent += 1
            except (discord.HTTPException, ValueError, OSError):
                logger.exception("Free games delivery failed in guild %s", guild.id)

    @bot.listen("on_ready")
    async def start_deal_checks():
        if not check_deals.is_running():
            check_deals.start()
        if not expire_alerts.is_running():
            expire_alerts.start()

    choices = [app_commands.Choice(name=platform, value=platform) for platform in PLATFORMS]
    alert_choices = [*choices, app_commands.Choice(name=ADD_ON_ROLE, value=ADD_ON_ROLE)]
    platforms_menu = [(platform, platform) for platform in PLATFORMS[1:]]
    placeholder = "Choose platforms (empty means any)"

    async def browse(interaction, platforms, dlcs_and_addons, *, addons_only=False, private=False):
        await interaction.response.defer(thinking=True, ephemeral=private)
        label = ", ".join(platforms) or "Any platform"
        deals = await get_feed(fetch_deals)
        epic_only = deals is None and (not platforms or bool(set(platforms) & {"Any", "PC", "Epic Games Store"}))
        if epic_only:
            deals = await get_feed(fetch_epic_deals)
        if deals is None:
            links = f"[Check GamerPower]({GAMERPOWER_SITE})"
            if epic_only:
                links += f" or [browse the Epic Games Store]({EPIC_STORE}/free-games)"
            return await result(interaction, "Free-game feed unavailable",
                                f"I couldn't load current offers for {label}. {links} for the latest listings.",
                                error=True, private=private)
        filters = {"platforms": platforms, "dlcs_and_addons": dlcs_and_addons}
        now = int(datetime.now(timezone.utc).timestamp())
        matching = [deal for deal in deals if deal_matches(deal, filters)
                    and listed_stock(deal) != 0
                    and (not addons_only or str(deal.get("type", "")).casefold() in ADD_ON_TYPES)
                    and ((end := feed_timestamp(deal.get("end_date"))) is None or end > now)]
        if not matching:
            detail = f"No active {'add-ons' if addons_only else 'offers'} matched {label}."
            if epic_only:
                detail += f" Only the [Epic Games Store US feed]({EPIC_STORE}/free-games) was available."
            else:
                detail += f" [GamerPower]({GAMERPOWER_SITE})"
            return await result(interaction, "No matching giveaways", detail, private=private)
        await send_pages(interaction, free_game_pages(matching, label, epic_only=epic_only, guild=interaction.guild),
                         "free_games", ephemeral=private)

    @bot.tree.command(description="Browse currently free game giveaways by platform")
    @app_commands.describe(platform="Pick one platform, or omit for a multi-select picker",
                           dlcs_and_addons="Include DLCs, add-ons and loot as well as games")
    @app_commands.choices(platform=choices)
    async def freegamesnow(interaction: discord.Interaction, platform: str | None = None, dlcs_and_addons: bool = False):
        if platform is not None:
            return await browse(interaction, [platform], dlcs_and_addons)

        async def chosen(component, platforms):
            await browse(component, platforms, dlcs_and_addons)

        await interaction.response.send_message(embed=discord.Embed(
            title="Filter free games", description="Choose any number of platforms, then confirm. Leave empty for all."),
            view=MultiChoiceView(interaction.user.id, platforms_menu, placeholder, chosen), ephemeral=True)

    @bot.tree.command(description="Browse current add-ons for your platform roles without sending alerts")
    @app_commands.guild_only()
    @app_commands.describe(platform="Leave blank to use your platform roles, or pick a platform for this search")
    @app_commands.choices(platform=choices)
    async def myaddons(interaction: discord.Interaction, platform: str | None = None):
        if platform is not None:
            return await browse(interaction, [platform], True, addons_only=True, private=True)

        role_ids = await ping_roles(interaction.guild_id)
        member_roles = {role.id for role in interaction.user.roles}
        platforms = [name for name in PLATFORMS[1:] if role_ids.get(name) in member_roles]
        if platforms:
            return await browse(interaction, platforms, True, addons_only=True, private=True)

        async def chosen(component, platforms):
            await browse(component, platforms, True, addons_only=True, private=True)

        await interaction.response.send_message(embed=discord.Embed(
            title="Browse add-ons", description="Pick the platforms you want to browse. This won't change your roles or alert settings."),
            view=MultiChoiceView(interaction.user.id, platforms_menu, placeholder, chosen), ephemeral=True)

    @bot.tree.command(description="Enable, change or disable free-game alerts for this server")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(platform=choices)
    @app_commands.describe(platform="Pick one platform, or omit for a multi-select picker",
                           dlcs_and_addons="Include DLCs, add-ons and loot as well as games",
                           addons_channel="Optional separate channel for DLCs, add-ons and loot")
    async def freegames(interaction: discord.Interaction, channel: discord.TextChannel = None,
                        platform: str | None = None, dlcs_and_addons: bool = False, enabled: bool = True,
                        addons_channel: discord.TextChannel | None = None):
        if enabled:
            if not isinstance(channel, discord.TextChannel):
                return await result(interaction, "Choose a channel", "Select a text channel, or set enabled to False.", error=True)
            for destination in (channel, addons_channel):
                if destination is None:
                    continue
                permissions = destination.permissions_for(interaction.guild.me)
                if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                    return await result(interaction, "Channel inaccessible", f"Jenny needs View Channel, Send Messages, "
                                        f"and Embed Links in {destination.mention}.", error=True)
        async def save(component, platforms):
            if not component.permissions.administrator:
                return await result(component, "Permission denied", "Only administrators can configure alerts.",
                                    error=True, private=True)
            await component.response.defer(thinking=True, ephemeral=True)
            config = ({"channel": channel.id, "platforms": platforms, "dlcs_and_addons": dlcs_and_addons or bool(addons_channel),
                       "addons_channel": addons_channel.id if addons_channel else None} if enabled else None)
            await asyncio.to_thread(set_setting, "free_games", json.dumps(config), str(interaction.guild_id))
            addon_target = f"\nAdd-ons: {addons_channel.mention}." if addons_channel else ""
            await result(component, "Free-game alerts enabled" if enabled else "Free-game alerts disabled",
                         f"{channel.mention} · {', '.join(platforms) or 'Any platform'}. Checked every 30 minutes.{addon_target}"
                         if enabled else "No more alerts will be sent.", private=True)

        if not enabled or platform is not None:
            return await save(interaction, [platform] if platform else [])
        await interaction.response.send_message(embed=discord.Embed(title="Filter free-game alerts",
            description="Choose any number of platforms, then confirm. Leave empty for all."),
            view=MultiChoiceView(interaction.user.id, platforms_menu, placeholder, save), ephemeral=True)

    @bot.tree.command(description="Set or clear the role mentioned for free-game alerts")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(platform=alert_choices)
    @app_commands.describe(platform="Use Any for general game alerts, pick a platform, or choose Add-Ons",
                           role="Role to mention. Leave blank to clear this rule.")
    async def freegamesrole(interaction: discord.Interaction, platform: str = "Any", role: discord.Role | None = None):
        if role is not None and role.is_default():
            return await result(interaction, "Choose a server role", "The everyone role cannot be used.", error=True, private=True)
        await interaction.response.defer(thinking=True, ephemeral=True)
        role_ids = await ping_roles(interaction.guild_id)
        if role is None:
            role_ids.pop(platform, None)
        else:
            role_ids[platform] = role.id
        await asyncio.to_thread(set_setting, "free_game_ping_roles", json.dumps(role_ids), str(interaction.guild_id))
        detail = f"{platform}: {role.mention}." if role else f"Cleared the {platform} role rule."
        if role and not role.mentionable:
            detail += " Jenny needs Mention Everyone in the alert channels to ping this role."
        await result(interaction, "Free-game role updated", detail, private=True)
