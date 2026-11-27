"""Server-configurable welcome embeds, with optional local image storage."""

import asyncio
import hashlib
from io import BytesIO
import json
import logging
from pathlib import Path
import re
from uuid import uuid4

import discord
from discord import app_commands
from PIL import Image

from audit_log import send_audit
from command_runtime import COMMAND_TIMEOUT, command_timeout
from community_commands import result
from database import get_setting, set_setting
from discord_format import embed_color_picker, expand_channel_mentions, expand_emojis


LOGGER = logging.getLogger(__name__)
IMAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "welcome_images"
IMAGE_LIMIT = 8 * 1024 * 1024
IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}
TOKENS = re.compile(r"\{(user|username|display_name|server|member_count|channel_id)\}")


def welcome_text(template, member, channel):
    guild = member.guild
    values = {"user": member.mention, "username": member.name, "display_name": member.display_name,
              "server": guild.name, "member_count": str(guild.member_count or len(guild.members)), "channel_id": channel.mention}

    text = TOKENS.sub(lambda match: values[match[1]], expand_channel_mentions(template, guild))
    return expand_emojis(text, guild)


def save_welcome_image(data):
    if len(data) > IMAGE_LIMIT:
        raise ValueError("Choose an image no larger than 8 MB.")
    with Image.open(BytesIO(data)) as image:
        extension = IMAGE_FORMATS.get(image.format)
        if not extension:
            raise ValueError("Upload a PNG, JPEG, WebP, or GIF image.")
        image.verify()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{hashlib.sha256(data).hexdigest()[:32]}.{extension}"
    destination = IMAGE_DIR / filename
    if not destination.exists():
        temporary = IMAGE_DIR / f"{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(data)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return filename


def welcome_image_path(config):
    filename = config.get("image_file") or ""
    if config.get("image") == "custom" and re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|webp|gif)", filename):
        return IMAGE_DIR / filename
    return None


def build_welcome(config, channel, member=None):
    """Render a real welcome, or a template snapshot when no member is supplied."""
    def render(text):
        return (welcome_text(text, member, channel) if member is not None else
                expand_emojis(expand_channel_mentions(text, channel.guild), channel.guild))

    joined = member.joined_at if member is not None else None
    embed = discord.Embed(title=render(config["title"])[:256], description=render(config["text"])[:4096],
                          color=config["color"], timestamp=joined or discord.utils.utcnow())
    path = welcome_image_path(config)
    if path is not None and path.is_file():
        embed.set_image(url=f"attachment://{path.name}")
        return embed, path
    if member is not None and config["image"] == "avatar":
        embed.set_image(url=member.display_avatar.url)
    return embed, None


async def post_welcome(config, channel, member, *, preview=False):
    embed, image = build_welcome(config, channel, member)
    if preview:
        embed.title = f"Preview · {embed.title}"[:256]
    if config["image"] == "custom" and (image is None or not channel.permissions_for(member.guild.me).attach_files):
        LOGGER.warning("Sending welcome without custom image in guild %s: file missing or Attach Files denied", member.guild.id)
        embed.set_image(url=None)
        image = None
    await channel.send(embed=embed, files=[discord.File(image)] if image else [],
                       allowed_mentions=discord.AllowedMentions.none())


class WelcomeSetup(discord.ui.Modal, title="Configure welcome messages"):
    def __init__(self, channel, mode, settings, include_bots):
        super().__init__(timeout=600)
        self.channel, self.mode, self.settings = channel, mode, settings
        self.include_bots = include_bots
        self.heading = discord.ui.TextInput(max_length=160, default=settings.get("title", "Welcome to {server}!"))
        self.body = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=1500,
                                         default=settings.get("text", "Hey {user}, you're member #{member_count}! :wave:"))
        self.color = embed_color_picker(settings.get("color", 0x5865F2))
        self.add_item(discord.ui.Label(text="Welcome embed title", component=self.heading))
        self.add_item(discord.ui.Label(text="Welcome message", component=self.body,
                                       description="{user} · {server} · {member_count} · {channel_id} · {123…} for another channel"))
        self.add_item(self.color)
        if mode == "custom":
            existing = welcome_image_path(settings)
            required = not (existing and existing.is_file())
            self.image = discord.ui.FileUpload(required=required, min_values=1 if required else 0, max_values=1)
            self.add_item(discord.ui.Label(text="Upload an image", description="PNG, JPEG, WebP or GIF, up to 8 MB.",
                                           component=self.image))

    async def on_submit(self, interaction):
        if not interaction.permissions.administrator:
            return await result(interaction, "Permission denied", "Only administrators can configure welcomes.",
                                error=True, private=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                title, text = self.heading.value.strip(), self.body.value.strip()
                if not title or not text:
                    raise ValueError("Enter both a welcome title and a message.")
                color = int(self.color.component.values[0])
                filename = self.settings.get("image_file") if self.mode == "custom" else None
                if self.mode == "custom" and self.image.values:
                    upload = self.image.values[0]
                    if upload.size > IMAGE_LIMIT:
                        raise ValueError("Choose an image no larger than 8 MB.")
                    filename = await asyncio.to_thread(save_welcome_image, await upload.read())
                if self.mode == "custom" and not filename:
                    raise ValueError("Upload an image for the custom image mode.")
                config = dict(enabled=True, channel=self.channel.id, title=title, text=text, color=color,
                              image=self.mode, image_file=filename, include_bots=self.include_bots)
                await asyncio.to_thread(set_setting, "welcome", json.dumps(config), str(interaction.guild_id))
        except TimeoutError:
            return await command_timeout(interaction)
        except Image.DecompressionBombError:
            return await result(interaction, "Welcome image too large", "Choose an image with smaller dimensions.",
                                error=True, private=True)
        except (IndexError, ValueError, OSError, discord.HTTPException) as error:
            LOGGER.warning("Could not save welcome in guild %s: %s", interaction.guild_id, type(error).__name__)
            return await result(interaction, "Welcome setup failed", str(error) if isinstance(error, ValueError)
                                else "Check the image and Jenny's file permissions.", error=True, private=True)
        await result(interaction, "Welcome messages enabled", f"New members will be greeted in {self.channel.mention}. "
                     "Use `/welcome preview:True` to test it without waiting for a join.", private=True)
        snapshot, image = build_welcome(config, self.channel)
        await send_audit(interaction.guild, "Welcome embed saved", f"Destination: {self.channel.mention}",
                         actor=interaction.user, source=interaction.channel, snapshot=snapshot, attachment=image)


def register_welcome(bot):
    async def settings(guild):
        raw = await asyncio.to_thread(get_setting, "welcome", "{}", str(guild.id))
        return json.loads(raw)

    @bot.tree.command(description="Configure or disable welcome embeds for new server members")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel for welcomes", image="Image in the welcome embed",
                           enabled="Set False to disable without losing your settings",
                           include_bots="Welcome bots as well as people. Defaults to True for new configurations.",
                           preview="Post a labeled preview of your saved welcome in its channel")
    @app_commands.choices(image=[
        app_commands.Choice(name="No image", value="none"),
        app_commands.Choice(name="Member's profile picture", value="avatar"),
        app_commands.Choice(name="Custom uploaded image", value="custom"),
    ])
    async def welcome(interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                      image: str | None = None, enabled: bool = True, include_bots: bool | None = None,
                      preview: bool = False):
        config = await settings(interaction.guild)
        if preview:
            target = interaction.guild.get_channel(config.get("channel"))
            if not isinstance(target, discord.TextChannel):
                return await result(interaction, "No welcome to preview", "Configure a welcome channel first.",
                                    error=True, private=True)
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                await post_welcome(config, target, interaction.user, preview=True)
            except (OSError, discord.HTTPException) as error:
                LOGGER.warning("Welcome preview failed in guild %s: %s", interaction.guild_id, type(error).__name__)
                return await result(interaction, "Welcome preview failed", "Check Jenny's channel permissions and runtime log.",
                                    error=True, private=True)
            return await result(interaction, "Welcome preview sent", f"See {target.mention}.", private=True)
        if not enabled:
            config["enabled"] = False
            await asyncio.to_thread(set_setting, "welcome", json.dumps(config), str(interaction.guild_id))
            return await result(interaction, "Welcome messages disabled", "Your configuration is saved.", private=True)
        channel = channel or interaction.guild.get_channel(config.get("channel"))
        mode = image or config.get("image", "none")
        if not isinstance(channel, discord.TextChannel) or not interaction.guild.me:
            return await result(interaction, "Welcome channel required", "Select a text channel.", error=True, private=True)
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links
                and (mode != "custom" or perms.attach_files)):
            return await result(interaction, "Welcome channel inaccessible", "Jenny needs View Channel, Send Messages, "
                                "Embed Links, and Attach Files for a custom image.", error=True, private=True)
        include_bots = config.get("include_bots", True) if include_bots is None else include_bots
        await interaction.response.send_modal(WelcomeSetup(channel, mode, config, include_bots))

    @bot.listen("on_member_join")
    async def send_welcome(member):
        guild = member.guild
        try:
            async with asyncio.timeout(20):
                config = await settings(guild)
                if not config.get("enabled") or (member.bot and not config.get("include_bots", True)):
                    return
                channel = guild.get_channel(config["channel"])
                if not isinstance(channel, discord.TextChannel):
                    raise ValueError("The welcome channel no longer exists")
                await post_welcome(config, channel, member)
        except (OSError, ValueError, KeyError, TimeoutError, discord.HTTPException) as error:
            LOGGER.warning("Welcome could not be sent in guild %s: %s", guild.id, type(error).__name__)
            await send_audit(guild, "Welcome message failed", "Check the welcome channel, permissions and image.",
                             failure=True, cooldown=60)
