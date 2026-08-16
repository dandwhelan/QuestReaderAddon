# Voice source tooling

Tooling for the audio generation pipeline. This directory covers the first half
of the problem — finding and extracting the in-game speech that AI voice models
are cloned from. It does not generate anything yet.

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

## Next

The remaining stages are text acquisition, text normalization (quest text is
full of `$n`, `$r`, `$c`, `$b` and `$g male:female;` substitution tokens that
must be expanded before synthesis), synthesis, and packaging. See the pipeline
plan for the full sequence.
