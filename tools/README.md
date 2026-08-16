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
python3 voice_sources.py index

# What speech exists for an NPC?
python3 voice_sources.py lookup "Alleria Windrunner"

# Coverage across a list of quest givers, one name per line.
python3 voice_sources.py report npcs.txt

# FileDataIDs to feed the extraction step.
python3 voice_sources.py manifest npcs.txt -o ids.txt
```

Names are matched on whole tokens, so titles and epithets resolve in either
direction — `Xal'atath` finds `xalatath_blade_of_the_black_empire`. Ambiguous
names are reported rather than guessed at.

Python 3 standard library only; no dependencies.

## Extracting the audio

`manifest` produces the list of FileDataIDs to pull. To turn those into files:

1. Install [wow.export](https://github.com/Kruithne/wow.export). It reads from a
   local game install *or* streams from Blizzard's public CDN, so a full client
   install is not strictly required.
2. Point it at your source (local install or CDN) and let it load the build.
3. Open the **Sounds** tab and filter to the FileDataIDs from `ids.txt`.
4. Export. Files arrive as `.ogg` — the format the game stores natively — so no
   transcoding is needed before they are used as cloning references.

Organize the exports one directory per NPC. The synthesis step keys reference
audio by NPC, and the listfile paths (`sound/creature/<npc>/`) already give that
grouping for free.

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
