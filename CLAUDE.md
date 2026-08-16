# QuestReaderAddon

A WoW addon that plays AI-generated voiceovers for quest text, cloned from
each NPC's own in-game dialogue. `tools/` holds the regeneration pipeline
that produces that audio. Read `tools/README.md` before touching the
pipeline — it's the authoritative, detailed reference; this file is
orientation and safety rules, not a duplicate of it.

## Layout

- `QuestReaderAddon.lua`, `QuestReaderAddon_Settings.lua`,
  `QuestAudioLibraryUI.lua`, `.toc` — the addon itself.
- `SoundLengths.lua` — the index the addon reads before playing anything. A
  clip missing from this file is invisible to the addon even if the audio
  file exists on disk. **This file is real user data, not a build artifact.**
- `Sounds/` — the shipped audio library, currently 5,000+ files. Most
  filenames end `.wav` but a majority of those are actually MP3 with a `.wav`
  extension (see below) — never assume the extension tells you the format.
- `tools/` — the regeneration pipeline. Python 3, stdlib-only except where a
  script says otherwise. Has its own README with full usage.
- `tools/QuestReaderHarvester/` — a small companion addon that captures
  quest and gossip text live from a running client, for content that can't
  be scraped.

## Non-negotiable safety rules

- **Never run `build_soundlengths.py -o` pointed at the real
  `SoundLengths.lua` without a backup first**, and always verify the entry
  count after: it should equal old-count + new-clips, never less. A rebuild
  that silently drops entries is worse than no rebuild.
- **`Sounds/` originals are not deleted automatically**, even after a
  successful re-encode to Ogg. Deleting real shipped audio is the user's
  call after they've confirmed playback in-game — never do it as part of a
  "cleanup."
- Anything under `tools/.cache/` is a rebuildable cache, safe to delete.
  Anything in `Sounds/`, `SoundLengths.lua`, or a user's live AddOns install
  is not.
- If syncing changes into a live WoW install
  (`.../Interface/AddOns/QuestReaderAddon`), back up whatever's already
  there first — it may be a version the user is actively testing.

## What's already been solved (don't rediscover these)

- **wago.tools returns HTTP 403 to urllib's default User-Agent.** Every
  script that hits it (`missing_quests.py`, `npc_traits.py`,
  `voice_sources.py`) sends a real one. If you add a new client-table fetch,
  do the same.
- **The original `TTS` (Coqui) PyPI package is archived and won't install on
  Python 3.12+.** Use the `coqui-tts` fork, which provides the same `TTS`
  module.
- **torch ≥2.9 requires `torchcodec`, which needs FFmpeg *shared* libraries.**
  The usual Windows ffmpeg builds are static-only, so torchcodec fails to
  load with a cryptic `WinError 127` — after the model has already loaded,
  which makes it look like a model problem, not a codec one. Fix: uninstall
  `torchcodec` (F5-TTS) or pin `torch==2.8.*` (XTTS). Both engines fall back
  to torchaudio cleanly once torchcodec is out of the picture.
- **The two TTS engines don't share a venv well.** `.venv/` = XTTS,
  `.venv-f5/` = F5-TTS (the default). Both are gitignored.
- **F5-TTS is the default engine, not XTTS**, chosen after a direct measured
  comparison (see `tools/README.md` and `tools/synthesize.py`'s module
  constants for the numbers). Don't silently switch it back.
- **Reference budget is 60 seconds, not the "less is more" value a lot of
  cloning advice suggests.** That was deliberately tested at 12/30/60s by
  ear against real NPCs and 60 won. This only matters for NPCs with enough
  reference clips to hit the budget in the first place.
- **The Lua SavedVariables parser turns array-style Lua tables (`{"a","b"}`)
  into a Python dict keyed `"1","2",...`, not a list.** Code that reads
  harvester output learned this the hard way — check `isinstance(x, dict)`
  and sort numeric keys, not `isinstance(x, list)`.
- **Quest text and gossip text are both absent from client data files.**
  Verified directly against the actual DB2 definitions for the current
  build, not assumed. Quest text has a Wowhead-scraping fallback; gossip
  text does not — it can only come from the harvester addon being played
  with. Don't go looking for a gossip DB2 table; there isn't one.
- **Wowhead rate-limits.** `wowhead_quests.py` retries 403/429 with
  exponential backoff and writes whatever it gathered if the block outlasts
  that, rather than losing a long run's progress. Don't run two fetches
  against the cache at once — it roughly halves the effective pacing and is
  the likely cause of an early rate-limit incident.
- **The naming scheme for indexed audio is `<questID>_<passage>.<ext>` for
  quests and `npc<ID>_gossip<n>.<ext>` for gossip** — deliberately prefixed
  so the two numeric spaces (quest IDs, creature IDs) can never collide on
  one key. `build_soundlengths.py`'s filename pattern accepts both.
- **71% of the shipped `.wav` library is actually MP3 in a `.wav`
  wrapper.** Only the genuine-PCM slice (29%) should ever be re-encoded —
  `convert_library.py` checks the `fmt` chunk's format tag, not just the
  RIFF/WAVE header, before touching a file. Re-encoding an already-lossy
  file is a real quality cost for little to no size benefit.

## Conventions

- Pronunciation fixes go in `tools/pronunciations.json`, added only after
  actually hearing a name mispronounced in generated audio and checking the
  respelling against the game's own voice-over — never guessed in advance.
- Voice bank donor choices for NPCs with no audio of their own are a
  judgement call, not something to leave silently unfilled. Document the
  reasoning in the commit when picking one.
- Commit messages in this repo explain *why*, generally at some length —
  match that style rather than terse one-liners, especially for anything
  fixing a bug that wasn't obvious from the symptom.
