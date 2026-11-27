# Jenny AI Discord Bot

Jenny is a self-hosted Discord AI bot with memory, voice chat, image understanding, and a Minecraft modpack library. She can keep up with a conversation, look up a recipe in your pack, or run a Discord command when you ask in plain English.

Jenny started as a goofy nod to the character from the original Minecraft Jenny Mod. She shares the name, with an adapted personality and character traits for Discord. This is an independent project with no sexual content. Jenny is here to hang out and help you play Minecraft, whether you're figuring out a recipe or exploring a new modpack.

You only need to know how to copy API keys, create a Discord bot, and run a few terminal commands. Start with text chat and add voice, web search, or Minecraft data when you want them.

**Jenny is a young project and is still in development.** The RAG system has been tested with **Minecraft 1.21.1 NeoForge mods**. Other mods, mod loaders and Minecraft versions may work, but they have not all been tested. Some data may be missing or extracted incorrectly. If you find a problem, please [report it](SUPPORT.md).

## Contents

- [What's new in v0.96bf](#whats-new-in-v096bf)
- [What Jenny can do](#what-jenny-can-do)
- [Quick start](#quick-start)
- [Create and invite your Discord bot](#create-and-invite-your-discord-bot)
  - [Privileged intents](#privileged-intents)
  - [Installation and permissions](#installation-and-permissions)
  - [Role hierarchy](#role-hierarchy)
- [API keys and free usage](#api-keys-and-free-usage)
  - [Multiple API key support](#multiple-api-key-support)
- [Configure the environment file](#configure-the-environment-file)
- [Optional voice service](#optional-voice-service)
  - [Language and voice replies](#language-and-voice-replies)
- [Minecraft modpack data](#minecraft-modpack-data)
- [Commands](#commands)
- [Server tools](#server-tools)
  - [Moderation](#moderation)
  - [Tickets and reaction roles](#tickets-and-reaction-roles)
  - [Free-game alerts](#free-game-alerts)
  - [Welcome messages](#welcome-messages)
  - [Activity and invite tracking](#activity-and-invite-tracking)
  - [Discord audit and runtime logs](#discord-audit-and-runtime-logs)
  - [Restart and updates](#restart-and-updates)
- [Memory and data](#memory-and-data)
- [Resource use](#resource-use)
- [FAQ](#faq)
  - [Setup and chat](#setup-and-chat)
  - [Voice and media](#voice-and-media)
  - [Minecraft and trivia](#minecraft-and-trivia)
  - [Server tools and commands](#server-tools-and-commands)
  - [Providers, privacy and support](#providers-privacy-and-support)
- [Roadmap](#roadmap)
- [Project files and development](#project-files-and-development)
- [Support and license](#support-and-license)

## What's new in v0.96bf

- **Server setup.** Configure an automatic join role and welcome embed, build ticket or reaction-role panels, and copy a published panel into a new draft. Assigned staff and administrators can claim and close tickets. Jenny saves the transcript before deleting the channel.
- **Free games and add-ons.** Choose several platforms, ping roles inside the alert card, and route add-ons separately. Cards show store or platform badges, direct claim links, and ratings for offers Jenny can verify. Alerts update in place when published stock changes or an offer ends.
- **Activity and moderation.** Activity, invite, and trivia rankings use the same period controls and pagination. Use periods like `30m` or `1w` for mutes, timeouts, and timed bans.
- **Voice and context.** Jenny queues voice replies, prioritizes recent messages and embeds, and describes images before answering. `/language` selects the chat and transcription language, and each runtime has its own archived log.

See the [full changelog](CHANGELOG.md#096bf) or run `/changelog` in Discord.

## What Jenny can do

- Chat in servers and DMs, answer replies to her messages, and use recent context and saved summaries.
- Understand images and sampled frames from supported GIFs, transcribe voice notes, and talk in a server voice channel with the optional voice service.
- Browse your installed mods, search recipe outputs or ingredients, show grids for shaped recipes, and display materials for supported workbench recipes.
- Host timed solo or multiplayer Minecraft trivia, with separate question categories and a server leaderboard.
- Run the same Discord commands from natural language, show usage and profiles, and manage roles when both the requester and Jenny have the required permissions.
- Run a support desk with ticket channels, publish self-assignable role buttons, and post free-game alerts.

## Quick start

1. Install [Python 3.12](https://www.python.org/downloads/) and [Git](https://git-scm.com/downloads). [Node.js](https://nodejs.org/en/download) 22.12 or newer and [FFmpeg](https://ffmpeg.org/download.html) are for the optional voice service.
2. Clone [Jenny on GitHub](https://github.com/MotrynStudios/Jenny-AI-Discord-Bot) or download the ZIP and open a terminal in the project folder.
3. [Create a Discord bot](#create-and-invite-your-discord-bot) and get one [text model API key](#api-keys-and-free-usage).
4. Create a virtual environment, install the dependencies, and copy `.env.example` to `.env`.

Windows PowerShell:

~~~powershell
git clone https://github.com/MotrynStudios/Jenny-AI-Discord-Bot.git
cd Jenny-AI-Discord-Bot
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
~~~

On macOS or Linux, use `python3.12 -m venv .venv`, `./.venv/bin/python -m pip install -r requirements.txt`, and `cp .env.example .env`. Skip the clone step if you already downloaded the project.

Open `.env` in a text editor. For basic chat, fill in `DISCORD_TOKEN` and, for example, `GROQ_API_KEY_1`. Leave the other keys blank. Then start Jenny:

~~~powershell
.\.venv\Scripts\python.exe Jenny\Jenny-Bot.py
~~~

On macOS or Linux use `./.venv/bin/python Jenny/Jenny-Bot.py`. On Windows you can also run `.\start.bat`. Once Jenny is online, run `/health` in Discord and say “Hey Jenny.” Use `/setmainchannel` in the channel you want her to use for saved chat searches and voice command results. Use `/setlogschannel` if you want a separate channel for interaction and profile logs.

## Create and invite your Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications), choose **New Application**, and name it. Under **Bot**, use **Reset Token** to generate or replace the bot token. Put it in `DISCORD_TOKEN`. The application ID, public key, and client secret do not go in that field.
2. Set the intents below on the **Bot** page and save your changes.
3. Under **Installation**, enable **Guild Install**, choose **Discord Provided Link**, and configure the install scopes and bot permissions below. Copy the install link, open it, select your server, and authorize. Your account needs permission to manage the server. See the [Discord quick start](https://docs.discord.com/developers/quick-start/getting-started).
4. In Discord, check the bot role and the channel's permission overrides. A private channel may require a separate permission grant. For your own servers only, you can turn off **Public Bot** so other users cannot install it. Leave **Requires OAuth2 Code Grant** off for this setup. No redirect URL or interactions endpoint is needed because Jenny connects to the Discord gateway.

### Privileged intents

| Bot setting | Value | What it does |
| --- | --- | --- |
| Message Content Intent | On | Lets Python read ordinary message text and attachments, including messages that say “Jenny” without an account mention. |
| Server Members Intent | On | Lets Jenny track members, names, roles, and profile changes and resolve users for commands. |
| Presence Intent | Off | Jenny publishes her own activity but does not subscribe to everyone else's presence. |

Apps that Discord has verified may need approval to use privileged intents. See [Discord's gateway intent rules](https://docs.discord.com/developers/events/gateway#privileged-intents).

### Installation and permissions

Use `bot` and `applications.commands` for Guild Install. Jenny can use DMs with users who share a server with the bot. You do not need User Install for that.

| Permission | When to grant it |
| --- | --- |
| View Channel, Send Messages, Read Message History | In channels Jenny should use. History lets her load older replied-to messages. |
| Embed Links | For recipe cards, trivia and command reports, and profile embeds. |
| Send Messages in Threads | If you want Jenny to reply inside threads. |
| Connect, Speak | In the voice channels you want Jenny to use. |
| Manage Roles | Assigning and removing roles, reaction-role panels, timed mutes, and the channel permission overwrites used by tickets. |
| Manage Channels | Creating, editing, and closing ticket channels in the selected category. |
| Manage Server | Reading invite use counts for invite leaderboards. |
| Attach Files | Uploading a custom image in welcome messages. |
| Kick Members, Ban Members, Moderate Members | Grant the permissions for `/kick`, `/ban`, and `/timeout` respectively if you plan to use those commands. |

For a private test server you control, **Administrator** is an option for trying the full feature set. It gives the bot every server permission and access to channels regardless of channel overrides. For a regular server, grant the permissions in the table for the features you use. Self-hosting lets you control the process and local files. Discord permissions and external API requests still apply.

Members who use slash commands need **Use Application Commands** in the channel. See [Discord permissions](https://docs.discord.com/developers/topics/permissions) and [command permissions](https://docs.discord.com/developers/docs/interactions/slash-commands#permissions) if commands are missing for specific roles.

### Role hierarchy

In Discord, open **Server Settings → Roles**. Drag Jenny's highest role above every role she should assign or remove, including roles offered in reaction panels and the roles used for mutes. For moderation, Jenny also needs to sit above the target member's highest role. Jenny's role can stay under the owner and staff roles she has no reason to manage.

For example, with **Owner → Jenny → Helpers → Members**, Jenny can grant or remove **Helpers** when the requesting member has the required permissions and rank. A member whose highest role is **Owner** remains above Jenny. **Administrator** does not bypass role order. Discord integration-managed roles and the server owner are also protected. [Discord's role hierarchy](https://docs.discord.com/developers/topics/permissions#permission-hierarchy)

## API keys and free usage

Start with one text provider. Add other providers as you enable features such as web search or voice.

| Service | What Jenny uses it for | Get a key and check limits |
| --- | --- | --- |
| Groq | GPT-OSS chat, Qwen image analysis, Whisper transcription | Sign into [GroqCloud](https://console.groq.com/keys), create an API key, and paste it into `GROQ_API_KEY_1`. Check your [account limits](https://console.groq.com/settings/limits) and [rate limit guide](https://console.groq.com/docs/rate-limits). The published free table lists GPT-OSS 120B at 30 requests/minute, 1,000/day, 8,000 tokens/minute, and 200,000/day. Whisper also has audio duration quotas. |
| OpenRouter | Optional first chat route | Create a key in [OpenRouter settings](https://openrouter.ai/settings/keys). The example uses [GPT-OSS 120B's free variant](https://openrouter.ai/openai/gpt-oss-120b:free). Free variants do not require buying credits and are normally capped at 20 requests/minute and 50/day without a qualifying credit purchase. See the [free model guide](https://openrouter.ai/collections/free-models). |
| Google Gemini | Optional chat, vision, trivia and memory fallback | Create a key in [Google AI Studio](https://aistudio.google.com/apikey) and verify the project plan. [Model pricing](https://ai.google.dev/gemini-api/docs/pricing) shows which models have free API access. [Limits](https://ai.google.dev/gemini-api/docs/rate-limits) vary by model and project. Gemini's [terms](https://ai.google.dev/gemini-api/terms) have age, audience and regional rules. Leave it disabled if those rules do not fit your server. |
| Tavily | Optional web lookup | Sign into the [Tavily dashboard](https://app.tavily.com/) and copy a key to `TAVILY_API_KEY_1`. Tavily documents 1,000 free credits a month without a card. Jenny requests basic search, which uses one credit per call. See [API credits](https://docs.tavily.com/documentation/api-credits). |
| Deepgram | Optional spoken responses | Create a project and API key in the [Deepgram console](https://console.deepgram.com/). Deepgram documents a [$200 starter credit](https://developers.deepgram.com/guides/fundamentals/make-your-first-api-request). Check [pricing](https://deepgram.com/pricing) and your project's [Flux TTS](https://developers.deepgram.com/docs/flux-tts/voices) access. Credit can run out. |

You can run basic Jenny chat on a supported free tier without linking a credit card. Keep paid billing off and use the exact free model ID where a provider has separate paid and free routes. Check the linked dashboards for current limits and model availability. Optional voice uses Deepgram credits. You can skip a service if its terms or signup requirements do not work for you.

### Multiple API key support

Jenny accepts one or more API keys for Groq, OpenRouter, Gemini, Tavily, and Deepgram. Add numbered entries such as `GROQ_API_KEY_1` and `GROQ_API_KEY_2` to your `.env` for credential rotation and failover. Jenny has no fixed cap on how many keys you can configure. [Key setup](CONFIGURATION.md#credentials)

**Provider limits still apply.** Use credentials you are authorized to use and follow each provider's terms, rate limits, and usage policies. Several keys can share the same quota. Jenny's multi-key support is for authorized credential management and availability. Do not create or combine accounts to bypass provider-imposed limits. [Groq acceptable use policy](https://console.groq.com/docs/legal/ai-policy) · [OpenRouter rate limits](https://openrouter.ai/docs/api/reference/limits)

## Configure the environment file

Copy [`.env.example`](.env.example) to a file named exactly `.env` in the project root. It contains plain `NAME=value` lines. Add your real token after the equals sign without angle brackets. Both runtimes load this file. Restart them after edits. For every field and default, see the [configuration reference](CONFIGURATION.md).

Minimal example with placeholders:

~~~dotenv
DISCORD_TOKEN=replace_with_your_bot_token
GROQ_API_KEY_1=replace_with_your_groq_key
~~~

Keep `.env` private. A previously configured `OPENROUTER_MODEL=openai/gpt-oss-120b` still selects the paid route. Set `OPENROUTER_MODEL=openai/gpt-oss-120b:free` to use the free variant.

## Optional voice service

Start Python first. Fill in a Groq key for Whisper and a Deepgram key for spoken replies. Install the pinned voice dependencies once:

~~~powershell
npm ci --prefix voice_service
node voice_service/index.js
~~~

On Windows, `.\RUN_VOICE_SERVICE.bat` starts the installed Node runtime. `.\start_hybrid_system.bat` checks that the Python API is already reachable and opens the voice service in a second window. Both processes use the same Discord bot token. They have separate runtime logs. Use `/joinvc` from a server voice channel, and `/leavevc` to disconnect.

`/replymode` controls future **chat-triggered** replies. Live voice turns normally speak. Ask for a text copy if you want one. Command results such as voice timings still go to the text channel. Jenny transcribes voice notes in DMs and in the main text channel. Replies to voice notes are text. Use `/deafen` to pause listening or `/deafen deafened:False` to resume. Jenny ignores audio from other bots. With several distinct Groq keys, live transcription can process one clip per key in parallel. Stale clips expire under load.

### Language and voice replies

Jenny defaults to English for chat and speech recognition. A server administrator can use `/language code:` to choose the language Jenny writes in and the language Whisper listens for. For DMs and servers without an override, set `GROQ_STT_LANGUAGE` in `.env` and restart both runtimes. The configured Deepgram voice speaks English. Jenny strips emojis, URLs and non-Latin text before sending speech to Deepgram.

Voice recognition can run in parallel, but Jenny handles reply turns one at a time in each server. Another person speaking won't cancel your reply. The queue discards requests that expire or belong to a voice session Jenny has already left.

## Minecraft modpack data

Put your mod JARs in `mods/` and instance config files in `config/`, or pass their paths to the builder. Then run:

~~~powershell
.\.venv\Scripts\python.exe tools\rag\build_index.py --skip-trivia
~~~

This extracts files, creates searchable documents, and builds a local vector index. Run without `--skip-trivia` to also build the trivia bank, or use `--trivia-only` after an existing extraction. Trivia generation uses your model quota. It aims for 100 questions each for descriptions, recipes, and items/blocks, subject to usable local evidence and provider availability. Read the [RAG guide](tools/rag/README.md) for supported formats, flags, and troubleshooting. Restart Jenny after refreshing the index.

Mixed trivia gives roughly half its questions to recipes when enough are available. A server administrator can run `/generatetrivia count:100 category:recipes difficulty:medium` to add questions to the installation's shared bank. Results may be lower than requested when there are too few unused clues or the model providers cannot finish.

Jenny searches extracted data for installed pack facts. Recipe cards use source patterns for grids and list the materials and bench for supported custom recipes such as TaCZ. The **Web search** button accepts a query or uses the current result if left blank. Web results are external references and may describe a different game version or pack.

## Commands

Use `/help` to see commands you can run in the current context. Enter a command name, such as `/help command:recipe`, for its options, defaults and choices. You can also ask Jenny to run a supported command in chat, DMs, or voice. Admin and role checks still apply. Moderation, panel setup, free-game configuration, and host maintenance use slash commands. If a command cannot finish within a minute, Jenny reports a timeout and records it in the runtime log.

| Area | Commands |
| --- | --- |
| Setup, health and feedback | `/help`, `/health`, `/invite`, `/report`, `/setmainchannel`, `/getmainchannel`, `/setlogschannel`, `/getlogschannel`, `/togglejenny`, `/models`, `/setmodel`, `/changelog` |
| Minecraft | `/mods`, `/modcontents`, `/recipe`, `/searchpack`, `/trivia`, `/trivialeaderboard`, `/generatetrivia` (admin) |
| Voice | `/joinvc`, `/leavevc`, `/voicestatus`, `/replymode`, `/voicetimes`, `/deafen` |
| Server tools | `/mute`, `/ban`, `/kick`, `/timeout`, `/ticketpanel`, `/claimticket`, `/reactionroles`, `/copyembed`, `/freegames`, `/freegamesrole`, `/language`, `/welcome`, `/joinrole`, `/restart`, `/update` |
| Rankings | `/activity leaderboard`, `/invites user`, `/invites leaderboard`, `/trivialeaderboard` |
| Quick commands | `/faq`, `/coinflip`, `/roll`, `/freegamesnow`, `/myaddons` |
| People and roles | `/serverinfo`, `/user`, `/profile`, `/avatar`, `/roles`, `/profilehistory`, `/recentchanges`, `/finduser`, `/lastseen`, `/assignrole`, `/removerole`, `/refreshprofiles` |
| Memory and usage | `/searchchat`, `/summarize`, `/mymemory`, `/forget` (admin), `/myusage`, `/tokenusage` |

`/recipe item:` searches outputs and `used_item:` searches ingredients. `/mods mod:` filters the roster and `/modcontents mod:` browses one mod by category. When a result spans multiple pages, use **Back**, **Next**, or the page number to navigate.


## Server tools

### Moderation

Use the **period** field on `/mute`, `/timeout`, or `/ban`. Write `30m` for minutes, `2h` for hours, `3d` for days, or `1w` for weeks. A bare number means minutes. Mutes and timeouts have a 28-day maximum. Use `period:0` to clear a mute or timeout early.

`/mute member: period: mode:` temporarily restricts text, voice, or both. Jenny creates a role for the selected mode on first use. **Text** blocks posting and starting threads. **Voice** blocks speaking in voice and sending voice recordings. **Both** applies both sets of restrictions. Expiration survives a restart. The requester needs **Manage Roles** and **Moderate Members**. Jenny needs **Manage Roles** in the affected channels and must be above the member and mute roles.

`/timeout` uses Discord's built-in timeout, and `/kick` removes a current member. `/ban` preserves the member's message history. Omit its period for a permanent ban, or enter a period and Jenny will remove her timed ban when it expires. If Jenny is offline at expiry, she checks it after restarting. Discord permissions and both role hierarchies apply. These commands are available as slash commands.

### Tickets and reaction roles

Create a Discord category for tickets, and give Jenny **View Channel**, **Manage Channels**, and **Manage Roles** there. Run `/ticketpanel category:` in the channel where members should see the ticket buttons. The selected category is where Jenny creates the private channels.

1. Enter the panel title and description, then choose a named embed color from the menu.
2. Select **Add button**. Enter the label and the message for that ticket type, pick a button color, and select up to 10 staff roles. Paste an emoji or type a shortcode such as `:computer:`, `:desktop:`, or `:support:` for a custom server emoji. An invalid emoji leaves the button intact and Jenny tells you she saved it without an icon.
3. Add up to 25 buttons. Select a draft button to edit, remove, or **Set order** from 00 to 24. Moving a button into an occupied position inserts it there and shifts the other buttons. A position past the end moves it to the last spot. Shortened labels appear in full on the card, and panels with more than five rows get continuation cards. **Panel settings** lets you change the title, description, color, and category before publishing.
4. Optionally select **On-open command** for that button. Enter an allowed command such as `faq`, `recipe`, or `roll`. Put named options on separate lines, such as `dice=3`. It runs in the new ticket as the opener, with the opener’s permissions. Commands that affect moderation, roles, accounts, memory, or the host can’t be run automatically from tickets.
5. Select **Publish**. The draft is visible only to you until you publish it. Members can reopen a ticket of that type after an assigned staff member or administrator closes it.

Each button creates a private text channel in the selected category for the member, Jenny, and the configured staff roles. Discord administrators retain access. Each member can have one open ticket per button and can open a new one after closing it. The welcome post pings the configured roles if Discord allows Jenny to mention them. Use mentionable roles or give Jenny **Mention Everyone** specifically where those pings are needed.

Assigned staff and administrators can use **Claim ticket**, `/claimticket`, and **Close ticket**. The first eligible claim wins and survives a restart. When an eligible staff member closes a ticket, Jenny writes the transcript to the local `ticket_records` table, then deletes the Discord channel. It saves messages and accessible threads, plus attachment names and links. Discord attachment links can expire. Jenny keeps the ticket open if the archive cannot be saved. Ticket conversations stay out of automated chat memory and AI prompts.

To set up reaction roles, create the roles in **Server Settings → Roles**. Put Jenny's highest role above the roles she should assign, and give her **Manage Roles**. Run `/reactionroles title:` in the channel you want people to use. Select **Add button**, pick an existing role, and optionally set a label and emoji. Choose the embed color in **Panel settings** and a button style for each role. Arrange the buttons with **Set order**, then select **Publish**. Jenny checks the roles again when members click, so a role that later gains staff permissions or moves above Jenny stops being self-assignable.

Use `/copyembed` with the link to a published Jenny ticket or reaction-role panel to start a new draft. You can also use its message ID and specify its original channel. Publishing creates a new panel in the current channel. After verifying it, an administrator can remove the old panel. Jenny sends a read-only copy of the new panel to the configured logs channel.

Click a shared button to add or remove its role. Discord uses the same color for a public button for everyone. The private confirmation provides a red **Remove role** button for the member who clicked. Jenny must be above the roles and have **Manage Roles**. Staff permissions make a role ineligible. Both kinds of panel survive a bot restart.

### Free-game alerts

Use `/freegamesnow` in a server or DM to browse active giveaways. Choose one platform in the command or leave `platform` empty to pick several platforms, such as Steam and GOG. The alert setup uses the same picker. Turn on `dlcs_and_addons` to include DLCs, add-ons, and loot. Results include claim links and listed end dates when available. If GamerPower is unreachable, requests that include **Any**, **PC**, or **Epic Games Store** fall back to the US Epic storefront and label the source. Check your own store region before claiming. [Epic Games Store](https://store.epicgames.com/en-US/free-games)

Run `/freegames` with a destination channel. Set `addons_channel` if you want DLCs, add-ons, and loot to use a separate channel. This also enables add-ons. Run it with `enabled:False` to stop new alerts.

- **Full games:** Use `/freegamesrole role:` to set a general alert role, or choose a platform such as Steam. A matching platform role takes priority. If an offer lists both PC and Epic Games Store and you hold both roles, one post can mention both. Choose PC for broad coverage or individual stores for narrower alerts.
- **Add-ons:** Set `/freegamesrole platform:Add-Ons role:` to ping one role for DLC and loot across the selected platforms. These posts never ping the platform or general game roles. Run `/myaddons` for a private list filtered to your platform roles, even without the Add-Ons role. Specify `platform:` to browse that platform without changing any alert subscriptions.

Omit `role` to clear a rule. A role must be mentionable or Jenny needs **Mention Everyone** in the destination channel. Scheduled alerts use a Discord card that can ping the configured roles inside the card itself. Browse results are ordinary embeds and don't ping subscribers. [Discord Text Display reference](https://docs.discord.com/developers/components/reference#text-display)

Jenny polls GamerPower every 30 minutes and remembers alerts across restarts. Alerts with a listed end time are edited to say **Offer ended**. When a new undated giveaway exposes an explicit remaining quantity in the feed or its GamerPower claim button, Jenny shows **Remaining** and updates the original card on each poll as the count changes. A confirmed zero marks the card **Out of stock** and finishes tracking. Claimed totals and missing values do not establish remaining stock.

Cards place a small distributor or platform icon at the top right and the offer art at the bottom. Jenny uses a matching server emoji such as `:steam:` or `:epicgames:`, a storefront icon, or the GamerPower source badge when available. **Giveaway by** identifies the actual distributor even when the icon represents a platform. The linked artwork and marks belong to their respective owners.

The **Rating** line links to the product's public score on Steam, GOG, Google Play, or the App Store when Jenny can verify it. Steam shows the percentage of positive reviews. Other supported stores show their own rating scale. Packs and DLC need a rating for that exact product. An offer without a verified score displays **No verified rating for this offer**. If Jenny cannot resolve a claim URL, the original feed link stays available. Availability varies by region and store. [GamerPower API](https://www.gamerpower.com/api-read) · [Steam user reviews](https://partner.steamgames.com/doc/store/getreviews)

### Welcome messages

1. Enable **Server Members Intent** in the Developer Portal so Jenny receives join events. Give her **View Channel**, **Send Messages**, and **Embed Links** in the destination. For a custom image, give her **Attach Files** as well.
2. Run `/welcome channel: image:` as an administrator. Choose a member avatar, a custom upload, or no image. The form accepts a PNG, JPEG, WebP, or GIF up to 8 MB for custom images. Enter a title and message, pick a named border color, and submit. A saved template also goes to the configured logs channel.
3. Run `/welcome preview:True` to post a labeled test in the configured channel. Each real welcome shows when the member joined. Set `include_bots:False` if you only want to welcome people. Use `/welcome enabled:False` to pause without losing the template.

Both text fields accept emoji and placeholders. Use `{user}` for a member mention, `{username}` for the account name, `{display_name}` for their server name, `{server}` for the server name, `{member_count}` for the current count, and `{channel_id}` for the selected welcome channel. In the message body, use `{123456789012345678}` or `{channel:123456789012345678}` with a real channel ID to mention another channel. Repeat the placeholder for multiple channels.

For example, use `Hey {user}, welcome to {server}! Start with {123456789012345678}. :wave:` and replace the digits with a channel ID from the same server. A channel mention still follows Discord's visibility permissions.

Administrators can run `/joinrole role:` to give one non-staff role to new human members after any Discord rules screening. Jenny needs **Manage Roles** and must be above the role. Run `/joinrole` without a role to disable it. Changes apply to future joins.

### Activity and invite tracking

`/activity leaderboard` ranks members by messages or time connected to voice. `/invites user` shows tracked invitees and how many have left. `/invites leaderboard` ranks invites or departures. All three leaderboards, including `/trivialeaderboard`, offer **Daily**, **Weekly**, **Monthly**, and **All time**. Your rank appears on each page, and the navigation row appears when there is more than one page. Member and usage reports also have **All time**.

Jenny saves open voice sessions once a minute. An unexpected shutdown can lose up to a minute of connected time per open session. Time while the bot is offline is unavailable.

Invite tracking needs **Manage Server** for the bot. Discord doesn't tell the bot which link a new member used, so Jenny compares invite use counts and records a join when she can attribute it to one link. Ambiguous joins, vanity links and joins while the bot is offline may be absent. Timed trivia rankings use games played after this update. Earlier trivia scores still count under **All time**.

### Discord audit and runtime logs

Use `/setlogschannel` to choose a staff channel for admin commands, moderation targets and reasons, profile changes, and actionable failures such as rate limits. Jenny keeps regular conversations in her database and leaves them out of the Discord audit channel.

Live logs are `logs/python-latest.log` and `logs/voice-latest.log`. Each line has a UTC timestamp. On restart the previous session is archived under its start date. Earlier `*-runtime.log` files remain available locally.

The Python log records slash commands that take at least one second. Those timings include Discord, database work, and any provider calls. `/voicetimes` breaks down recent voice turns by stage. The Node log can skip lines if disk writes back up, then reports how many it skipped.

### Restart and updates

The server owner can run `/update` to compare the local checkout tag or changelog version with the latest GitHub release and read its notes. The check also works on a ZIP installation.

Start Jenny under the supervisor to enable `/restart` and `/update install:True`:

```bash
python tools/run_supervised.py --voice
```

Omit `--voice` for Python only. Close existing Jenny runtimes before starting the supervisor. The member requesting a restart or installation must own both the current Discord server and the Jenny application. For team-owned applications, Jenny checks the Discord team owner. It looks these identities up through Discord, so no guild or operator ID is required.

Installing an update needs a clean Git checkout on `main` with the official HTTPS origin. Jenny verifies that the fetched tag points at the same commit GitHub reported, checks changed code for syntax errors, and permits only a fast-forward that preserves local files. Releases that change dependencies need to be installed from the host. Jenny announces the result and release notes after reconnecting.

## Memory and data

Jenny keeps recent conversation turns per Discord user and conversation. The Python task summarizes active streams and keeps dated personal summaries for seven days. Durable personal details are keyed by Discord user ID across that person's DMs, server chat and voice. Channel summaries and raw conversations are scoped to their source. Recent visible command results can be included as context in the same channel.

The database, generated pack files, and runtime logs live on the host that runs Jenny. Discord still handles the messages, attachments, and voice traffic. When enabled, model routes receive the relevant message context, saved memories, and pack excerpts. Vision sends images or sampled frames for analysis. Transcription sends audio to Groq, spoken replies send text to Deepgram, and web lookup sends queries to Tavily. If you enable `/report`, submitted report text becomes a GitHub issue. Discuss these settings and data retention with your server before enabling them. See [configuration and storage](CONFIGURATION.md#storage-and-privacy).

## Resource use

Early measurements on the maintainer's setup were about **80 MB for Python** and **40 MB for Node.js** during light use, and roughly **500 MB for Python** with a warm RAG index. Actual memory varies with the pack, server count, traffic, and library versions. Extraction and indexing can use substantially more memory and CPU than normal chat. Leave `RAG_PREWARM=false` to load the encoder only when a semantic question needs it. Keyword search and semantic retrieval share the parsed pack documents.

Jenny streams synthesized voice audio from memory and releases playback listeners after each chunk. Speech capture and queues have time and size limits. Large local reports run their database work away from Discord's event loop. Discord itself, giveaway and search feeds, and AI providers each contribute network latency. Use the logs to tell a slow local path from a slow upstream call.

## FAQ

These are the same questions and answers you can browse with `/faq` in Discord.

### Setup and chat

#### How do I talk to Jenny?

Mention her, include her name in your message, reply directly to her, or message her in DMs.

#### Why doesn't Jenny answer?

Run `/health` and check that Jenny can view and send messages in the channel. The host should check Message Content Intent, whether `/togglejenny` disabled chat, and the recent errors in `logs/python-latest.log`.

#### Why don't the slash commands appear?

Check that the bot was installed with the `bot` and `applications.commands` scopes and that you have Use Application Commands in the channel. Some commands require server permissions or only work in a server. Start with `/help`. [Discord setup](#installation-and-permissions)

#### What is the main channel for?

Set it with `/setmainchannel` for voice command results, server voice note transcription, and saved chat searches. Jenny can still answer when addressed in other channels she can access.

#### Can I change her personality?

Yes. Edit `Jenny/Prompts.py` and restart Python. Leave secrets and private user data out of prompts. [Project guide](PROJECT.md)

### Voice and media

#### Why isn't voice working?

Run the Node service as well as Python. Confirm `/health` shows it online, join a voice channel, then run `/joinvc`. Check Connect and Speak permissions, provider access, and FFmpeg on `PATH`. A failed join reports an error and stops. See `logs/voice-latest.log` for connection, STT and TTS details.

#### How do I get a reply in voice or in chat?

`/replymode` changes how Jenny answers messages you type. Live voice requests normally get a spoken reply. Ask Jenny to send a text copy if you want one. Commands can post their embeds in the text channel, and voice notes always get text replies when addressed to Jenny.

#### Can I stop Jenny from listening in voice?

An administrator in Jenny's voice channel can use `/deafen`. Use `/deafen deafened:False` to resume listening, or `/leavevc` to disconnect.

#### How do I change the language Jenny uses?

Jenny starts in English. An administrator can change the language of chat replies and transcription with `/language`. Her spoken voice is English. [Language and voice replies](#language-and-voice-replies)

#### What can Jenny see in pictures or GIFs?

With vision configured, Jenny can describe supported attachments and sampled frames from supported GIFs. Ask her about an image you attach or reply to the image directly. Links that require a login or fail to load may be unavailable.

### Minecraft and trivia

#### Where do modpack answers come from?

Jenny searches the installed pack data the host has extracted and indexed. The extraction was tested on Minecraft 1.21.1 NeoForge. Other versions and loaders can have gaps. [Modpack setup](tools/rag/README.md)

#### How do I look up something crafted with an item?

Use `/recipe used_item:` for ingredients or `/recipe item:` for outputs. Use `/mods` to list installed mods and `/modcontents` to browse a mod's items, blocks and recipes.

#### Why isn't my mod or recipe showing?

Check that the installed JAR was extracted and the index build completed. Some mods generate recipes at runtime or store them in formats Jenny does not support yet. Report the mod name, version, Minecraft version, loader and a sanitized example. See the [RAG guide](tools/rag/README.md).

#### What does Web search do on a recipe card?

Leave the search box empty to look up the current item, or enter your own query. The results come from external sites and may describe a different pack or Minecraft version.

#### Can I limit trivia to recipes or play solo?

Yes. Set the category, difficulty and mode when you run `/trivia`. Choose **Select categories** to combine specific types of clues. Party games accept answers from multiple players, and `/trivialeaderboard` shows server scores. If the recipe bank is too small, an administrator can add questions with `/generatetrivia`.

### Server tools and commands

#### Can I ask Jenny to run a command in chat?

Yes. Say “Jenny, roll 3 dice” or “Jenny, show my token usage.” Jenny selects the command and its options from the ones available to you. Moderation, ticket setup and host operations use slash commands.

#### Why can't Jenny grant the role I selected?

Jenny needs Manage Roles and her highest role must be above the role she's granting. The person requesting the change also needs the required permissions and role position. [Role hierarchy](#role-hierarchy)

#### How do I pick how long a moderation action lasts?

Enter a period such as `30m`, `2h`, `3d`, or `1w` in `/mute`, `/timeout`, or `/ban`. Use `0` to clear a mute or timeout. Omit the ban period for a permanent ban. [Moderation](#moderation)

#### How do I set up tickets or reaction roles?

Run `/ticketpanel` to create a form with separate fields for each button and a picker for staff roles. Run `/reactionroles` to build buttons people can use to give themselves a role. Both commands require a server administrator. [Panel setup](#tickets-and-reaction-roles)

#### Where can I see which games are free right now?

Run `/freegamesnow` and optionally pick a platform or include DLC and loot. Use `/myaddons` for a private add-on list filtered to your platform roles or a platform you pick. An administrator can use `/freegames` to set up alerts in a channel. [Free-game alerts](#free-game-alerts)

#### Can I edit a panel or copy one to another channel?

Select any button or open **Panel settings** while the draft is open. To start a new draft from a published ticket or reaction-role panel, use `/copyembed` with its message link or ID. [Panel setup](#tickets-and-reaction-roles)

#### Why are some invitees missing from the rankings?

Jenny tracks joins from the time she can read invite usage. A join is counted only when she can identify the invite link. [Activity and invite tracking](#activity-and-invite-tracking)

#### Why can't I install an update using Discord?

Run Jenny under the supervisor from a clean Git clone. The person using `/update install:True` must own both the current server and Jenny's Discord application. ZIP installs can still check for releases. [Restart and updates](#restart-and-updates)

### Providers, privacy and support

#### Do I need all the API keys?

No. A Discord token and one text model key are enough for chat. Add a Groq key for Qwen vision or Whisper, Deepgram for speech output, and Tavily for web lookup. [API key setup](CONFIGURATION.md#credentials)

#### Why does a provider return 429?

The provider reached a quota or rate limit. Wait for it to reset or lower request volume. Multiple keys can share one quota. [Multiple API key support](#multiple-api-key-support)

#### What does Jenny remember about me?

Use `/mymemory` to see your saved personal summary. Server administrators can use `/forget user:` to clear a member's conversation turns and summary for that server. Shared memory, DMs, raw messages, usage, and audit records are kept. [Storage and privacy](CONFIGURATION.md#storage-and-privacy)

#### Where does my data go?

Jenny saves her database and generated Minecraft files on the host. Discord handles the chat and voice traffic. Enabled features can send relevant text, images, audio or search queries to the configured providers. [Memory and data](#memory-and-data)

#### How do I report a bug or request a feature?

Use `/report` when the host has enabled it, or use the repository from `/invite`. Include your Jenny version, the command you ran, and a short example of what happened. Keep credentials and private messages out of public reports. [Support](SUPPORT.md)

## Roadmap

- Broader mod, loader and Minecraft version coverage. The long-term aim is support across all three, with compatibility tested and documented as it grows.
- More Discord features and commands.
- More ways to customize Jenny and the features an installation enables.
- A hosted option, if it turns out to be practical.

Expect more updates as the project grows. Bug reports and suggestions help decide what comes next.

## Project files and development

See the [file-by-file guide](PROJECT.md) for the runtime, extraction tools, setup files, and generated files that stay local. See [CONTRIBUTING.md](CONTRIBUTING.md) for code changes and checks and [CHANGELOG.md](CHANGELOG.md) for release history.

## Support and license

Open a [GitHub issue](https://github.com/MotrynStudios/Jenny-AI-Discord-Bot/issues/new/choose), contact **theinevitableinvincible** on Discord, or email [motrynstudios@gmail.com](mailto:motrynstudios@gmail.com). Bugs, extraction reports, questions, suggestions, collaborations, and hiring inquiries are welcome. See [support](SUPPORT.md) for what to include and [security reporting](SECURITY.md) for private vulnerability reports.

Jenny is released under the [MIT License](LICENSE), copyright Mohamed Ayman Elnashar. Preserve the copyright and license notice when redistributing the code.
