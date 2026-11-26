"""Persistent ticket and role panels with server-scoped configuration."""

import asyncio
import json
import re
from uuid import uuid4

import discord
from discord import app_commands

from community_commands import result
from database import get_setting, set_setting
from timed_mutes import ROLE_NAMES


PANEL_LOCK = asyncio.Lock()
ROLE_LOCK = asyncio.Lock()
ROLE_PERMISSIONS = {
    "administrator", "manage_roles", "manage_guild", "manage_channels", "manage_webhooks",
    "manage_messages", "manage_threads", "moderate_members", "kick_members", "ban_members",
    "manage_events", "mention_everyone", "manage_nicknames", "mute_members", "deafen_members",
    "move_members", "view_audit_log",
}


def is_ticket_channel(channel):
    if isinstance(channel, discord.Thread):
        channel = channel.parent
    return isinstance(channel, discord.TextChannel) and (channel.topic or '').startswith(
        ('jenny-ticket:', 'closed-jenny-ticket:')
    )


def resolve_role(guild, value):
    match = re.fullmatch(r"(?:<@&)?(\d{15,22})>?", str(value).strip())
    role = guild.get_role(int(match[1])) if match else None
    if role is None or role.is_default():
        raise ValueError("Each role must be a valid role ID or mention from this server.")
    return role


def safe_role(guild, role):
    """Validate again at click time because role permissions and positions can change."""
    return bool(role and role.name not in ROLE_NAMES.values() and not role.is_default() and not role.managed and guild.me
                and guild.me.guild_permissions.manage_roles and guild.me.top_role > role
                and not any(getattr(role.permissions, name) for name in ROLE_PERMISSIONS)
                and not any(getattr(channel.overwrites_for(role), name) is True
                            for channel in guild.channels for name in ROLE_PERMISSIONS))


def parse_ticket_buttons(raw, guild):
    entries = json.loads(raw)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 25:
        raise ValueError("Enter a JSON list with 1 to 25 buttons.")
    buttons = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each button needs a JSON object.")
        text = entry.get("label")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 80:
            raise ValueError("Each button label must contain 1 to 80 characters.")
        emoji = entry.get("emoji")
        if emoji is not None:
            if not isinstance(emoji, str) or len(emoji) > 100 or not emoji.strip():
                raise ValueError("Omit emoji or enter one Unicode or Discord custom emoji.")
            emoji = str(discord.PartialEmoji.from_str(emoji.strip()))
        message, roles = entry.get("message"), entry.get("roles", [])
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 1400:
            raise ValueError("Each ticket button needs a welcome message of up to 1,400 characters.")
        if not isinstance(roles, list) or len(roles) > 10:
            raise ValueError("Ticket roles must be a list of up to 10 role IDs or mentions.")
        buttons.append({"label": text.strip(), "emoji": emoji, "message": message.strip(),
                        "roles": list(dict.fromkeys(resolve_role(guild, role).id for role in roles))})
    return buttons


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
        guild_id = str(interaction.guild_id)
        raw = await asyncio.to_thread(get_setting, f"panel:{self.key}", None, guild_id)
        panel = json.loads(raw) if raw else None
        if not panel or panel["message"] != interaction.message.id or self.slot >= len(panel["buttons"]):
            return await result(interaction, "Panel unavailable", "Ask an administrator to recreate it.", error=True, private=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        button = panel["buttons"][self.slot]
        try:
            if panel["kind"] == "tickets":
                await open_ticket(interaction, self.key, self.slot, panel["category"], button)
            else:
                await toggle_role(interaction, button["role"])
        except discord.HTTPException:
            await result(interaction, "Panel action failed", "Discord rejected the action. Check Jenny's permissions and try again.",
                         error=True, private=True)


class CloseTicket(discord.ui.DynamicItem[discord.ui.Button], template=r"jticket:close"):
    def __init__(self, item=None):
        super().__init__(item or discord.ui.Button(label="Close ticket", emoji="🔒",
                                                    style=discord.ButtonStyle.danger, custom_id="jticket:close"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item)

    async def callback(self, interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        match = re.fullmatch(r"jenny-ticket:([0-9a-f]{20}):\d+:(\d+)", channel.topic or "")
        if not match:
            return await result(interaction, "Ticket already closed", "Ask a staff member if you need the transcript.", private=True)
        opener_id = int(match[2])
        opener = interaction.guild.get_member(opener_id)
        if not (interaction.user.id == opener_id or interaction.permissions.manage_channels or
                any(channel.overwrites_for(role).send_messages is True for role in interaction.user.roles)):
            return await result(interaction, "Permission denied", "Only the opener or ticket staff can close it.", error=True, private=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with PANEL_LOCK:
            if (channel.topic or "") != match[0]:
                return await result(interaction, "Ticket already closed", "The conversation was kept for staff.", private=True)
            changes = channel.overwrites
            for target in changes:
                if target.id == opener_id:
                    changes[target] = discord.PermissionOverwrite(view_channel=False, send_messages=False)
                    break
            else:
                if opener:
                    changes[opener] = discord.PermissionOverwrite(view_channel=False, send_messages=False)
            try:
                await channel.edit(name=("closed-" + channel.name)[:100],
                                   topic="closed-" + match[0], overwrites=changes,
                                   reason=f"Ticket closed by {interaction.user.id}")
            except discord.HTTPException:
                return await result(interaction, "Could not close ticket", "Check Jenny's Manage Channels permission.", error=True, private=True)
        if interaction.user.id == opener_id:
            await result(interaction, "Ticket closed", "Staff can still read the conversation.", private=True)
        else:
            await result(interaction, "Ticket closed", "The opener's access was removed.", private=True)


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
        channels = await guild.fetch_channels()
        existing = discord.utils.find(lambda channel: isinstance(channel, discord.TextChannel) and
                                      channel.category_id == category_id and channel.topic == topic, channels)
        if existing:
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


ROLE_STYLES = {"Blue": "primary", "Gray": "secondary", "Green": "success", "Red": "danger"}
ROLE_ICONS = (("None", None), ("Games", "🎮"), ("Bell", "🔔"), ("Paint", "🎨"), ("Block", "🧱"),
              ("Swords", "⚔️"), ("Books", "📚"), ("Check", "✅"), ("Chat", "💬"), ("Star", "⭐"))


async def publish_panel(interaction, kind, category, buttons, embed):
    """Publish and persist the same panel format for tickets and reaction roles."""
    key = uuid4().hex[:20]
    view = discord.ui.View(timeout=None)
    for slot, button in enumerate(buttons):
        view.add_item(PanelButton(
            key, slot, label=button["label"], emoji=button["emoji"],
            style=getattr(discord.ButtonStyle, button.get("style", "primary")),
        ))
    message = await interaction.channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
    data = {"message": message.id, "kind": kind, "category": category, "buttons": buttons}
    try:
        await asyncio.to_thread(set_setting, f"panel:{key}", json.dumps(data), str(interaction.guild_id))
    except Exception:
        await message.delete()
        raise
    return message


class RolePanelBuilder(discord.ui.View):
    """Build a persistent panel from a private, administrator-only preview."""

    def __init__(self, owner, guild, heading, description):
        super().__init__(timeout=600)
        self.owner, self.heading, self.description = owner, heading, description
        self.entries = []
        self.selected_role = None
        self.style = "primary"
        self.icon = None
        self.use_lock = asyncio.Lock()
        self.published = False
        self.icons = dict((f"standard:{i}", icon) for i, (_, icon) in enumerate(ROLE_ICONS))
        self.icon_picker.options = [discord.SelectOption(label=label, value=f"standard:{i}", emoji=icon, default=i == 0)
                                    for i, (label, icon) in enumerate(ROLE_ICONS)]
        for icon in [emoji for emoji in guild.emojis if emoji.is_usable()][:15]:
            key = f"custom:{icon.id}"
            self.icons[key] = str(icon)
            self.icon_picker.append_option(discord.SelectOption(label=icon.name, value=key, emoji=icon))

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner and interaction.permissions.administrator:
            return True
        await result(interaction, "Permission denied", "Only the administrator who opened this builder can use it.",
                     error=True, private=True)
        return False

    def preview(self, note=""):
        detail = "Choose a role, a button color, and an optional icon, then select **Add button**."
        if self.selected_role:
            detail += f"\nSelected: {discord.utils.escape_markdown(self.selected_role.name)}"
        if self.entries:
            detail += "\n\n" + "\n".join(
                f"{index}. {entry['emoji'] or ''} {discord.utils.escape_markdown(entry['label'])}"
                for index, entry in enumerate(self.entries, 1)
            )
        if note:
            detail += f"\n\n{note}"
        return discord.Embed(title=f"Reaction-role setup · {len(self.entries)}/25"[:256],
                             description=detail[:4000], color=discord.Color.blurple())

    async def refresh(self, interaction, note=""):
        self.add_button.disabled = self.selected_role is None or len(self.entries) == 25
        self.remove_button.disabled = self.finish_button.disabled = not self.entries
        await interaction.response.edit_message(embed=self.preview(note), view=self,
                                                allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Choose a role", row=0)
    async def role_picker(self, interaction, selector):
        role = selector.values[0]
        self.selected_role = role if safe_role(interaction.guild, role) else None
        await self.refresh(interaction, "" if self.selected_role else "Choose a manageable role without staff permissions.")

    @discord.ui.select(placeholder="Choose a button color", row=1, options=[
        discord.SelectOption(label=label, value=value, default=value == "primary")
        for label, value in ROLE_STYLES.items()
    ])
    async def color_picker(self, interaction, selector):
        self.style = selector.values[0]
        for option in selector.options:
            option.default = option.value == self.style
        await self.refresh(interaction)

    @discord.ui.select(placeholder="Choose an icon", row=2)
    async def icon_picker(self, interaction, selector):
        choice = selector.values[0]
        self.icon = self.icons[choice]
        for option in selector.options:
            option.default = option.value == choice
        await self.refresh(interaction)

    @discord.ui.button(label="Add button", style=discord.ButtonStyle.primary, row=3, disabled=True)
    async def add_button(self, interaction, button):
        role = self.selected_role
        if not role or not safe_role(interaction.guild, interaction.guild.get_role(role.id)):
            return await self.refresh(interaction, "Choose a manageable role first.")
        if any(entry["role"] == role.id for entry in self.entries):
            return await self.refresh(interaction, "That role already has a button.")
        if len(self.entries) >= 25:
            return await self.refresh(interaction, "A panel can have up to 25 buttons.")
        self.entries.append({"label": role.name[:80], "role": role.id, "emoji": self.icon, "style": self.style})
        self.selected_role = None
        await self.refresh(interaction, "Button added. Choose another role or publish.")

    @discord.ui.button(label="Remove last", style=discord.ButtonStyle.secondary, row=3, disabled=True)
    async def remove_button(self, interaction, button):
        if self.entries:
            self.entries.pop()
        await self.refresh(interaction)

    @discord.ui.button(label="Publish", style=discord.ButtonStyle.success, row=3, disabled=True)
    async def finish_button(self, interaction, button):
        if self.use_lock.locked() or self.published:
            return await result(interaction, "Panel already submitted", "Wait for the current publish to finish.", private=True)
        if not self.entries or any(not safe_role(interaction.guild, interaction.guild.get_role(entry["role"]))
                                   for entry in self.entries):
            return await self.refresh(interaction, "Add a valid role, or remove any role that became unavailable.")
        async with self.use_lock:
            entries = list(self.entries)
            await interaction.response.defer()
            embed = discord.Embed(title=self.heading, description=self.description, color=discord.Color.blurple())
            try:
                message = await publish_panel(interaction, "roles", None, entries, embed)
            except discord.HTTPException:
                return await interaction.edit_original_response(
                    embed=self.preview("Could not publish. Check channel access and selected emojis."), view=self
                )
            self.published = True
            self.stop()
            await interaction.edit_original_response(
                embed=discord.Embed(title="Panel published", description=f"[Open panel]({message.jump_url})",
                                    color=discord.Color.green()), view=None
            )


class TicketSetup(discord.ui.Modal, title="Configure ticket panel"):
    heading = discord.ui.TextInput(label="Embed title", max_length=160)
    description = discord.ui.TextInput(label="Embed description", style=discord.TextStyle.paragraph, max_length=1500)
    color = discord.ui.TextInput(label="Embed color as hex", default="#5865F2", min_length=6, max_length=7)
    buttons = discord.ui.TextInput(
        label="Button configuration (JSON)", style=discord.TextStyle.paragraph, max_length=4000,
        default='[{"label":"Support","emoji":"🎫","roles":["123456789012345678"],"message":"How can we help?"}]',
    )

    def __init__(self, category):
        super().__init__()
        self.category_id = category.id

    async def on_submit(self, interaction):
        if not interaction.guild or not interaction.permissions.administrator:
            return await result(interaction, "Permission denied", "Only administrators can configure panels.", error=True, private=True)
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", self.color.value):
            return await result(interaction, "Invalid color", "Enter a color like #5865F2.", error=True, private=True)
        try:
            buttons = parse_ticket_buttons(self.buttons.value, interaction.guild)
        except (ValueError, TypeError) as error:
            return await result(interaction, "Invalid button configuration", str(error), error=True, private=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(title=self.heading.value, description=self.description.value,
                              color=int(self.color.value.removeprefix("#"), 16))
        try:
            message = await publish_panel(interaction, "tickets", self.category_id, buttons, embed)
        except discord.HTTPException:
            return await result(interaction, "Could not publish panel", "Check the channel and any custom emojis.", error=True, private=True)
        await result(interaction, "Panel published", f"[Open panel]({message.jump_url})", private=True)


def register_panels(bot):
    bot.add_dynamic_items(PanelButton, CloseTicket)

    @bot.tree.command(description="Publish a configurable private-ticket panel in this channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketpanel(interaction: discord.Interaction, category: discord.CategoryChannel):
        available = category.permissions_for(interaction.guild.me)
        if not (available.view_channel and available.manage_channels and available.manage_roles):
            return await result(interaction, "Category inaccessible",
                                "Jenny needs View Channel, Manage Channels, and Manage Roles in the ticket category.",
                                error=True, private=True)
        await interaction.response.send_modal(TicketSetup(category))

    @bot.tree.command(description="Publish a configurable self-assignable role panel in this channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def reactionroles(interaction: discord.Interaction, title: app_commands.Range[str, 1, 160],
                            description: app_commands.Range[str, 0, 1500] = ""):
        view = RolePanelBuilder(interaction.user.id, interaction.guild, title, description)
        await interaction.response.send_message(embed=view.preview(), view=view, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
