"""Shared period, mode and page navigation for server rankings."""

import asyncio
from collections import defaultdict
from datetime import timedelta
import logging

import discord
from discord import app_commands

from community_commands import result
from database import (
    get_activity_ranks, get_invite_ranks, get_trivia_leaderboard,
    record_attributed_invite, record_invited_departure,
)


LOGGER = logging.getLogger(__name__)
PERIODS = {"Daily": 1, "Weekly": 7, "Monthly": 30, "All time": None}
MODES = {"Activity": {"Text": "text", "Voice": "voice"},
         "Invites": {"Invited": "invited", "Left": "left"},
         "Modpack trivia": {"Points": "points", "Wins": "wins"}}


def period_start(days):
    return (discord.utils.utcnow() - timedelta(days=days)).isoformat() if days else ""


class LeaderboardView(discord.ui.View):
    def __init__(self, guild, owner_id, kind):
        super().__init__(timeout=600)
        self.guild, self.owner_id, self.kind = guild, owner_id, kind
        self.period, self.mode = "All time", next(iter(MODES[kind].values()))
        self.page = 0
        self.pages = []
        self.lock = asyncio.Lock()
        self.filters = []
        for name in PERIODS:
            item = discord.ui.Button(label=name, row=0)

            async def select_period(interaction, period=name):
                await self.change(interaction, period=period)

            item.callback = select_period
            self.filters.append((item, name, None))
            self.add_item(item)
        for label, mode in MODES[kind].items():
            item = discord.ui.Button(label=label, row=1)

            async def select_mode(interaction, selected=mode):
                await self.change(interaction, mode=selected)

            item.callback = select_mode
            self.filters.append((item, None, mode))
            self.add_item(item)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await result(interaction, "Open your own leaderboard", "Run the command to browse your own view.", private=True)
        return False

    def rows(self):
        since = period_start(PERIODS[self.period])
        guild_id = str(self.guild.id)
        if self.kind == "Activity":
            return get_activity_ranks(guild_id, since, self.mode)
        if self.kind == "Invites":
            return get_invite_ranks(guild_id, since, self.mode)
        return get_trivia_leaderboard(guild_id, limit=10000, since=since, order=self.mode)

    def build(self, rows):
        mode_label = next(label for label, name in MODES[self.kind].items() if name == self.mode)
        lines = []
        rank = None
        for index, row in enumerate(rows, 1):
            mine = str(row["user_id"]) == str(self.owner_id)
            if mine:
                rank = index
            if self.kind == "Activity":
                amount = row["amount"]
                if self.mode == "voice":
                    hours, rest = divmod(amount, 3600)
                    minutes, seconds = divmod(rest, 60)
                    score = f"{hours:,}h {minutes}m {seconds}s connected"
                else:
                    score = f"{amount:,} messages"
            elif self.kind == "Invites":
                score = f"{row['invited']:,} invited · {row['departed']:,} left"
            else:
                score = f"{row['points']:,} points · {row['games_won']:,} wins · {row['games_played']:,} games"
            line = f"{index}. <@{row['user_id']}> · {score}"
            lines.append(f"**{line} · You**" if mine else line)
        self.pages = []
        chunks = [lines[offset:offset + 12] for offset in range(0, len(lines), 12)] or [["No activity recorded for this period."]]
        for index, chunk in enumerate(chunks, 1):
            embed = discord.Embed(title=f"{self.kind} leaderboard · {mode_label} · {self.period}",
                                  description="\n".join(chunk), color=discord.Color.blurple())
            position = f"#{rank:,}" if rank else "unranked"
            embed.set_footer(text=f"Your rank: {position} · Page {index}/{len(chunks)}")
            self.pages.append(embed)
        self.page = min(self.page, len(self.pages) - 1)
        self.refresh_buttons()

    def refresh_buttons(self):
        for control in (self.previous, self.page_button, self.next):
            visible = control in self.children
            if len(self.pages) < 2 and visible:
                self.remove_item(control)
            elif len(self.pages) > 1 and not visible:
                self.add_item(control)
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= len(self.pages) - 1
        self.page_button.label = f"Page {self.page + 1}/{len(self.pages)}"
        for item, period, mode in self.filters:
            item.style = discord.ButtonStyle.primary if period == self.period or mode == self.mode else discord.ButtonStyle.secondary

    async def change(self, interaction, *, period=None, mode=None):
        await interaction.response.defer()
        async with self.lock:
            self.period = period or self.period
            self.mode = mode or self.mode
            self.page = 0
            self.build(await asyncio.to_thread(self.rows))
            await interaction.edit_original_response(embed=self.pages[0], view=self)

    async def show_page(self, interaction, page):
        self.page = max(0, min(len(self.pages) - 1, page))
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="← Back", row=2)
    async def previous(self, interaction, button):
        await self.show_page(interaction, self.page - 1)

    @discord.ui.button(label="Page 1/1", row=2)
    async def page_button(self, interaction, button):
        from discord_commands import ResultPageModal
        await interaction.response.send_modal(ResultPageModal(self))

    @discord.ui.button(label="Next →", row=2)
    async def next(self, interaction, button):
        await self.show_page(interaction, self.page + 1)


async def show_leaderboard(interaction, kind):
    if not interaction.guild:
        return await result(interaction, "Server required", "Run this command in a server.", error=True)
    await interaction.response.defer(thinking=True)
    view = LeaderboardView(interaction.guild, interaction.user.id, kind)
    view.build(await asyncio.to_thread(view.rows))
    await interaction.followup.send(embed=view.pages[0], view=view, allowed_mentions=discord.AllowedMentions.none())


def register_leaderboards(bot):
    activity = app_commands.Group(name="activity", description="See recorded server activity", guild_only=True)
    invites = app_commands.Group(name="invites", description="See recorded invite activity", guild_only=True)
    known = {}
    locks = defaultdict(asyncio.Lock)

    async def invite_counts(guild):
        if not guild.me or not guild.me.guild_permissions.manage_guild:
            return None
        try:
            async with asyncio.timeout(7):
                codes = await guild.invites()
                return {invite.code: (invite.uses or 0, invite.inviter.id if invite.inviter else None) for invite in codes}
        except (discord.HTTPException, TimeoutError) as error:
            LOGGER.warning("Could not read invite counts in guild %s: %s", guild.id, type(error).__name__)
            return None

    async def baseline(guild):
        async with locks[guild.id]:
            counts = await invite_counts(guild)
            if counts is not None:
                known[guild.id] = counts

    @bot.listen("on_ready")
    async def invites_ready():
        for guild in bot.guilds:
            await baseline(guild)

    bot.add_listener(baseline, "on_guild_join")

    @bot.listen("on_invite_create")
    async def invite_created(invite):
        if invite.guild and invite.guild.id in known:
            async with locks[invite.guild.id]:
                known[invite.guild.id][invite.code] = (invite.uses or 0, invite.inviter.id if invite.inviter else None)

    @bot.listen("on_member_join")
    async def track_invite(member):
        if member.bot:
            return
        guild = member.guild
        async with locks[guild.id]:
            current = await invite_counts(guild)
            old = known.pop(guild.id, None)
            if current is None:
                return
            known[guild.id] = current
            if old is None:
                return
            changes = [(uses - old[code][0], inviter) for code, (uses, inviter) in current.items()
                       if code in old and uses > old[code][0]]
            if len(changes) == 1 and changes[0][0] == 1 and changes[0][1] and changes[0][1] != member.id:
                await asyncio.to_thread(record_attributed_invite, guild.id, member.id, changes[0][1])

    @bot.listen("on_member_remove")
    async def track_departure(member):
        await asyncio.to_thread(record_invited_departure, member.guild.id, member.id)

    @activity.command(name="leaderboard", description="Rank members by chat messages or connected voice time")
    async def activity_leaderboard(interaction: discord.Interaction):
        await show_leaderboard(interaction, "Activity")

    @invites.command(name="leaderboard", description="Rank members by tracked invitations and departures")
    async def invite_leaderboard(interaction: discord.Interaction):
        await show_leaderboard(interaction, "Invites")

    @invites.command(name="user", description="Show a member's tracked invitations and how many invitees left")
    @app_commands.describe(member="Member to check. Defaults to you.")
    async def invite_user(interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        rows = await asyncio.to_thread(get_invite_ranks, interaction.guild.id, user_id=member.id)
        stats = rows[0] if rows else {"invited": 0, "departed": 0}
        await result(interaction, "Invite activity", f"{member.mention} · {stats['invited']:,} invited · {stats['departed']:,} left"
                     "\nOnly joins tied to an invite Jenny observed are counted.")

    bot.tree.add_command(activity)
    bot.tree.add_command(invites)
