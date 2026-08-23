# One addon per expansion — the plan

Written 23 Aug 2026, against real numbers where they exist and marked
explicitly where they don't.

## Why

Full-game audio is projected at ~9 GB (measured: 86.8 KB/clip mean, ~110,000
passages projected at 2.22/quest). That cannot ship as one CurseForge
download (500 MB/file cap) or live in one git repo comfortably (GitHub soft
limits at 1 GB, strong recommendation under 5 GB). Splitting by expansion is
the natural boundary: players already think in those terms, and it matches
how the harvest and voicing work is being done anyway.

## The mechanism (built today, verified, not yet committed... wait, committed as of 4949cd2/9be55fa's ancestor `d20e18e`)

Already in place and tested:

- `QuestReaderAddon_RegisterSoundPack(name, index)` in `QuestReaderAddon.lua`
  — a pack calls this once, from its own `Register.lua`, to hand its
  `SoundLengths` table to the base addon. Works regardless of load order
  (a pending-queue global catches the case where a pack loads first).
- A missing pack degrades to "no audio for that quest" — never an error.
- `tools/build_sound_packs.py` splits `Sounds/` + `SoundLengths.lua` into
  per-expansion pack folders, each with its own `.toc`, `Register.lua`, and
  shard index. It never writes to `Sounds/` or `SoundLengths.lua` (verified
  by checksum before/after).
- Mapping quest → expansion comes from `tools/quest_expansions.json`,
  itself built by parsing the Wowhead page's "Added in World of Warcraft:
  &lt;Expansion&gt;" line — there is no expansion field in the client's own
  `QuestV2` table.

## Naming

`QuestReaderAddon_Pack_<Expansion>`, one word per expansion, matching the
existing example (`QuestReaderAddon_Pack_Cataclysm`). `.toc` carries
`OptionalDeps: QuestReaderAddon` and the same `## Interface:` line as the
base addon.

## Expansion order and status

The harvest is being done in per-expansion batches, oldest-content-gap
first, not necessarily release order — whichever is cheapest to scope next.
Numbers below are **as of this run**, not final; they update as harvesting
continues.

| Expansion | Quests mapped so far | Status |
|---|---:|---|
| The War Within | 387 | mapped, largest so far |
| Midnight | 308 | mapped, active development content |
| Dragonflight | 31 | mapped |
| Battle for Azeroth | 1 | mapped |
| Shadowlands | 0 | not yet harvested |
| Legion | 0 | not yet harvested |
| Warlords of Draenor | 0 | not yet harvested |
| Mists of Pandaria | 0 | not yet harvested |
| Cataclysm | 0 | not yet harvested (has the example pack skeleton) |
| Wrath of the Lich King | 0 | not yet harvested |
| The Burning Crusade | 0 | not yet harvested |
| Classic | 0 | not yet harvested |

"Mapped" means a cached Wowhead page confirms the expansion. A further
~367 quest IDs have been fetched but carry no "Added in" line at all — that
is a ceiling of the method, not a scoping failure, and those will end up in
a Shared/Unsorted pack regardless of how much more harvesting is done.

**1,716 quest IDs that already have audio have not been fetched at all yet**
(scope note from earlier today) — at 1.5s/request that is ~13 hours on its
own, separate from the "no audio yet" harvest currently running.

## The pipeline, running now

1. **Harvest** (network-bound, polite rate limit, currently running):
   fetches Wowhead pages for quest IDs with no audio yet, expansion-by-
   expansion, writing `tools/harvested_<expansion>.json` per batch and
   updating `quest_expansions.json` as it goes.
2. **Voice** (GPU-bound, done by the coordinator as each batch lands):
   dry-run against the current voice bank, synthesise what resolves,
   install, rebuild `SoundLengths.lua` with the 1:1 gate, commit, push.
3. **Voicebank gaps**: passages whose NPC has no reference audio at all
   are deferred to a voicebank-expansion pass (race/sex lookup via client
   tables, Wowhead as fallback) rather than blocking the batch.
4. **Pack split**: once an expansion has a meaningful body of voiced audio,
   `build_sound_packs.py` cuts it into its own pack directory. Not run
   per-batch — batched itself, since it's cheap to rerun and the mapping
   keeps changing.

## What still needs a decision, not automation

- **CurseForge publishing** is one project per pack, each pointed at a
  subdirectory of this repo (there's no native multi-addon-per-repo
  concept on CurseForge). Not started.
- **The `## Interface:` value in each pack's `.toc` is currently hardcoded**
  in `build_sound_packs.py`, not read from the base addon's `.toc`. Fine
  today since they match; will drift on the next interface-version bump
  unless fixed.
- **Whether to also fetch the 1,716 already-audio'd-but-unmapped quest IDs**
  — separate ~13h job, not started, your call.
