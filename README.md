# Jenny AI Discord Bot

Jenny is a self-hosted Discord AI bot with memory, voice chat, image understanding, and a Minecraft modpack library. She can keep up with a conversation, look up a recipe in your pack, or run a Discord command when you ask in plain English.

Jenny started as a goofy nod to the character from the original Minecraft Jenny Mod. She shares the name, with an adapted personality and character traits for Discord. This is an independent project with no sexual content. Jenny is here to hang out and help you play Minecraft, whether you're figuring out a recipe or exploring a new modpack.

You only need to know how to copy API keys, create a Discord bot, and run a few terminal commands. Start with text chat and add voice, web search, or Minecraft data when you want them.

**Jenny is a young project and is still in development.** The RAG system has been tested with **Minecraft 1.21.1 NeoForge mods**. Other mods, mod loaders and Minecraft versions may work, but they have not all been tested. Some data may be missing or extracted incorrectly. If you find a problem, please [report it](SUPPORT.md).

## Contents

- [What's new in 0.95bf](#whats-new-in-095bf)
- [What Jenny can do](#what-jenny-can-do)
- [Quick start](#quick-start)
- [Create and invite your Discord bot](#create-and-invite-your-discord-bot)
  - [Privileged intents](#privileged-intents)
  - [Installation and permissions](#installation-and-permissions)
  - [Role hierarchy](#role-hierarchy)
- [API keys and free usage](#api-keys-and-free-usage)
- [Configure the environment file](#configure-the-environment-file)
- [Optional voice service](#optional-voice-service)
- [Minecraft modpack data](#minecraft-modpack-data)
- [Commands](#commands)
- [Server tools](#server-tools)
  - [Moderation](#moderation)
  - [Tickets and reaction roles](#tickets-and-reaction-roles)
  - [Free-game alerts](#free-game-alerts)
  - [Restart and updates](#restart-and-updates)
- [Memory and data](#memory-and-data)
- [Resource use](#resource-use)
- [FAQ](#faq)
  - [Do I need all the API keys?](#do-i-need-all-the-api-keys)
  - [Jenny is online, but why doesn't she answer?](#jenny-is-online-but-why-doesnt-she-answer)
  - [Why isn't voice working?](#why-isnt-voice-working)
  - [Why isn't my mod or recipe showing?](#why-isnt-my-mod-or-recipe-showing)
  - [Why does a provider return 429?](#why-does-a-provider-return-429)
  - [Can I change her personality?](#can-i-change-her-personality)
- [Roadmap](#roadmap)
- [Project files and development](#project-files-and-development)
- [Support and license](#support-and-license)

## What's new in 0.95bf

- **Moderation and community tools.** Timed text and voice mutes, Discord timeouts, kicks and bans, private ticket panels, self-assignable role panels, and opt-in free-game alerts. Try `/faq`, `/coinflip`, or `/roll` for quick commands.
- **Voice and search.** `/deafen` pauses listening, other bots are ignored in voice, and live transcription can split clips across several Groq keys. Voice timing reports go to chat. Web search follow-ups cite sources when available.
- **Flexible setup.** Use one key per provider or add any number of numbered keys. Server owners can check for releases in Discord. On a supervised install, the application owner can also restart Jenny or request a verified update from a server they own.

See the [full changelog](CHANGELOG.md#095bf) for details.

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
| Kick Members, Ban Members, Moderate Members | Grant the permissions for `/kick`, `/ban`, and `/timeout` respectively if you plan to use those commands. |

For a private test server you control, **Administrator** is an option for trying the full feature set. It gives the bot every server permission and access to channels regardless of channel overrides. For a regular server, grant the permissions in the table for the features you use. Self-hosting lets you control the process and local files. Discord permissions and external API requests still apply.

Members who use slash commands need **Use Application Commands** in the channel. See [Discord permissions](https://docs.discord.com/developers/topics/permissions) and [command permissions](https://docs.discord.com/developers/docs/interactions/slash-commands#permissions) if commands are missing for specific roles.

### Role hierarchy

In Discord, open **Server Settings → Roles**. Drag Jenny's highest role above every role she should assign or remove, including roles offered in reaction panels and the roles used for mutes. For moderation, Jenny also needs to sit above the target member's highest role. Jenny's role can stay under the owner and staff roles she has no reason to manage.

For example, with **Owner → Jenny → Helpers → Members**, Jenny can grant or remove **Helpers** when the requesting member has the required permissions and rank. A member whose highest role is **Owner** remains above Jenny. **Administrator** does not bypass role order. Discord integration-managed roles and the server owner are also protected. [Discord's role hierarchy](https://docs.discord.com/developers/topics/permissions#permission-hierarchy)

## API keys and free usage

Start with one text provider. Extra keys give Jenny more routes when a provider is unavailable or hits its quota. Add as many extra keys as needed with numbered names such as `GROQ_API_KEY_2` or `TAVILY_API_KEY_3`. Keys from the same account or project can share a quota. Use only accounts and credentials you are authorized to use under each provider's terms.

| Service | What Jenny uses it for | Get a key and check limits |
| --- | --- | --- |
| Groq | GPT-OSS chat, Qwen image analysis, Whisper transcription | Sign into [GroqCloud](https://console.groq.com/keys), create an API key, and paste it into `GROQ_API_KEY_1`. Check your [account limits](https://console.groq.com/settings/limits) and [rate limit guide](https://console.groq.com/docs/rate-limits). The published free table lists GPT-OSS 120B at 30 requests/minute, 1,000/day, 8,000 tokens/minute, and 200,000/day. Whisper also has audio duration quotas. |
| OpenRouter | Optional first chat route | Create a key in [OpenRouter settings](https://openrouter.ai/settings/keys). The example uses [GPT-OSS 120B's free variant](https://openrouter.ai/openai/gpt-oss-120b:free). Free variants do not require buying credits and are normally capped at 20 requests/minute and 50/day without a qualifying credit purchase. See the [free model guide](https://openrouter.ai/collections/free-models). |
| Google Gemini | Optional chat, vision, trivia and memory fallback | Create a key in [Google AI Studio](https://aistudio.google.com/apikey) and verify the project plan. [Model pricing](https://ai.google.dev/gemini-api/docs/pricing) shows which models have free API access. [Limits](https://ai.google.dev/gemini-api/docs/rate-limits) vary by model and project. Gemini's [terms](https://ai.google.dev/gemini-api/terms) have age, audience and regional rules. Leave it disabled if those rules do not fit your server. |
| Tavily | Optional web lookup | Sign into the [Tavily dashboard](https://app.tavily.com/) and copy a key to `TAVILY_API_KEY_1`. Tavily documents 1,000 free credits a month without a card. Jenny requests basic search, which uses one credit per call. See [API credits](https://docs.tavily.com/documentation/api-credits). |
| Deepgram | Optional spoken responses | Create a project and API key in the [Deepgram console](https://console.deepgram.com/). Deepgram documents a [$200 starter credit](https://developers.deepgram.com/guides/fundamentals/make-your-first-api-request). Check [pricing](https://deepgram.com/pricing) and your project's [Flux TTS](https://developers.deepgram.com/docs/flux-tts/voices) access. Credit can run out. |

You can run basic Jenny chat on a supported free tier without linking a credit card. Keep paid billing off and use the exact free model ID where a provider has separate paid and free routes. Check the linked dashboards for current limits and model availability. Optional voice uses Deepgram credits. You can skip a service if its terms or signup requirements do not work for you.

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

## Minecraft modpack data

Put your mod JARs in `mods/` and instance config files in `config/`, or pass their paths to the builder. Then run:

~~~powershell
.\.venv\Scripts\python.exe tools\rag\build_index.py --skip-trivia
~~~

This extracts files, creates searchable documents, and builds a local vector index. Run without `--skip-trivia` to also build the trivia bank, or use `--trivia-only` after an existing extraction. Trivia generation uses your model quota. It aims for 100 questions each for descriptions, recipes, and items/blocks, subject to usable local evidence and provider availability. Read the [RAG guide](tools/rag/README.md) for supported formats, flags, and troubleshooting. Restart Jenny after refreshing the index.

Mixed trivia gives roughly half its questions to recipes when enough are available. A server administrator can run `/generatetrivia count:100 category:recipes difficulty:medium` to add questions to the installation's shared bank. Results may be lower than requested when there are too few unused clues or the model providers cannot finish.

Jenny searches extracted data for installed pack facts. Recipe cards use source patterns for grids and list the materials and bench for supported custom recipes such as TaCZ. The **Web search** button accepts a query or uses the current result if left blank. Web results are external references and may describe a different game version or pack.

## Commands

Use `/help` to see commands you can run in the current context. You can also ask Jenny to run a supported command in chat, DMs, or voice. Admin and role checks still apply. Moderation, panel setup, free-game configuration, and host maintenance use slash commands.

| Area | Commands |
| --- | --- |
| Setup, health and feedback | `/help`, `/health`, `/invite`, `/report`, `/setmainchannel`, `/getmainchannel`, `/setlogschannel`, `/getlogschannel`, `/togglejenny`, `/models`, `/setmodel`, `/changelog` |
| Minecraft | `/mods`, `/modcontents`, `/recipe`, `/searchpack`, `/trivia`, `/trivialeaderboard`, `/generatetrivia` (admin) |
| Voice | `/joinvc`, `/leavevc`, `/voicestatus`, `/replymode`, `/voicetimes`, `/deafen` |
| Server tools | `/mute`, `/ban`, `/kick`, `/timeout`, `/ticketpanel`, `/reactionroles`, `/freegames`, `/restart`, `/update` |
| Quick commands | `/faq`, `/coinflip`, `/roll` |
| People and roles | `/serverinfo`, `/user`, `/profile`, `/avatar`, `/roles`, `/profilehistory`, `/recentchanges`, `/finduser`, `/lastseen`, `/assignrole`, `/removerole`, `/refreshprofiles` |
| Memory and usage | `/searchchat`, `/summarize`, `/mymemory`, `/forgetmydata`, `/forgetme`, `/myusage`, `/tokenusage` |

`/recipe item:` searches outputs and `used_item:` searches ingredients. `/mods mod:` filters the roster and `/modcontents mod:` browses one mod by category. When a result spans multiple pages, use **Back**, **Next**, or the page number to navigate.


## Server tools

### Moderation

Use `/mute member: minutes: mode:` to temporarily restrict text, voice or both. Jenny creates a role for the selected mode on first use. **Text** blocks posting and starting threads. **Voice** blocks speaking in voice and sending voice recordings. **Both** applies both sets of restrictions. Where posting is blocked, members also cannot upload voice recordings in that channel. Use zero minutes to remove Jenny's active mute early. Expiration survives a restart. The requester needs **Manage Roles** and **Moderate Members**. Jenny needs **Manage Roles** in the affected channels and must be above the member and mute roles.

`/timeout` uses Discord's built-in timeout for up to 28 days. Use zero minutes to clear it. `/kick` removes a current member. `/ban` prevents a current member from returning and keeps their message history. Discord permissions and both role hierarchies apply. These commands are available as slash commands.

### Tickets and reaction roles

Run `/ticketpanel category:` in the channel where the panel should appear. In the modal, set a title, a description, a hex embed color such as `#5865F2`, and a JSON list with one object per button. The list can contain up to 25 buttons. Turn on Discord Developer Mode to copy role IDs. Replace the example IDs with roles from your server. Use `"roles":[]` if a ticket type should have no staff role.

```json
[
  {"label":"Help","emoji":"🎫","roles":["123456789012345678"],"message":"What can we help you with?"},
  {"label":"Report a bug","emoji":"🐛","roles":["234567890123456789"],"message":"What happened, and how can we reproduce it?"}
]
```

Each button creates a private text channel in the selected category for the member, Jenny, and the configured staff roles. Discord administrators retain access. Each member can have one open ticket per button. The welcome post pings the configured roles if Discord allows Jenny to mention them. Use mentionable roles or give Jenny **Mention Everyone** specifically where those pings are needed. **Close ticket** hides the channel from the opener and preserves it for staff. Staff can delete closed channels when their retention policy allows it. Jenny keeps ticket-channel messages out of automated chat memory and AI prompts.

Run `/reactionroles title:` in the channel where the panel should appear. Pick a role, choose blue, gray, green or red, and pick an optional icon from the included options or server emojis. Select **Add button** for each role, then **Publish** when ready.

Click a shared button to add or remove its role. Discord uses the same color for a public button for everyone. The private confirmation provides a red **Remove role** button for the member who clicked. Jenny must be above the roles and have **Manage Roles**. Staff permissions make a role ineligible. Both kinds of panel survive a bot restart.

### Free-game alerts

Run `/freegames` with a destination channel and platform, and opt in to DLC and loot if wanted. Run it with `enabled:False` to stop alerts. Jenny polls [GamerPower](https://www.gamerpower.com/api-read) every 30 minutes without a key, and remembers the offers it has already posted in that channel. The embed links to the claim page and the original listing. It includes the description, the listed expiry and, when the claim points to a verified Steam app, [Steam user reviews](https://partner.steamgames.com/doc/webapi/IUserReviewsService). Availability varies by region and store.

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

On the maintainer's setup, idle or light use was roughly **80 MB RAM for Python** and **40 MB for Node.js**. Loading the local RAG model and index brought Python to roughly **500 MB**. The extra memory holds the embedding model, search index and retrieved data in the process, so Jenny can reuse them between requests. Idle CPU use is usually low. Extraction, indexing, local embeddings, and active voice can use more CPU and RAM. These are approximate observations and vary with the machine, library versions, server count and pack size. Leave `RAG_PREWARM=false` to defer RAG loading until needed.

## FAQ

### Do I need all the API keys?

No. A Discord token and one text model key are enough for chat. Add a Groq key for Qwen vision or Whisper, Deepgram for speech output, and Tavily for web lookup. The [configuration reference](CONFIGURATION.md) lists every setting.

### Jenny is online, but why doesn't she answer?

In a server, mention Jenny, say her name, or reply directly to one of her messages. Check Message Content Intent, channel access, `/togglejenny`, `/health`, and the latest lines in `logs/python-runtime.log`. If slash commands are missing, check the install scopes and server command permissions.

### Why isn't voice working?

Run the Node service as well as Python. Confirm `/health` shows it online, join a voice channel, then run `/joinvc`. Check Connect and Speak permissions, provider access, and FFmpeg on `PATH`. A failed join reports an error and stops. See `logs/voice-runtime.log` for connection, STT and TTS details.

### Why isn't my mod or recipe showing?

Check that the installed JAR was extracted and the index build completed. Some mods generate recipes at runtime or store them in formats Jenny does not support yet. Report the mod name, version, Minecraft version, loader and a sanitized example. See the [RAG guide](tools/rag/README.md).

### Why does a provider return 429?

Its quota or rate limit has been reached. Jenny can try another configured key or provider. Wait for the limit to reset or reduce traffic. A second key tied to the same quota usually cannot increase it.

### Can I change her personality?

Yes. Edit [`Jenny/Prompts.py`](Jenny/Prompts.py) and restart Python. Keep API keys and personal data out of prompts and commits.

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
