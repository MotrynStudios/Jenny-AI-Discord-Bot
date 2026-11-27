"""Bound Discord command execution and report expired interactions."""

import asyncio
import logging

import discord
from discord import app_commands


COMMAND_TIMEOUT = 52
LOGGER = logging.getLogger(__name__)


class MultiChoiceView(discord.ui.View):
    """Collect several combinable filter options in one Discord select."""
    def __init__(self, owner, choices, placeholder, confirm):
        super().__init__(timeout=300)
        self.owner, self.confirm = owner, confirm
        self.submitted = False
        self.selected = []
        self.options = discord.ui.Select(placeholder=placeholder, min_values=0, max_values=len(choices),
                                         options=[discord.SelectOption(label=label, value=value) for value, label in choices])
        self.options.callback = self.selection_changed
        self.add_item(self.options)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner and not self.submitted:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message("This picker belongs to another request or was already submitted.",
                                                    ephemeral=True)
        return False

    async def selection_changed(self, interaction):
        self.selected = list(self.options.values)
        await interaction.response.defer()

    @discord.ui.button(label="Confirm selection", style=discord.ButtonStyle.primary, row=1)
    async def submit(self, interaction, button):
        self.submitted = True
        self.stop()
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await self.confirm(interaction, self.selected)
        except TimeoutError:
            await command_timeout(interaction)


async def command_timeout(interaction):
    LOGGER.warning("Discord interaction timed out: %s", interaction.id)
    embed = discord.Embed(title="Command timed out", description="Check Jenny's logs for details.",
                          color=discord.Color.red())
    try:
        async with asyncio.timeout(4):
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=None)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.NotFound:
        if interaction.channel and hasattr(interaction.channel, "send"):
            try:
                async with asyncio.timeout(3):
                    await interaction.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.HTTPException, TimeoutError):
                pass
    except (discord.HTTPException, TimeoutError):
        pass


class JennyCommandTree(app_commands.CommandTree):
    async def _call(self, interaction):
        # discord.py dispatches checks, transformers and callbacks through this entry point.
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await super()._call(interaction)
        except TimeoutError:
            await command_timeout(interaction)
        finally:
            elapsed = loop.time() - started
            if elapsed >= 1 and interaction.type is discord.InteractionType.application_command:
                name = (interaction.data or {}).get("name", "unknown")
                LOGGER.warning("Discord command /%s took %.0fms including network and providers", name, elapsed * 1000)
