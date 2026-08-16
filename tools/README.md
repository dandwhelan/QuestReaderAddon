# Audio generation tooling

Tooling for regenerating the addon's quest voiceovers. It gathers the two
inputs synthesis needs — the text to speak, and reference audio for the voice
that speaks it — and does not generate audio itself yet.

## Start here

Run in this order. Each step's output is the next step's input; nothing has to
exist beforehand.

```sh
# 1. Which quests have no audio?  ->  missing.txt
python3 missing_quests.py -o missing.txt

# 2. What do they say, and who says it?  ->  passages.json
python3 wowhead_quests.py --ids missing.txt -o passages.json

# 3. Which NPCs need a cloned voice?  ->  npcs.txt
python3 -c "import json;print('\n'.join(sorted({p['npcName'] for p in json.load(open('passages.json'))['passages'] if p['npcName']})))" > npcs.txt

# 4. Do those NPCs have usable reference audio?
python3 voice_sources.py report npcs.txt

# 5. Which audio files to extract  ->  ids.txt
python3 voice_sources.py manifest npcs.txt --per-npc 8 -o ids.txt

#    ... extract ids.txt with wow.export into ./reference-audio ...

# 6. Plan the generation, then run it  ->  ./generated
python3 synthesize.py passages.json --reference ./reference-audio --dry-run
python3 synthesize.py passages.json --reference ./reference-audio -o ./generated

# 7. Rebuild the index the addon reads
python3 build_soundlengths.py ./generated -o ../SoundLengths.lua
```

Then extract the audio listed in `ids.txt` with wow.export (see *Extracting the
audio* below), and you have matched text-and-voice pairs ready for synthesis.

On Windows use `py.exe` in place of `python3`.

Step 2 can be replaced by the harvester addon if you would rather read text from
a live client than from Wowhead — see *Harvesting quest text*. The two produce
the same records.

### What each file is

| File | Produced by | Contains |
| --- | --- | --- |
| `missing.txt` | step 1 | quest IDs with no audio, one per line |
| `passages.json` | step 2 | text and speaker for each quest passage |
| `npcs.txt` | step 3 | the NPC names to clone |
| `ids.txt` | step 5 | FileDataIDs to extract in wow.export |
| `./generated` | step 6 | the finished `<questID>_<passage>.ogg` clips |
| `SoundLengths.lua` | step 7 | the index the addon looks up before playing |

## Why this exists

Quest audio is keyed by quest ID alone (`<questID>_<type>.wav`), so the shipped
addon records nothing about which NPC speaks which line. The original
NPC-to-voice mapping was never published and cannot be recovered from the addon,
so it has to be rebuilt. This tool rebuilds the half that is recoverable from
game data.

## What is and is not available

The two halves of the pipeline have very different constraints:

| Data | Source | Available? |
| --- | --- | --- |
| NPC speech audio | Game client / CDN | Yes — 138,086 files across 3,678 NPCs |
| Quest → NPC mapping | Game client at runtime | Yes — via the addon API |
| Quest description / progress / completion text | Server, at runtime | **No — not in client files** |

Quest text is not datamineable. `QuestV2` carries only `ID`, `UniqueBitFlag` and
`UiQuestDetailsThemeID`; `QuestObjective.Description_lang` holds short objective
strings, not narrative. There is no quest description table. The text has to be
captured from a running client or sourced from a site that collects it from
players.

## Reference audio availability

Of the 3,678 NPCs with recorded speech:

| Clips | NPCs | Suitability |
| ---: | ---: | --- |
| 50+ | 790 | Ample — enough to fine-tune |
| 20–49 | 1,098 | Strong zero-shot cloning |
| 10–19 | 588 | Good |
| 5–9 | 470 | Workable |
| 2–4 | 437 | Marginal |
| 1 | 295 | Needs a fallback voice |

About 80% clear the five-clip threshold. Named story NPCs are richly resourced
— Alleria Windrunner has 1,017 clips, Xal'atath 531 — so the campaign content
that makes up recent patches is well covered. The thin end of the distribution
is generic unnamed NPCs, which need a fallback voice bank keyed by race and
gender.

## Usage

```sh
# Build the index. Downloads the ~150 MB community listfile once and caches it.
# Optional — any command below builds the index if it is missing.
python3 voice_sources.py index

# What speech exists for an NPC?
python3 voice_sources.py lookup "Alleria Windrunner"

# Coverage and FileDataIDs for the NPCs voiced in a patch, in one step.
python3 voice_sources.py report   --patch 120 1200 1205 1207 121
python3 voice_sources.py manifest --patch 120 1200 1205 1207 121 -o ids.txt

# Or work from a curated list instead, one name per line.
python3 voice_sources.py patch 120 1200 1205 1207 121 -o npcs.txt
python3 voice_sources.py report npcs.txt
python3 voice_sources.py manifest npcs.txt -o ids.txt
```

On Windows use `py.exe voice_sources.py ...`.

### Finding the NPCs for a patch

Voice files are named `vo_<patch>_<npc>_<nn>.ogg`, so the patch a line was
recorded for is readable straight off the filename. That narrows 3,678 voiced
NPCs to the few hundred involved in recent content without needing quest data
first, which is useful because the quest-to-NPC mapping is the slowest thing to
assemble.

The prefixes are not zero-padded consistently — 12.1 appears as `121` while
12.0.7 appears as `1207` — so patches are matched as literal strings, not
numbers. Midnight to date is `120 1200 1205 1207 121`, which covers 314 NPCs
and 8,063 files; 76% of those NPCs clear the five-clip threshold.

This list is a strong proxy for the quest givers in recent content, but it is
not the same thing: it includes NPCs voiced for cinematics and ambient dialogue
who give no quests, and it will miss any quest giver who is unvoiced. Treat it
as a starting point to validate against, not the final mapping.

Names are matched on whole tokens, so titles and epithets resolve in either
direction — `Xal'atath` finds `xalatath_blade_of_the_black_empire`. Ambiguous
names are reported rather than guessed at.

Python 3 standard library only; no dependencies.

## Extracting the audio

### How many files do you actually need?

Far fewer than the full set. Zero-shot cloning needs only a handful of clean
clips per voice, so `--per-npc 8` across every Midnight NPC is about 2,000
files — not the ~19,000 that pulling everything produces. Raising `--per-npc`
mostly adds extraction time and disk for no gain in clone quality.

### In wow.export, filter — do not paste IDs

Pasting thousands of IDs one at a time is not the intended workflow. Because
the filenames encode both the NPC and the patch, a single search does the same
job:

| Goal | Search |
| --- | --- |
| One NPC | `zuljarra` |
| Everything from patch 12.1 | `vo_121_` |
| Everything from 12.0 | `vo_120_` |

Then select all the results and export once.

1. Install [wow.export](https://github.com/Kruithne/wow.export). It reads from a
   local game install *or* streams from Blizzard's public CDN, so a full client
   install is not strictly required.
2. Point it at your source and let it load the build.
3. Open the **Sounds** tab, type the search above, select all, export.

Files arrive as `.ogg` — the format the game stores natively — so no transcoding
is needed before they are used as cloning references.

### Selecting the results

The audio tab has no way to import a list of IDs, so the search box is the
selection mechanism. Filter, then select the whole result set — click the first
row and Shift+Click the last, or Ctrl+A with the list focused — and press
**Export Selected**. The counter above the search box confirms how many are
selected.

Verified on wow.export v0.2.19: searching `vo_121` returns 347 files, the whole
of patch 12.1's voice-over, exportable in one action.

The audio tab also has quick filters for OGG/MP3/UNK. Set it to OGG, since all
voice-over is Ogg Vorbis.

Because selection happens through the search box, prefer filters that describe
the set you want (`vo_121_`, an NPC folder name) over generating an ID list.
`manifest` remains useful for feeding a command-line extractor, and for knowing
in advance how many files a given selection should produce.

### Command-line extraction

`--paths` emits `sound/creature/<npc>/<file>.ogg` lines instead of FileDataIDs,
for extractors that take a file list rather than IDs. Be aware that the obvious
candidate, [erorus/casc](https://github.com/erorus/casc), needs PHP 7.2+ plus a
`composer install`, and its README states Windows support is untested. On
Windows, Paste Selection above is the less painful route.

Either way, keep the exports grouped one directory per NPC. The listfile layout
(`sound/creature/<npc>/`) gives that for free, and the synthesis step keys
reference audio by NPC.

## Picking reference clips

More is not automatically better. For zero-shot cloning, clip quality dominates
clip count:

- Prefer clean spoken lines over combat barks, which are shouted and clipped.
- Avoid anything with heavy effects processing — void-corrupted and demonic NPCs
  often have pitch-shifted or layered lines that the model will faithfully
  reproduce as artifacts.
- Six to thirty seconds of clean speech is typically enough. The `--per-npc`
  cap defaults to 40 clips, which is comfortably more than needed.

## Harvesting quest text

`QuestReaderHarvester/` is a companion addon that solves the half of the
pipeline that cannot be datamined. Copy the folder into `Interface/AddOns/`,
enable it, and play. It records the description, progress and completion text of
every quest you interact with, along with the NPC who speaks each passage.

The speaker is recorded **per passage, not per quest**: the NPC who offers a
quest is frequently not the one who takes it back, and voicing the turn-in with
the giver's voice is a mistake that can only be fixed by regenerating.

```
/qrharvest        show how much has been captured
/qrharvest wipe   clear the store and start again
```

SavedVariables are written on logout or `/reload`, so `/qrharvest` reporting
captures does not mean they are on disk yet. The file lands at:

```
World of Warcraft/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/QuestReaderHarvester.lua
```

## Fetching quest text from Wowhead instead

`wowhead_quests.py` gets the same records without playing the content. Wowhead
holds quest text because players upload it, so it can be read back per quest ID.

```sh
python3 wowhead_quests.py --ids missing.txt -o passages.json
```

Feed it the quest IDs `/qrmissing` reports, or any list one per line. It
extracts the description, progress and completion text plus the quest giver and
turn-in NPC, assigning the giver to the offer and the turn-in NPC to the rest.
Pages are cached, so reruns cost nothing and only new IDs are fetched.

Which route to use:

| | Coverage | Freshness | Cost |
| --- | --- | --- | --- |
| Harvester | only what you play | authoritative | play time |
| Wowhead | everything at once | lags a patch by days | third-party data |

**Wowhead's terms of use do not permit scraping.** This is the route the
original project relied on — the addon README's note that "the source I utilize
for quest info can sometimes take several days" describes exactly this lag —
but it is a deliberate choice rather than a safe default. Requests are paced at
1.5s by default and cached; raise `--delay` rather than lowering it.

The practical combination is Wowhead for bulk and the harvester for anything it
lacks, since brand-new content is where its coverage is thinnest and where a
live client is authoritative.

## Preparing text for synthesis

`harvest_export.py` reads that file and emits one record per spoken passage —
quest, speaker, and text with Blizzard's markup expanded into speech.

```sh
python3 harvest_export.py QuestReaderHarvester.lua -o passages.json
python3 harvest_export.py QuestReaderHarvester.lua --format csv -o passages.csv
```

The expansion is the reason this step exists. Quest text is written to be read,
not spoken, and a model handed it raw will pronounce the markup:

| Markup | Becomes |
| --- | --- |
| `$n`, `$p` | the `--player-name` value, default "adventurer" |
| `$r` / `$c` | "traveler" / "hero" |
| `$b`, `\|n` | paragraph break |
| `$g Lad:Lass;` | one side, chosen by `--gender` |
| `\|4offering:offerings;` | the singular form |
| `\|cFFFF0000...\|r` | stripped |

Markup the script does not recognise is **reported rather than passed through**,
so unknown tokens surface before generation instead of appearing in the audio.
Passages with no NPC recorded are counted too — those come from objects and
auto-accepted quests, and cannot be voice-matched without a manual assignment.

The SavedVariables file is parsed rather than executed, so a file coming back
from someone else's game client cannot run code.

## Reducing the library size

The shipped library is **not** what its filenames suggest. Of 5,238 files
carrying a `.wav` extension:

| Actual format | Files | Size | Share |
| --- | ---: | ---: | ---: |
| MP3 | 4,449 | 962 MB | 71% |
| PCM WAV | 788 | 392 MB | 29% |

The game sniffs content rather than trusting the extension, so the mislabelled
MP3s play correctly and always have. But it means most of the library is
already compressed, and re-encoding it would be a lossy-to-lossy transcode:
quality lost for little space gained.

The real saving is confined to the 392 MB of genuine PCM, which re-encodes to
roughly 50 MB — about **340 MB off the download**, not the whole 1.3 GB.

```sh
python3 convert_library.py ../Sounds --dry-run     # estimate from a sample
python3 convert_library.py ../Sounds -o ../Sounds-ogg
python3 build_soundlengths.py ../Sounds-ogg -o ../SoundLengths.lua
```

`--dry-run` converts a spread of the library and extrapolates, rather than
assuming a ratio. Originals are kept unless `--replace` is given. Requires
ffmpeg, either on PATH or via `pip install imageio-ffmpeg`.

Worth knowing that git history holds every past content drop, so the repository
is several times the working tree regardless of what the working tree contains.
Shrinking the audio helps users downloading a release; it does not shrink a
clone.

## Choosing fallback voices

NPCs with no reference audio need a stand-in. Assigning at random makes a dwarf
sound like a dryad, so `npc_traits.py` resolves what each NPC actually is and
groups them, letting one chosen voice serve everyone sharing a race and sex.

```sh
python3 npc_traits.py "Elder Hagar" "Zul'jarra"
python3 npc_traits.py --names-file npcs.txt --groups
```

The chain is `Creature` for name and creature type, then
`Creature -> CreatureDisplayInfo -> CreatureDisplayInfoExtra` for the model's
race and sex. Coverage is partial: NPCs whose model carries no extended display
record resolve only to their creature type, and NPCs too new to appear in the
published tables do not resolve at all. In practice this matters less than it
sounds, because the NPCs that fail to resolve are mostly unique named
characters, and those are the ones that already have reference audio to clone.

## Building the fallback voice bank

`voice_bank.py` turns the trait lookup into an actual assignment: one stand-in
voice per race and sex, applied to every NPC in that group.

```sh
python3 voice_bank.py --names npcs.txt -o voicebank.json
python3 voice_bank.py --names npcs.txt --report
```

Donors are chosen in two tiers. The game already ships around 160 **generic
voice pools** — `generic_blood_elf_citizen_a`, `generic_stormwind_human`,
`generic_amani_civilian` — recorded as anonymous members of a race or faction.
Those are preferred, because they were made for exactly this and carry no
recognisable personality. Where no pool matches the race, the best-recorded
named NPC of that race and sex is used instead.

A pool only matches when **every** word of the race name appears in it. Matching
on partial overlap put night elves on a blood elf pool, since both contain
"elf".

Groups with no viable donor are reported rather than filled with something
unsuitable — those are a judgement call, not something to guess at.

### Feed it real NPC names

Pass the NPCs from `passages.json` (step 3 of the pipeline), not `--patch`.
The patch list comes from *sound folder* names, and many of those are voice
sets rather than creatures — `generic_zulaman_troll_forest_tribe_civilian` is a
pool, not an NPC, so it will not resolve against the creature tables.

## Generating the audio

`synthesize.py` matches each passage's speaker to that NPC's reference clips
and generates one file per passage. The default engine is Coqui XTTS-v2, which
clones zero-shot from a few seconds of audio and outputs at 24 kHz, the rate the
addon already ships.

```sh
python3 synthesize.py passages.json --reference ./reference-audio --dry-run
```

### What happens to the audio

Reference clips are chosen rather than taken in order. Selection was the first
six alphabetically, which is close to random — an NPC whose filenames begin with
attack barks was cloned from shouting. Clips are now ranked to prefer ordinary
dialogue over combat and reaction lines, and to prefer a usable length
(2.5–20 s) over grunts.

Generated audio is trimmed and levelled before encoding. Synthesis output varies
in level between clips and carries a beat of silence at each end, which is
audible as lines jumping in volume against the game's own dialogue. Each clip is
silence-trimmed, normalised to **-16 LUFS**, resampled to 24 kHz mono and
encoded to Ogg Vorbis in a single ffmpeg pass. `--no-normalize` skips it.

That target is measured, not chosen: the game's own dialogue sits at about
-16 LUFS, while the shipped library ranges from -13 to -20. Normalising makes
new audio both consistent with the game and more consistent than the existing
library.

**Always dry-run first.** It reports how many clips would be generated, how many
already exist, and — most usefully — which NPCs have no reference audio and so
need a fallback voice chosen. Planning needs no GPU and no model, so it is worth
running before installing anything.

Generation itself needs `pip install TTS` and, to be practical, a GPU. Runs are
resumable: existing output is skipped, so an interrupted run continues rather
than starting over, and a failed clip can be retried by rerunning.

Ogg encoding shells out to `ffmpeg`. Use `--keep-wav` to skip it.

**This script has not been run against a real model.** Its planning, matching
and resume logic are tested; the generation call itself is not, because no GPU
or TTS install was available where it was written. Expect to adjust it on first
use.

## Rebuilding the index

`SoundLengths.lua` is what the addon consults before playing anything — a clip
missing from it is invisible even when the file is present, so this is a
required step, not a cleanup one.

```sh
python3 build_soundlengths.py ./generated -o ../SoundLengths.lua
```

Durations are read from the audio headers, so no ffmpeg is needed here. Files
not named `<questID>_<passage>.<ext>` are reported rather than silently indexed,
since the addon could never find them.

The addon accepts both `.ogg` and `.wav` and prefers Ogg where a clip exists in
both, so a pack can be converted over gradually rather than all at once.
