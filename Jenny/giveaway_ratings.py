"""Read public storefront ratings for the exact product in a giveaway."""

from functools import lru_cache
from html import unescape
import json
import math
import re
from time import monotonic
from urllib.parse import urlsplit

import requests


STEAM = "store.steampowered.com"
PRODUCT_TYPES = {"Product", "VideoGame", "SoftwareApplication"}
JSON_LD = re.compile(r'<script\b[^>]*\btype=["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>', re.I | re.S)
STEAM_MATCH = re.compile(
    r'<a\b[^>]*\bhref="(https://store\.steampowered\.com/app/\d+/[^"]*)"[^>]*>\s*'
    r'<div class="match_name">([^<]+)</div>', re.I,
)
STEAM_POSITIVE = re.compile(r'data-tooltip-html="(\d+)% of the ([\d,]+) user reviews\b[^"]* are positive\b')


def _get_page(url, deadline, params=None, *, apple_redirect=True):
    # Only callers with validated storefront URLs may reach this function.
    if monotonic() >= deadline:
        return ""
    try:
        with requests.get(url, params=params, timeout=(2, 2), stream=True, allow_redirects=False) as response:
            if response.is_redirect and apple_redirect and url.startswith("https://apps.apple.com/"):
                # Apple redirects older slugs to the current name of the same app.
                target = _product_url(response.headers.get("Location"), "game")
                if target and target[0] == "App Store" and target[1].rstrip("/").split("/")[-1] == url.rstrip("/").split("/")[-1]:
                    return _get_page(target[1], deadline, apple_redirect=False)
            if response.status_code != 200:
                return ""
            body = bytearray()
            for chunk in response.iter_content(32768):
                if len(body) + len(chunk) > 2 * 1024 * 1024 or monotonic() > deadline:
                    return ""
                body.extend(chunk)
            return body.decode("utf-8", errors="replace")
    except requests.RequestException:
        return ""


def _product_url(url, kind):
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != parsed.hostname:
            return None
    except ValueError:
        return None
    path = parsed.path
    if parsed.hostname == STEAM and re.fullmatch(r"/app/\d+(?:/[\w%-]+)?/?", path):
        return "Steam", f"https://{STEAM}{path}"
    if parsed.hostname == "www.gog.com" and re.fullmatch(r"/(?:[a-z]{2}/)?game/[a-z0-9_-]+/?", path):
        return "GOG", "https://www.gog.com" + path
    if kind != "game":
        return None
    if parsed.hostname == "apps.apple.com" and re.fullmatch(r"/[a-z]{2}/app/(?:[\w%-]+/)?id\d+/?", path):
        return "App Store", "https://apps.apple.com" + path
    if parsed.hostname == "play.google.com" and path == "/store/apps/details":
        if match := re.search(r"(?:^|&)id=([A-Za-z0-9_.]+)(?:&|$)", parsed.query):
            return "Google Play", f"https://play.google.com{path}?id={match[1]}"
    return None


def _number(value):
    try:
        number = float(value) if not isinstance(value, bool) else float("nan")
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _product_rating(html, expected_title=None):
    ratings = set()
    for script in JSON_LD.findall(html):
        try:
            data = json.loads(script)
        except (ValueError, RecursionError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                # JSON-LD commonly wraps the product in a graph with separate breadcrumb data.
                ratings.update(filter(None, (_rating_entry(value, expected_title) for value in graph)))
            else:
                rating = _rating_entry(item, expected_title)
                if rating:
                    ratings.add(rating)
    return next(iter(ratings)) if len(ratings) == 1 else None


def _rating_entry(item, expected_title):
    if not isinstance(item, dict):
        return None
    types = item.get("@type")
    types = types if isinstance(types, list) else [types]
    rating = item.get("aggregateRating")
    if not any(isinstance(value, str) and value in PRODUCT_TYPES for value in types) or not isinstance(rating, dict):
        return None
    if expected_title:
        name = item.get("name")
        if not isinstance(name, str) or _title_key(name) != expected_title:
            return None
    score = _number(rating.get("ratingValue"))
    maximum = _number(rating.get("bestRating", 5))
    ratings = _number(rating.get("ratingCount"))
    reviews = _number(rating.get("reviewCount"))
    count = ratings if ratings is not None else reviews
    if score is None or maximum is None or not 0 <= score <= maximum or maximum <= 0:
        return None
    if count is not None and count <= 0:
        return None
    plural = "ratings" if ratings is not None else "reviews"
    displayed_score = f"{score:.2f}".rstrip("0").rstrip(".")
    return f"{displayed_score}/{maximum:g}" + (f" · {int(count):,} {plural}" if count else "")


def _steam_rating(html, expected_title=None):
    if expected_title:
        name = re.search(r'class="apphub_AppName"[^>]*>([^<]+)<', html)
        if not name or _title_key(unescape(name[1])) != expected_title:
            return None
    # Match the storefront's aggregate review count to exclude its recent-review row.
    count = re.search(r'itemprop="reviewCount"\s+content="(\d+)"', html)
    if count:
        for percent, reviews in STEAM_POSITIVE.findall(html):
            if int(reviews.replace(",", "")) == int(count[1]) > 0 and 0 <= int(percent) <= 100:
                return f"{percent}% positive · {int(count[1]):,} reviews"
    return None


def _title_key(title):
    return re.sub(r"[\W_]+", " ", re.sub(r"[™®©]", "", title.casefold())).strip()


def _offer_title(title, kind):
    query = re.sub(r"(?:\s+(?:(?:steam\s+)?keys?\s+)?giveaway|\s+\((?:steam|gog)\))+$", "", title, flags=re.I)
    if kind == "dlc":
        # Only drop a trailing label when the title still explicitly names the DLC.
        query = re.sub(r"^(.+(?:\s-\s|:\s).+)\s+DLC$", r"\1", query, flags=re.I)
    return _title_key(query)


@lru_cache(maxsize=256)
def _steam_listing(wanted, hour):
    # A publisher may distribute a Steam key without linking to the product page.
    if not wanted:
        return None
    page = _get_page(f"https://{STEAM}/search/suggest", monotonic() + 4,
                     {"term": wanted[:160], "f": "games", "cc": "US", "l": "english"})
    matches = {product for url, name in STEAM_MATCH.findall(page)
               if _title_key(unescape(name)) == wanted and (product := _product_url(unescape(url), "game"))}
    return next(iter(matches)) if len(matches) == 1 else None


@lru_cache(maxsize=256)
def _listing_rating(store, url, expected_title, hour):
    params = {"l": "english", "cc": "US"} if store == "Steam" else None
    page = _get_page(url, monotonic() + 4, params)
    rating = _steam_rating(page, expected_title) if store == "Steam" else _product_rating(page, expected_title)
    return f"{rating} · [{store}](<{url}>)" if rating else None


def fetch_rating(url, title, kind, platforms):
    """Return a sourced score, or leave the offer unrated when the match is uncertain."""
    hour = int(monotonic()) // 3600
    product = _product_url(url, kind)
    exact_title = _offer_title(title, kind)
    platform_names = {value.strip().casefold() for value in platforms.split(",")}
    if not product and kind in {"game", "dlc"} and "steam" in platform_names:
        product = _steam_listing(exact_title, hour)
    if kind != "game" and not exact_title:
        return None
    return _listing_rating(*product, exact_title if kind != "game" else None, hour) if product else None
