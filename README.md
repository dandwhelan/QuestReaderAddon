# WoW - Quest Reader (SpeakStone Narration)

World of Warcraft addon that reads quest text aloud using AI voices cloned from
each NPC's own in-game dialogue. Every quest description, progress and
completion line has a generated audio file, synthesized from a clone of the
voice that quest giver actually uses in the game. Books, item text and gossip
get the same treatment where audio exists.

Currently voices **The War Within** and **Midnight** (12.0–12.1) content most
heavily. Coverage of other expansions is real but partial and growing — see
"Per-expansion companion packs" below for the actual numbers, which are far
from complete for anything before Dragonflight.

This is a working hobby tool with real rough edges: most of the library is
still unmapped to an expansion, gossip audio is thin because it can't be
scraped, and generation for the whole game would take on the order of months
of GPU time. It's actively being built out, not a finished product.

## Installation

If downloading directly, grab the latest release from
[the Releases page](https://github.com/clhammer89/wow-questreader/releases)
and unzip the whole folder into your WoW AddOns directory, generally
`C:\Program Files (x86)\World of Warcraft\_retail_\Interface\AddOns`. If you
want expansion coverage beyond the base addon's bundled library, also install
the matching `QuestReaderAddon_Pack_<Expansion>` companion addon(s) — see
below for why those are separate.

This adds a "Read Quest" button to the bottom of the quest frame, and reads
quest text automatically as you progress through it. Addon options let you
toggle the minimap button and auto-read.

If you use a quest UI addon like Immersion, make sure auto-play is turned on
in Quest Reader's own settings — Immersion's custom frame doesn't trigger the
addon's normal hooks otherwise.

## Why the addon is split into a base addon plus per-expansion packs

The base addon ships with its own bundled `Sounds/` library and
`SoundLengths.lua` index. As coverage grows toward the whole game, that
library would eventually blow past two hard ceilings: CurseForge rejects any
single project file over 500 MB, and a git repository carrying gigabytes of
binary audio in its history becomes unworkable to clone, fork or host (this
project's own repo history briefly reached several GB before being rewritten
to control it).

The fix is to split newer/less-common audio into separate companion addons —
`QuestReaderAddon_Pack_<Expansion>` — each with its own small `Sounds/`
folder, its own `SoundLengths.lua` (under its own global variable name, e.g.
`QuestReaderSoundLengths_Pack_Cataclysm`), and a `Register.lua` that hands its
index to the base addon at load time via
`QuestReaderAddon_RegisterSoundPack(packName, soundLengths)`. The base addon
merges every registered pack's index into one lookup table
(`addon.soundSources`) and, when it's time to play a clip, checks every
registered source for a match (preferring `.ogg` over `.wav`) — the player
never sees a seam between "bundled" and "pack" audio, they just get a missing
line or a spoken one.

Quest audio that hasn't yet been mapped to a specific expansion lands in an
`Unsorted` pack rather than being dropped or guessed at — mapping quest IDs to
expansions turns out to be a real, still-partial research problem (see the
walkthrough for why). As of the last pack build, the large majority of
already-generated audio is still sitting in `Unsorted`.

## Commands

| Command | Does |
| --- | --- |
| `/qrtoggle` | Toggle the minimap button |
| `/qrauto` | Toggle auto-read on/off |
| `/qrmissing` | List quest IDs you've encountered with no audio |

## Status

- English only for now. Other languages are a future goal, not started.
- Gossip (the greeting text from NPCs who give no quest) is **partially
  supported**: the addon can play it back, but almost none has been generated
  yet, because — unlike quest text — it isn't recoverable from any external
  source. See the technical section below for why, and how to help capture it.
- Audio ships as Ogg Vorbis where available, which the game's own dialogue
  also uses, falling back to the original WAV library where a quest hasn't
  been regenerated yet.
- Missing quests happen. If you see "no audio for quest `<ID>`" in chat,
  that quest hasn't been voiced yet — `/qrmissing` lists everything you've
  hit locally. Feel free to open an issue with the ID.

## Bugs

If you receive "Quest sound file not found" or "Failed to play audio", the
quest is missing its audio, not broken — see Status above.

## Shout-outs and thanks

- Curseforge user Paratusjv for the stop functionality and quest-UI-addon
  support that shipped in an earlier version.
- Everyone who has provided feedback and support — thank you HAQ!

## Links

- Project on Curse: https://curseforge.com/wow/addons/wow-questreader
- Project on Wago: https://addons.wago.io/addons/wow-questreader
- Project on Github: https://github.com/clhammer89/wow-questreader
- Donate: https://www.paypal.com/donate/?hosted_button_id=54FT9M5AAV9KG

---

## Technical deep dive: how the voiceovers actually get made

This section is for anyone who wants to know what's really going on under
the hood, or wants to help regenerate audio for a new patch. Everything
described here lives in `tools/`, which has its own more detailed README.

### The core problem: quest text isn't in the game files

You'd expect quest description/progress/completion text to be sitting in a
client database table somewhere, minable like almost everything else in WoW.
It isn't. The `QuestV2` table carries only an ID, a bit flag, and a UI theme
ID; `QuestObjective` carries short one-line objective strings, not the
narrative text. The actual prose is delivered by the server at runtime and
exists nowhere in the files Blizzard ships.

So getting the text at all means one of two things: reading it live off a
running client, or getting it from somewhere that already collected it from
players. Both are used here — a companion in-game addon
(`QuestReaderHarvester`) for authoritative live capture, and a Wowhead
scraper for bulk coverage of content Wowhead has already indexed.

Gossip text — the greeting an NPC gives when clicked, whether or not they
offer a quest — has the exact same problem, confirmed directly against the
client's actual data definitions for the current build: there is no
gossip-text table either. The nearest thing, `GossipNPCOption`, only carries
structural flags (vendor links, trainer links, LFG queue links), no text
field at all. So gossip audio can *only* come from the harvester; there is no
Wowhead-equivalent shortcut for it. If you want to help, enable
`QuestReaderHarvester` and just play the game — every NPC gossip window you
open gets captured.

### The voice side: NPC audio is fully minable

Unlike quest text, every line of NPC speech in the game is a real file on
Blizzard's CDN, and a community-maintained listfile maps file IDs to their
paths. Creature dialogue lives at predictable paths —
`sound/creature/<npc>/vo_<patch>_<npc>_<nn>.ogg` — so it can be indexed and
searched without touching the game client at all.

Coverage varies a lot by NPC. Named story characters are richly recorded —
Alleria Windrunner alone has over a thousand clips — while many minor quest
givers have only a handful, and about a fifth have none at all. Those get a
stand-in voice instead of their own (see "Filling the gaps" below).

### Turning text + reference audio into a voice clone

The engine is **F5-TTS**, a flow-matching voice cloning model, chosen over
the more commonly used XTTS-v2 after a direct, measured comparison: cloning
the same NPC from identical reference audio, F5's output matched the
reference voice's own frequency balance far more closely — especially in the
800 Hz–2.5 kHz band where speech articulation and emphasis actually live,
where XTTS scooped that band while adding extra bass and treble that reads
as "boomy" and "vague." A listening test agreed with the measurement.

A few things about how a clone is built matter more than they look:

- **How much reference audio to use is a real trade-off, not "more is
  better."** Handing the model everything available makes it average across
  all of it, which flattens delivery. The pipeline defaults to 60 seconds of
  reference audio per voice, chosen by direct A/B listening test — earlier
  defaults used far less on the theory that more reference audio would hurt
  more than it helped, which turned out to be wrong for how closely the
  clone should resemble a specific, recognisable NPC.
- **Which clips get used, not just how many.** Reference clips are ranked to
  prefer ordinary connected dialogue over combat barks and reaction lines —
  though this only works where filenames describe their contents, and modern
  voice-over files (`vo_121_taklejo_7758051.ogg`) carry no such information,
  so for recent content this reduces to picking clips of a sensible length.
- **Loudness is matched to the game, not guessed.** Generated audio is
  normalised to -16 LUFS, measured directly against the game's own dialogue
  (which sits at about -16.1 LUFS) rather than picked arbitrarily.
- **Pronunciation of in-universe names is a real, recurring problem.** No
  model trained on English speech will pronounce "Zul'Aman" correctly on the
  first try. `tools/pronunciations.json` is a small, deliberately
  hand-curated lexicon of respellings for names the model gets wrong,
  applied to text before it reaches the engine. It only grows by listening —
  never by guessing in advance. The current fix is the smallest one that
  works: strip the apostrophe so the name reads as one word
  (`"Zul'Aman"` → `"Zulaman"`). An earlier, more elaborate approach spelled
  names out phonetically with hyphens (`"Zool-Ah-Mahn"`); it was tried and
  rejected on listening — every hyphen invites a small pause, so a
  three-hyphen name comes out as separate stressed pieces instead of one
  word, and it measurably lengthened clips too. The lookup itself has to
  match the *longest* candidate name first (`"Amani'Zar"` before `"Amani"`),
  or a short name that happens to be a prefix of a longer one gets mangled.

### Filling the gaps: NPCs with no voice of their own

Roughly a fifth of NPCs have no recorded speech at all — often generic,
unnamed characters. Rather than pick a voice at random (which produces
things like a dwarf sounding like a tree spirit), those NPCs are grouped by
race and sex, and each group is assigned one donor voice: preferably one of
the ~160 anonymous "generic" voice pools the game itself ships for exactly
this purpose (`generic_blood_elf_citizen_a` and similar), falling back to
the best-recorded named NPC of a matching race and sex where no pool exists.
A handful of groups still have no good match at all — those get an explicit,
deliberate choice, documented as such, rather than a silent guess.

### Keeping the download size sane

The original shipped library looks bigger than it is. Of the 5,238 audio
files, 71% are already MP3 compressed audio wearing a `.wav` file extension
— the game reads file contents rather than trusting the name, so they've
always played correctly, and re-encoding them again would just be a second
lossy pass for no real benefit. Only the remaining 29% is genuine
uncompressed PCM, and that slice — and only that slice — gets re-encoded to
Ogg Vorbis, at a quality setting matched to what the game's own dialogue
already uses. That one change alone removes roughly 340 MB from the
download without touching a single file that didn't need it.

### The pipeline, end to end

For anyone who wants to actually run this:

```
tools/missing_quests.py      Diff the client's quest table against
                              SoundLengths.lua -> quest IDs with no audio

tools/wowhead_quests.py      Fetch quest text + speaker for those IDs
tools/QuestReaderHarvester   ...or capture it live, in-game, instead
tools/harvest_export.py      Convert the harvester's capture into the
                              same format the Wowhead fetch produces

tools/voice_sources.py       Find which NPCs have usable reference audio
tools/npc_traits.py          Resolve an NPC's race/sex from client data
tools/voice_bank.py          Assign a stand-in voice to NPCs with none

tools/synthesize.py          Generate the actual audio (F5-TTS by default)
tools/build_soundlengths.py  Rebuild the index the addon reads
tools/convert_library.py     Shrink the genuine-PCM slice of the library
tools/build_sound_packs.py   Split the library into per-expansion packs
tools/publish_packs.py       Push each pack to its own repo
```

`tools/run_batch.py` wraps most of this into one command for routine use —
fetch new quest text from the companion website, synthesize, install, rebuild
the index, and optionally tell the website what's now voiced — and
`tools/wake_when_work.py` can wake a sleeping GPU machine over LAN when work
shows up. See `docs/walkthrough.md` for the actual step-by-step operator
process, including the safety gates around rebuilding `SoundLengths.lua`.

`tools/README.md` covers all of this in far more depth, including exact
commands, what each generation setting actually does and why, and the
Windows-specific installation quirks (PyTorch version pins, a required
`torchcodec` removal, and similar) that took real trial and error to work
out.
