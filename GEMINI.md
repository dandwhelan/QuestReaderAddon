# SpeakStone Narration (QuestReaderAddon) — Gemini Agent Guidelines

A World of Warcraft addon that plays AI-generated voiceovers for quest text, books/items, and gossip, cloned from each NPC's in-game dialogue. The regeneration pipeline resides under `tools/`.

## Architecture & Layout

- **Core Addon:** `QuestReaderAddon.lua`, `QuestReaderAddon_Settings.lua`, `QuestAudioLibraryUI.lua`, `QuestReaderAddon.toc`
- **Audio Index:** `SoundLengths.lua` — the index the addon reads before playing any audio file. A clip missing from this file cannot be played even if the file exists on disk. **This file is real user data, not a build artifact.**
- **Audio Assets:** `Sounds/` — library containing 5,000+ files (`.ogg` and legacy `.wav`/MP3 wrapper).
- **Tooling & TTS Pipeline:** `tools/` (see `tools/README.md` for full reference and pipeline details).
- **Harvester Addon:** `tools/QuestReaderHarvester/` — companion addon (SpeakStone Harvester) that captures live quest, gossip, and item/book text from the WoW client.

## Critical Safety Rules

1. **Never run `build_soundlengths.py -o` against `SoundLengths.lua` without a backup first.** Always verify after rebuilding that `new_count >= old_count + new_clips`.
2. **Never delete audio files from `Sounds/` as part of cleanup.** Deletion of real shipped audio is only done after in-game playback confirmation.
3. **Cache files under `tools/.cache/` are safe to delete/rebuild;** files in `Sounds/`, `SoundLengths.lua`, or live WoW installs are not.
4. **Live WoW Addon Sync:** When modifying files and syncing to `C:\Program Files (x86)\World of Warcraft\_retail_\Interface\AddOns\QuestReaderAddon` or `QuestReaderHarvester`, **always create a timestamped backup first** (e.g. `.backup_<timestamp>`).

## Audio Keying & Naming Conventions

- **Quests:** `<questID>_<passage>.<ext>` (`description`, `progress`, `completion`)
- **Gossip:** `npc<npcID>_gossip<n>.<ext>` (e.g., `npc12345_gossip1.ogg`)
- **Items & Books:** `item<itemID>_page<page>.<ext>` or `item_<clean_name>_page<page>.<ext>` or `<clean_name>_page<page>.<ext>`
- Formats supported: `.ogg` (preferred/new), `.wav`.

## User Interface & Minimap Controls

- **Minimap Button (LibDBIcon / DataBroker):**
  - **Left-Click:** Open Settings panel (`/ss`, `/speakstone`, `/qr`, `/questreader`).
  - **Right-Click:** Export harvested data window (`/ssharvest export`, `/qrharvest export`).
  - **Middle-Click:** Toggle chat debug messages on/off (`/ssdebug`, `/qrdebug`).
- **Slash Commands:**
  - `/ss`, `/speakstone`, `/qr`, `/questreader`: Open Settings UI.
  - `/ssauto`, `/speakstoneauto`, `/qrauto [on|off]`: Toggle quest audio autoplay.
  - `/ssdebug`, `/speakstonedebug`, `/qrdebug [on|off]`: Toggle debug messages in chat.
  - `/ssmissing`, `/speakstonemissing`, `/qrmissing`: Open missing quest audio session report.
  - `/qrlibrary`: Open Audio Library UI player.
  - `/qrtoggle`: Toggle minimap button visibility.
  - `/ssharvest`, `/speakstoneharvest`, `/qrharvest [export|wipe|clear]`: Manage harvester data.
- **External UI Hooking:**
  - DialogueUI (`DUIBookFrame`) is hooked via `ScrollToPage` and `ScrollTo` for multi-page book reading.

## TTS Engine & Pipeline Rules

- **TTS Engine:** F5-TTS is the default engine (`.venv-f5/`), not XTTS (`.venv/`).
- **Reference Audio Budget:** 60 seconds of reference audio per NPC.
- **wago.tools:** Always send a custom `User-Agent` header to avoid HTTP 403.
- **SavedVariables Parsing:** Lua array-style tables parse as Python dicts keyed by numeric strings (`"1"`, `"2"`, etc.) — handle with `isinstance(x, dict)` and numeric key sorting.
- **Pronunciations:** Add custom pronunciations to `tools/pronunciations.json` only after verifying against in-game voiceovers.
