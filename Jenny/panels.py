"""Persistent ticket and role panels with server-scoped configuration."""

import asyncio
import json
import logging
import re
import sqlite3
import unicodedata
from uuid import uuid4
from weakref import WeakValueDictionary

import discord
from discord import app_commands

from community_commands import result
from database import archive_ticket, claim_ticket, get_setting, set_setting
from timed_mutes import ROLE_NAMES
from discord_format import button_emoji, embed_color_picker, expand_channel_mentions, expand_emojis
from command_runtime import COMMAND_TIMEOUT, command_timeout
from audit_log import send_audit


PANEL_LOCK = asyncio.Lock()
ROLE_LOCK = asyncio.Lock()
TICKET_LOCKS = WeakValueDictionary()
TICKET_TOPIC = re.compile(r"jenny-ticket:([0-9a-f]{20}):(\d+):(\d+)")
LOGGER = logging.getLogger(__name__)
ROLE_PERMISSIONS = {
    "administrator", "manage_roles", "manage_guild", "manage_channels", "manage_webhooks",
    "manage_messages", "manage_threads", "moderate_members", "kick_members", "ban_members",
    "manage_events", "mention_everyone", "manage_nicknames", "mute_members", "deafen_members",
    "move_members", "view_audit_log",
}
STAFF_PERMISSION_MASK = discord.Permissions(**dict.fromkeys(ROLE_PERMISSIONS, True)).value


def is_ticket_channel(channel):
    if isinstance(channel, discord.Thread):
        channel = channel.parent
    return isinstance(channel, discord.TextChannel) and (channel.topic or '').startswith(
        ('jenny-ticket:', 'closed-jenny-ticket:')
    )


def safe_role(guild, role):
    """Validate again at click time because role permissions and positions can change."""
    return bool(role and role.name not in ROLE_NAMES.values() and not role.is_default() and not role.managed and guild.me
                and guild.me.guild_permissions.manage_roles and guild.me.top_role > role
                and not role.permissions.value & STAFF_PERMISSION_MASK
                and not any(channel.overwrites_for(role).pair()[0].value & STAFF_PERMISSION_MASK
                            for channel in guild.channels))


class PanelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"jpanel:(?P<key>[0-9a-f]{20}):(?P<slot>[0-9]{1,2})"):
    """Recover a panel button from its message ID and settings after a restart."""

    def __init__(self, key, slot, item=None, **kwargs):
        super().__init__(item or discord.ui.Button(custom_id=f"jpanel:{key}:{slot}", **kwargs))
        self.key, self.slot = key, slot

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["key"], int(match["slot"]), item=item)

    async def callback(self, interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild_id)
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                raw = await asyncio.to_thread(get_setting, f"panel:{self.key}", None, guild_id)
                panel = json.loads(raw) if raw else None
                if (not panel or interaction.message.id not in panel.get("messages", [panel.get("message")])
                        or self.slot >= len(panel["buttons"])):
                    return await result(interaction, "Panel unavailable", "Ask an administrator to recreate it.",
                                        error=True, private=True)
                button = panel["buttons"][self.slot]
                if panel["kind"] == "tickets":
                    channel = await open_ticket(interaction, self.key, self.slot, panel["category"], button)
                    if channel and button.get("command"):
                        await run_ticket_command(interaction, channel, button["command"])
                else:
                    await toggle_role(interaction, button["role"])
        except TimeoutError:
            await command_timeout(interaction)
            await send_audit(interaction.guild, "Panel action timed out", "See the runtime log.",
                             actor=interaction.user, source=interaction.channel, failure=True)
        except Exception as error:
            LOGGER.exception("Panel action failed")
            await result(interaction, "Panel action failed", "Discord rejected the action. Check Jenny's permissions and try again.",
                         error=True, private=True)
            await send_audit(interaction.guild, "Panel action failed", type(error).__name__,
                             actor=interaction.user, source=interaction.channel, failure=True, cooldown=30)


async def run_ticket_command(interaction, channel, hook):
    # Use the opener's real permissions, even if an administrator saved the hook.
    name = hook.get("name")
    if name in TICKET_COMMANDS:
        from model_commands import ModelCommandContext, execute_model_command

        context = ModelCommandContext(interaction.user, channel)
        await execute_model_command(interaction.client, context, name, json.dumps(hook.get("options", {})))


async def ticket_access(interaction):
    channel = interaction.channel
    match = TICKET_TOPIC.fullmatch(channel.topic or "") if isinstance(channel, discord.TextChannel) else None
    if not match or not isinstance(interaction.user, discord.Member):
        return None, False
    raw = await asyncio.to_thread(get_setting, f"panel:{match[1]}", None, str(interaction.guild_id))
    panel = json.loads(raw) if raw else {}
    entries = panel.get("buttons", [])
    staff_roles = entries[int(match[2])].get("roles", []) if int(match[2]) < len(entries) else []
    staff = interaction.permissions.administrator or any(role.id in staff_roles for role in interaction.user.roles)
    return match, staff


def ticket_lock(channel_id):
    return TICKET_LOCKS.setdefault(channel_id, asyncio.Lock())


class ClaimTicket(discord.ui.DynamicItem[discord.ui.Button], template=r"jticket:claim"):
    def __init__(self, item=None):
        super().__init__(item or discord.ui.Button(label="Claim ticket", emoji="🙋",
                                                    style=discord.ButtonStyle.primary, custom_id="jticket:claim"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item)

    async def callback(self, interaction):
        await handle_ticket_claim(interaction)


async def handle_ticket_claim(interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        async with asyncio.timeout(COMMAND_TIMEOUT):
            match, staff = await ticket_access(interaction)
            if not match or not staff:
                return await result(interaction, "Cannot claim ticket", "Only the assigned staff roles or an administrator can claim an open ticket.",
                                    error=True, private=True)
            async with ticket_lock(interaction.channel_id):
                claimed, owner, closed = await asyncio.to_thread(
                    claim_ticket, interaction.channel_id, interaction.guild_id, int(match[3]), match[1],
                    int(match[2]), interaction.user.id,
                )
                if closed:
                    return await result(interaction, "Ticket closing", "Its transcript has already been archived.", private=True)
                if not claimed:
                    return await result(interaction, "Already claimed", f"<@{owner}> is handling this ticket.", private=True)
                if interaction.message and interaction.message.embeds:
                    embed = interaction.message.embeds[0].copy()
                    embed.add_field(name="Claimed by", value=interaction.user.mention, inline=False)
                    try:
                        await interaction.message.edit(embed=embed)
                    except discord.HTTPException:
                        LOGGER.warning("Could not show ticket claimant in channel %s", interaction.channel_id)
                await result(interaction, "Ticket claimed", "You're handling this ticket.", private=True)
    except TimeoutError:
        await command_timeout(interaction)
    except (ValueError, sqlite3.Error, discord.HTTPException):
        LOGGER.exception("Could not claim ticket in channel %s", interaction.channel_id)
        await result(interaction, "Claim failed", "Check Jenny's runtime log and try again.", error=True, private=True)


async def ticket_transcript(channel, bot_member):
    """Capture the channel and all threads Jenny can access before deleting them."""
    threads = {thread.id: thread for thread in channel.threads}
    async for thread in channel.archived_threads(limit=None):
        threads[thread.id] = thread
    async for thread in channel.archived_threads(limit=None, private=True,
                                                 joined=not channel.permissions_for(bot_member).manage_threads):
        threads[thread.id] = thread
    transcript = []
    for source in (channel, *threads.values()):
        messages = [{
            "id": str(message.id), "author_id": str(message.author.id), "author": message.author.display_name,
            "sent_at": message.created_at.isoformat(), "content": message.content,
            "attachments": [{"filename": item.filename, "url": item.url, "size": item.size}
                            for item in message.attachments],
            "embeds": [embed.to_dict() for embed in message.embeds],
        } async for message in source.history(limit=None, oldest_first=True)]
        transcript.append({"id": str(source.id), "name": source.name, "messages": messages})
    return transcript


class CloseTicket(discord.ui.DynamicItem[discord.ui.Button], template=r"jticket:close"):
    def __init__(self, item=None):
        super().__init__(item or discord.ui.Button(label="Close ticket", emoji="🔒",
                                                    style=discord.ButtonStyle.danger, custom_id="jticket:close"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item)

    async def callback(self, interaction):
        channel = interaction.channel
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                match, staff = await ticket_access(interaction)
                if not match:
                    return await result(interaction, "Cannot close ticket", "This channel is not an open Jenny ticket.",
                                        error=True, private=True)
                if not staff:
                    return await result(interaction, "Cannot close ticket", "Only assigned ticket staff or a server administrator can close it.",
                                        error=True, private=True)
                async with ticket_lock(channel.id):
                    transcript = await ticket_transcript(channel, interaction.guild.me)
                    await asyncio.to_thread(archive_ticket, channel.id, interaction.guild_id, int(match[3]), match[1],
                                            int(match[2]), interaction.user.id, transcript)
                    await result(interaction, "Transcript saved", "Closing this ticket channel.", private=True)
                    await channel.delete(reason=f"Ticket archived and closed by {interaction.user.id}")
        except TimeoutError:
            return await command_timeout(interaction)
        except Exception:
            LOGGER.exception("Could not archive and close ticket %s", getattr(channel, "id", None))
            return await result(interaction, "Ticket could not be closed", "The channel was kept. Check Jenny's runtime log and retry.",
                                error=True, private=True)
        await send_audit(interaction.guild, "Ticket archived and closed", f"Saved ticket #{channel.name} ({channel.id}) to the database.",
                         actor=interaction.user)


async def open_ticket(interaction, key, slot, category_id, button):
    guild, opener = interaction.guild, interaction.user
    topic = f"jenny-ticket:{key}:{slot}:{opener.id}"
    async with PANEL_LOCK:
        try:
            category = await guild.fetch_channel(category_id)
        except discord.NotFound:
            category = None
        if not isinstance(category, discord.CategoryChannel):
            return await result(interaction, "Category unavailable", "Ask an administrator to recreate the panel.", error=True, private=True)
        existing = discord.utils.find(lambda channel: channel.topic == topic, guild.text_channels)
        if existing:
            try:
                await guild.fetch_channel(existing.id)
            except discord.NotFound:
                pass  # The channel deletion event may still be in transit.
            else:
                return await result(interaction, "Ticket already open", existing.mention, private=True)
        roles = [role for role_id in button["roles"] if (role := guild.get_role(role_id))]
        if not guild.me:
            return await result(interaction, "Ticket unavailable", "Jenny could not resolve her server membership.", error=True, private=True)
        if len(roles) != len(button["roles"]):
            return await result(interaction, "Ticket roles changed", "Ask an administrator to update this panel.", error=True, private=True)
        access = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                      opener: access, guild.me: access}
        overwrites.update({role: access for role in roles})
        slug = re.sub(r"[^a-z0-9]+", "-", button["label"].lower()).strip("-")[:35] or "support"
        channel = await guild.create_text_channel(f"ticket-{slug}-{opener.id}", category=category, topic=topic,
                                                  overwrites=overwrites, reason=f"Ticket opened by {opener.id}")
        view = discord.ui.View(timeout=None)
        view.add_item(ClaimTicket())
        view.add_item(CloseTicket())
        mentions = " ".join([opener.mention, *(role.mention for role in roles)])
        allowed = discord.AllowedMentions(everyone=False, users=[opener], roles=roles, replied_user=False)
        try:
            await channel.send(mentions, embed=discord.Embed(title=button["label"], description=button["message"],
                               color=discord.Color.blurple()), view=view, allowed_mentions=allowed)
        except discord.HTTPException:
            try:
                await channel.delete(reason="Ticket welcome message could not be sent")
            except discord.HTTPException:
                return await result(interaction, "Ticket created without a welcome message", channel.mention, error=True, private=True)
            raise
    await result(interaction, "Ticket opened", channel.mention, private=True)
    return channel



class RemoveRoleView(discord.ui.View):
    """Personal red control. The shared panel remains identical for every viewer."""

    def __init__(self, user_id, role_id):
        super().__init__(timeout=300)
        self.user_id, self.role_id = user_id, role_id

    @discord.ui.button(label="Remove role", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await result(interaction, "Permission denied", "Only the member who added this role can remove it here.",
                                error=True, private=True)
        await interaction.response.defer()
        async with ROLE_LOCK:
            role = interaction.guild.get_role(self.role_id)
            member = await interaction.guild.fetch_member(self.user_id)
            if not safe_role(interaction.guild, role):
                return await result(interaction, "Role unavailable", "Ask an administrator to check the role.",
                                    error=True, private=True)
            try:
                if role in member.roles:
                    await member.remove_roles(role, reason="Reaction role toggle")
            except discord.HTTPException:
                return await result(interaction, "Role removal failed", "Discord rejected the action.",
                                    error=True, private=True)
        await interaction.edit_original_response(embed=discord.Embed(title="Role removed", description=role.name,
                                                                      color=discord.Color.greyple()), view=None)


async def toggle_role(interaction, role_id):
    async with ROLE_LOCK:
        guild = interaction.guild
        role = guild.get_role(role_id)
        member = await guild.fetch_member(interaction.user.id)
        if not safe_role(guild, role):
            return await result(interaction, "Role unavailable", "Ask an administrator to check its permissions and position.", error=True, private=True)
        removing = role in member.roles
        if removing:
            await member.remove_roles(role, reason="Reaction role toggle")
        else:
            await member.add_roles(role, reason="Reaction role toggle")
    detail = f"{role.name}. Use the same button to {'add it again' if removing else 'remove it'}."
    embed = discord.Embed(title="Role removed" if removing else "Role added", description=detail,
                         color=discord.Color.greyple() if removing else discord.Color.green())
    view = RemoveRoleView(member.id, role.id) if not removing else discord.utils.MISSING
    await interaction.followup.send(embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


COLORS = {"Blue": "primary", "Gray": "secondary", "Green": "success", "Red": "danger"}
MAX_PANEL_BUTTONS = 25
TICKET_COMMANDS = {"faq", "roll", "coinflip", "help", "mods", "modcontents", "recipe",
                   "searchpack", "serverinfo", "freegamesnow"}


def field(label, *, description=None, **kwargs):
    return discord.ui.Label(text=label, description=description, component=discord.ui.TextInput(**kwargs))


def can_create_tickets(category, bot_member):
    if not isinstance(category, discord.CategoryChannel) or bot_member is None:
        return False
    permissions = category.permissions_for(bot_member)
    return permissions.view_channel and permissions.manage_channels and permissions.manage_roles


async def publish_panel(interaction, kind, category, buttons, embed):
    key = uuid4().hex[:20]
    messages = []
    try:
        for index, rows in enumerate(panel_button_pages(buttons)):
            page = embed.copy() if not index else discord.Embed(title=f"{embed.title or 'Panel'} · Continued"[:256],
                                                                 color=embed.color)
            view = discord.ui.View(timeout=None)
            long_labels = []
            for row, entries in enumerate(rows):
                for slot, label in entries:
                    button = buttons[slot]
                    view.add_item(PanelButton(key, slot, row=row, label=label, emoji=button.get("emoji"),
                                              style=getattr(discord.ButtonStyle, button.get("style", "primary"))))
                    if label != button["label"]:
                        long_labels.append(f"**{slot + 1}.** {discord.utils.escape_markdown(button['label'])}")
            if long_labels:
                page.add_field(name="Full button labels", value="\n".join(f"• {label}" for label in long_labels), inline=False)
            messages.append(await interaction.channel.send(embed=page, view=view, allowed_mentions=discord.AllowedMentions.none()))
        data = {"message": messages[0].id, "messages": [message.id for message in messages], "channel": interaction.channel_id,
                "kind": kind, "category": category, "buttons": buttons, "embed": embed.to_dict()}
        await asyncio.to_thread(set_setting, f"panel:{key}", json.dumps(data), str(interaction.guild_id))
    except (Exception, asyncio.CancelledError):
        for message in messages:
            try:
                await message.delete()
            except discord.HTTPException:
                LOGGER.warning("Could not remove unfinished panel message %s", message.id)
        raise
    return messages[0]


def panel_button_pages(buttons):
    """Approximate client text widths and wrap before Discord's component limits."""
    rows = [[]]
    used = 0
    for slot, button in enumerate(buttons):
        width, end = 0, 0
        for char in button["label"]:
            width += (0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1)
            if width > 30:
                break
            end += 1
        truncated = end != len(button["label"])
        label = f"{button['label'][:end].rstrip()}… [{slot + 1}]" if truncated else button["label"]
        cost = (38 if truncated else width) + 6 + (3 if button.get("emoji") else 0)
        if rows[-1] and (len(rows[-1]) == 5 or used + cost > 48):
            rows.append([])
            used = 0
        rows[-1].append((slot, label))
        used += cost
    return [rows[index:index + 5] for index in range(0, len(rows), 5)]


class PanelDetails(discord.ui.Modal, title="Panel settings"):
    def __init__(self, builder, *, initial=False):
        super().__init__(timeout=600)
        self.builder, self.initial = builder, initial
        self.heading = field("Panel title", max_length=160, default=builder.heading or None)
        self.description = field("Panel description", style=discord.TextStyle.paragraph, required=False,
                                 max_length=1500, default=builder.description or None)
        self.color = embed_color_picker(builder.color)
        for item in (self.heading, self.description, self.color):
            self.add_item(item)
        if builder.kind == "tickets":
            defaults = [discord.SelectDefaultValue(id=builder.category_id, type=discord.SelectDefaultValueType.channel)]
            self.category = discord.ui.Label(text="Ticket category", component=discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.category], default_values=defaults))
            self.add_item(self.category)

    async def on_submit(self, interaction):
        if not await self.builder.interaction_check(interaction):
            return
        try:
            heading = self.heading.component.value.strip()
            if not heading:
                raise ValueError("Enter a title for this panel.")
            color = int(self.color.component.values[0])
            if self.builder.kind == "tickets":
                category = interaction.guild.get_channel(self.category.component.values[0].id)
                if not can_create_tickets(category, interaction.guild.me):
                    raise ValueError("Jenny needs View Channel, Manage Channels and Manage Roles in that category.")
                self.builder.category_id = category.id
        except (ValueError, IndexError) as error:
            return await result(interaction, "Invalid panel settings", str(error), error=True, private=True)
        self.builder.heading, self.builder.color = heading, color
        self.builder.description = self.description.component.value.strip()
        if self.initial:
            await interaction.response.send_message(embed=self.builder.preview(), view=self.builder, ephemeral=True)
        else:
            await self.builder.refresh_preview(interaction)


class PanelButtonDetails(discord.ui.Modal, title="Configure panel button"):
    def __init__(self, builder, guild, index=None):
        super().__init__(timeout=600)
        self.builder, self.index = builder, index
        self.original = builder.entries[index] if index is not None else None
        entry = self.original or {}
        ticket = builder.kind == "tickets"
        self.label = field("Button label", default=entry.get("label"), max_length=80, required=ticket,
                           placeholder=None if ticket else "Leave blank to use the role name")
        self.icon = field("Emoji", required=False, default=entry.get("emoji"), max_length=100,
                          placeholder="Paste an emoji or type :computer: or :server_emoji:")
        self.style = discord.ui.Label(text="Button color", component=discord.ui.Select(options=[
            discord.SelectOption(label=name, value=value, default=entry.get("style", "primary") == value)
            for name, value in COLORS.items()
        ]))
        role_ids = entry.get("roles", []) if ticket else [entry.get("role")]
        roles = [discord.SelectDefaultValue(id=role_id, type=discord.SelectDefaultValueType.role)
                 for role_id in role_ids if role_id and guild.get_role(role_id)]
        self.roles = discord.ui.Label(text="Staff roles to notify" if ticket else "Role granted by this button",
                                      component=discord.ui.RoleSelect(required=not ticket, min_values=0 if ticket else 1,
                                                                      max_values=10 if ticket else 1, default_values=roles))
        for item in (self.label, self.icon, self.style, self.roles):
            self.add_item(item)
        if ticket:
            self.welcome = field("Message inside the ticket", default=entry.get("message"),
                                 style=discord.TextStyle.paragraph, max_length=1400)
            self.add_item(self.welcome)

    async def on_submit(self, interaction):
        builder = self.builder
        if not await builder.interaction_check(interaction):
            return
        if self.index is None and len(builder.entries) >= MAX_PANEL_BUTTONS:
            return await result(interaction, "Panel full", "A panel can have up to 25 buttons.", error=True, private=True)
        if self.index is not None and (self.index >= len(builder.entries) or builder.entries[self.index] is not self.original):
            return await result(interaction, "Draft changed", "Select the button again and reopen its form.", error=True, private=True)
        guild = interaction.guild
        warning = None
        try:
            emoji = button_emoji(self.icon.component.value, guild)
        except ValueError:
            emoji = None
            warning = "Jenny saved the button without an emoji because the format or name was invalid."
        try:
            label = self.label.component.value.strip()
            entry = {"label": label, "emoji": emoji,
                     "style": self.style.component.values[0]}
            roles = [guild.get_role(role.id) for role in self.roles.component.values]
            if builder.kind == "tickets":
                welcome = self.welcome.component.value.strip()
                if not label or not welcome:
                    raise ValueError("Enter a button label and a welcome message.")
                if any(role is None or role.is_default() for role in roles):
                    raise ValueError("Select existing staff roles other than @everyone.")
                entry.update(message=welcome, roles=[role.id for role in roles])
                if self.original and self.original.get("command"):
                    entry["command"] = self.original["command"]
            else:
                role = roles[0] if roles else None
                if not safe_role(guild, role):
                    raise ValueError("Pick a role below Jenny without staff permissions.")
                if any(item.get("role") == role.id and item is not self.original for item in builder.entries):
                    raise ValueError("This draft already has a button for that role.")
                entry.update(label=label or role.name[:80], role=role.id)
        except (IndexError, ValueError) as error:
            return await result(interaction, "Invalid button settings", str(error), error=True, private=True)
        if self.index is None:
            builder.entries.append(entry)
            builder.selected = len(builder.entries) - 1
        else:
            builder.entries[self.index] = entry
        await builder.refresh_preview(interaction)
        if warning:
            await result(interaction, "Button saved without an emoji", warning, private=True)


class PanelButtonOrder(discord.ui.Modal, title="Set button order"):
    def __init__(self, builder):
        super().__init__(timeout=600)
        self.builder, self.index = builder, builder.selected
        self.entry = builder.entries[self.index]
        self.order = field("Position from 0 to 24", description="Insert at this position. A number past the last button moves it to the end.",
                           default=f"{self.index:02}", max_length=2)
        self.add_item(self.order)

    async def on_submit(self, interaction):
        if not await self.builder.interaction_check(interaction):
            return
        if self.index >= len(self.builder.entries) or self.builder.entries[self.index] is not self.entry:
            return await result(interaction, "Draft changed", "Select the button again.", private=True)
        value = self.order.component.value.strip()
        if not re.fullmatch(r"(?:[0-9]|[01][0-9]|2[0-4])", value):
            return await result(interaction, "Invalid button order", "Enter a number from 0 to 24.", error=True, private=True)
        self.builder.entries.pop(self.index)
        self.builder.selected = min(int(value), len(self.builder.entries))
        self.builder.entries.insert(self.builder.selected, self.entry)
        await self.builder.refresh_preview(interaction)


class TicketCommandSetup(discord.ui.Modal, title="Command to run when ticket opens"):
    def __init__(self, builder):
        super().__init__(timeout=600)
        self.builder, self.index = builder, builder.selected
        self.entry = builder.entries[self.index]
        command = self.entry.get("command") or {}
        self.command = field("Command name or blank to disable", required=False, max_length=40,
                             default=command.get("name"), placeholder="Examples: faq, mods, recipe, roll")
        self.options = field("Command options", description="One name=value per line. Example: dice=3.",
                             required=False, style=discord.TextStyle.paragraph, max_length=500,
                             default="\n".join(f"{key}={value}" for key, value in command.get("options", {}).items()) or None)
        self.add_item(self.command)
        self.add_item(self.options)

    async def on_submit(self, interaction):
        if not await self.builder.interaction_check(interaction):
            return
        if self.index >= len(self.builder.entries) or self.builder.entries[self.index] is not self.entry:
            return await result(interaction, "Draft changed", "Select the button again.", private=True)
        name = self.command.component.value.strip().lstrip("/").lower()
        try:
            if name:
                if name not in TICKET_COMMANDS:
                    raise ValueError("Choose an available ticket command: " + ", ".join(sorted(TICKET_COMMANDS)) + ".")
                command = interaction.client.tree.get_command(name)
                parameters = {value.name: value for value in command.parameters}
                entries = {}
                for line in self.options.component.value.splitlines():
                    key, separator, value = line.partition("=")
                    key = key.strip()
                    if not separator or key not in parameters or key in entries:
                        raise ValueError("Enter one valid, unique option per line, such as dice=3.")
                    parameter = parameters[key]
                    if parameter.type is discord.AppCommandOptionType.boolean:
                        if value.strip().lower() not in {"true", "false"}:
                            raise ValueError(f"{key} must be true or false.")
                        entries[key] = value.strip().lower() == "true"
                    else:
                        entries[key] = value.strip()
                if any(p.required and p.name not in entries for p in parameters.values()):
                    raise ValueError("Provide every required option. Run /help for the command's options.")
                self.entry["command"] = {"name": name, "options": entries}
            else:
                self.entry.pop("command", None)
        except (ValueError, AttributeError) as error:
            return await result(interaction, "Invalid command setup", str(error), error=True, private=True)
        await self.builder.refresh_preview(interaction)


class PanelBuilder(discord.ui.View):
    """Editable, private draft. Publishing creates a fresh persistent panel."""
    def __init__(self, owner_id, kind, category_id=None, heading="", description="", color=0x5865F2, entries=None):
        super().__init__(timeout=600)
        self.owner_id, self.kind, self.category_id = owner_id, kind, category_id
        self.heading, self.description, self.color = heading, description, color
        self.entries = [dict(entry) for _, entry in sorted(enumerate(entries or []),
                                                          key=lambda item: item[1].get("order", item[0]))]
        self.selected = 0 if self.entries else None
        self.publishing = False
        if kind != "tickets":
            self.remove_item(self.command_button)
        self.update_controls()

    async def interaction_check(self, interaction):
        if not interaction.guild or interaction.user.id != self.owner_id or not interaction.permissions.administrator:
            await result(interaction, "Permission denied", "Only the administrator who opened this draft can edit it.",
                         error=True, private=True)
            return False
        if self.is_finished() or self.publishing:
            await result(interaction, "Draft unavailable", "The draft expired or publishing has already started.", private=True)
            return False
        return True

    def preview(self):
        lines = [self.description[:800]] if self.description else []
        if self.category_id:
            lines.append(f"**Ticket category** · <#{self.category_id}>")
        for index, entry in enumerate(self.entries):
            marker = "»" if index == self.selected else "•"
            hook = f" · /{entry['command']['name']}" if entry.get("command") else ""
            lines.append(f"{marker} **{index:02}** {entry.get('emoji') or ''} "
                         f"{discord.utils.escape_markdown(entry['label'])}{hook}")
        embed = discord.Embed(title=f"Draft · {self.heading or 'New panel'}"[:256],
                              description="\n".join(lines)[:3900] or "Add your first button.", color=self.color)
        embed.set_footer(text=f"{len(self.entries)}/{MAX_PANEL_BUTTONS} buttons")
        return embed

    def update_controls(self):
        valid = self.selected is not None and 0 <= self.selected < len(self.entries)
        self.button_picker.options = [
            discord.SelectOption(label=f"{index:02} · {entry['label']}"[:100],
                                 value=str(index), default=index == self.selected)
            for index, entry in enumerate(self.entries)
        ] or [discord.SelectOption(label="No buttons yet", value="empty")]
        self.button_picker.disabled = not self.entries
        self.add_button.disabled = len(self.entries) >= MAX_PANEL_BUTTONS
        self.edit_button.disabled = self.remove_button.disabled = self.command_button.disabled = self.order_button.disabled = not valid
        self.finish_button.disabled = not self.entries

    async def refresh_preview(self, interaction):
        self.update_controls()
        await interaction.response.edit_message(embed=self.preview(), view=self, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.select(placeholder="Select any button to edit", row=0)
    async def button_picker(self, interaction, selector):
        self.selected = int(selector.values[0])
        await self.refresh_preview(interaction)

    @discord.ui.button(label="Add button", style=discord.ButtonStyle.primary, row=1)
    async def add_button(self, interaction, button):
        await interaction.response.send_modal(PanelButtonDetails(self, interaction.guild))

    @discord.ui.button(label="Edit button", row=1)
    async def edit_button(self, interaction, button):
        await interaction.response.send_modal(PanelButtonDetails(self, interaction.guild, self.selected))

    @discord.ui.button(label="Set order", row=1)
    async def order_button(self, interaction, button):
        await interaction.response.send_modal(PanelButtonOrder(self))

    @discord.ui.button(label="Remove button", style=discord.ButtonStyle.danger, row=1)
    async def remove_button(self, interaction, button):
        if self.selected is not None and self.selected < len(self.entries):
            self.entries.pop(self.selected)
        self.selected = 0 if self.entries else None
        await self.refresh_preview(interaction)

    @discord.ui.button(label="On-open command", row=1)
    async def command_button(self, interaction, button):
        await interaction.response.send_modal(TicketCommandSetup(self))

    @discord.ui.button(label="Panel settings", row=2)
    async def settings_button(self, interaction, button):
        await interaction.response.send_modal(PanelDetails(self))

    @discord.ui.button(label="Publish", style=discord.ButtonStyle.success, row=2)
    async def finish_button(self, interaction, button):
        if not self.entries or self.publishing:
            return await result(interaction, "Draft unavailable", "Add a button first.", error=True, private=True)
        guild = interaction.guild
        if self.kind == "tickets" and not can_create_tickets(guild.get_channel(self.category_id), guild.me):
            return await result(interaction, "Category inaccessible", "Change the category in Panel settings.",
                                error=True, private=True)
        if self.kind == "roles" and any(not safe_role(guild, guild.get_role(entry["role"])) for entry in self.entries):
            return await result(interaction, "Role unavailable", "Edit the affected button before publishing.",
                                error=True, private=True)
        if any(not guild.get_role(role_id) for entry in self.entries for role_id in entry.get("roles", [])):
            return await result(interaction, "Staff role unavailable", "Edit the affected button before publishing.",
                                error=True, private=True)
        self.publishing = True
        await interaction.response.defer()
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                entries = [dict(entry, order=index) for index, entry in enumerate(self.entries)]
                for entry in entries:
                    if "message" in entry:
                        entry["message"] = expand_emojis(expand_channel_mentions(entry["message"], guild), guild)
                panel_embed = discord.Embed(
                    title=expand_emojis(self.heading, guild)[:256],
                    description=expand_emojis(expand_channel_mentions(self.description, guild), guild)[:4096], color=self.color,
                )
                published = await publish_panel(interaction, self.kind, self.category_id, entries, panel_embed)
            self.stop()
            await interaction.edit_original_response(embed=discord.Embed(
                title="Panel published", description=f"[Open panel]({published.jump_url})", color=discord.Color.green(),
            ), view=None)
            summary = "\n".join(f"{entry.get('emoji') or '•'} {discord.utils.escape_markdown(entry['label'])}"
                                for entry in entries)[:900]
            await send_audit(guild, f"{self.kind.title()} panel published", f"[Open panel]({published.jump_url})\n{summary}",
                             actor=interaction.user, source=interaction.channel, snapshot=panel_embed)
        except TimeoutError:
            await command_timeout(interaction)
        except (ValueError, discord.DiscordException) as error:
            LOGGER.warning("Panel publication failed: %s", type(error).__name__)
            await result(interaction, "Could not publish panel", "Check the channel, roles and custom emojis.",
                         error=True, private=True)
        finally:
            self.publishing = False


def register_panels(bot):
    bot.add_dynamic_items(PanelButton, ClaimTicket, CloseTicket)

    async def assign_join_role(member):
        if member.bot or member.pending:
            return
        guild = member.guild
        role_id = await asyncio.to_thread(get_setting, "join_role", "", str(guild.id))
        if not role_id or not role_id.isdecimal():
            return
        role = guild.get_role(int(role_id))
        if role and role in member.roles:
            return
        try:
            if not safe_role(guild, role):
                raise ValueError("The configured role is missing, privileged, or out of reach.")
            async with asyncio.timeout(10):
                await member.add_roles(role, reason="Jenny join role")
        except (ValueError, discord.DiscordException, TimeoutError) as error:
            LOGGER.warning("Could not assign join role %s in guild %s: %s", role_id, guild.id, error)
            await send_audit(guild, "Join role could not be assigned", f"Check role ID {role_id}, permissions, and role order.",
                             failure=True, cooldown=120)

    bot.add_listener(assign_join_role, "on_member_join")

    @bot.listen("on_member_update")
    async def assign_role_after_screening(before, after):
        if before.pending and not after.pending:
            await assign_join_role(after)

    @bot.tree.command(description="Set or clear the automatic role for new server members")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(role="Role for new members. Leave blank to disable automatic assignment.")
    async def joinrole(interaction: discord.Interaction, role: discord.Role | None = None):
        guild = interaction.guild
        if role is not None and (not safe_role(guild, role) or
                                 (interaction.user.id != guild.owner_id and interaction.user.top_role <= role)):
            return await result(interaction, "Role unavailable", "Choose a non-staff role below Jenny and your highest role.",
                                error=True, private=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await asyncio.to_thread(set_setting, "join_role", str(role.id) if role else "", str(guild.id))
        if role:
            await result(interaction, "Join role configured", f"New members receive {role.mention} after any rules screening.",
                         private=True)
        else:
            await result(interaction, "Join role disabled", "Existing members keep their roles.", private=True)

    @bot.tree.command(description="Claim the current ticket as a member of its assigned support staff")
    async def claimticket(interaction: discord.Interaction):
        await handle_ticket_claim(interaction)

    @bot.tree.command(description="Build a private-ticket panel in this channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketpanel(interaction: discord.Interaction, category: discord.CategoryChannel):
        if not can_create_tickets(category, interaction.guild.me):
            return await result(interaction, "Category inaccessible",
                                "Jenny needs View Channel, Manage Channels and Manage Roles in that category.",
                                error=True, private=True)
        await interaction.response.send_modal(PanelDetails(PanelBuilder(interaction.user.id, "tickets", category.id),
                                                           initial=True))

    @bot.tree.command(description="Build a self-assignable role panel in this channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def reactionroles(interaction: discord.Interaction, title: app_commands.Range[str, 1, 160],
                            description: app_commands.Range[str, 0, 1500] = ""):
        view = PanelBuilder(interaction.user.id, "roles", heading=title, description=description)
        await interaction.response.send_message(embed=view.preview(), view=view, ephemeral=True)

    @bot.tree.command(description="Copy a Jenny ticket or reaction-role panel into an editable draft")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(message="Original message ID or Discord message link", channel="Original channel for a bare message ID")
    async def copyembed(interaction: discord.Interaction, message: str, channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        link = re.fullmatch(r"https://(?:www\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", message.strip())
        if link and int(link[1]) == guild.id:
            channel = guild.get_channel_or_thread(int(link[2]))
            message = link[3]
        elif channel is None:
            channel = interaction.channel
        if not (message.strip().isdigit() and isinstance(channel, (discord.TextChannel, discord.Thread))
                and channel.permissions_for(interaction.user).view_channel):
            return await result(interaction, "Panel message unavailable", "Use its channel and message ID or a link from this server.",
                                error=True, private=True)
        try:
            original = await channel.fetch_message(int(message))
        except discord.HTTPException:
            return await result(interaction, "Panel message unavailable", "Check the message ID and channel.",
                                error=True, private=True)
        if original.author.id != bot.user.id or not original.embeds:
            return await result(interaction, "Unsupported message", "Select a ticket or reaction-role panel published by Jenny.",
                                error=True, private=True)
        key = next((match[1] for row in original.components for child in row.children
                    if (match := re.fullmatch(r"jpanel:([0-9a-f]{20}):\d+", getattr(child, "custom_id", "") or ""))), None)
        raw = await asyncio.to_thread(get_setting, f"panel:{key}", None, str(guild.id)) if key else None
        panel = json.loads(raw) if raw else {}
        if original.id not in panel.get("messages", [panel.get("message")]) or panel.get("kind") not in {"tickets", "roles"}:
            return await result(interaction, "Unsupported message", "Select a saved ticket or reaction-role panel.",
                                error=True, private=True)
        embed = discord.Embed.from_dict(panel["embed"]) if panel.get("embed") else original.embeds[0]
        view = PanelBuilder(interaction.user.id, panel["kind"], panel["category"], embed.title or "Panel",
                            embed.description or "", embed.color.value if embed.color else 0x5865F2, panel["buttons"])
        await interaction.edit_original_response(embed=view.preview(), view=view)
