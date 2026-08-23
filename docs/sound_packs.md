# Splitting audio into per-expansion packs

Design note for shipping voiceover audio as separate, expansion-scoped addon
packs instead of one ~9 GB download. Covers pack layout, the runtime merge,
the honest state of quest->expansion mapping, and what's tested vs. not.

## 1. Pack layout

Each pack is an ordinary WoW addon, independent of the base:

```
QuestReaderAddon_Pack_<Suffix>/
  QuestReaderAddon_Pack_<Suffix>.toc
  SoundLengths.lua      -- this pack's shard, global QuestReaderSoundLengths_Pack_<Suffix>
  Register.lua          -- ~10 lines, registers the shard with the base addon
  Sounds/                -- only this pack's clips
```

Working example checked in at `packs/QuestReaderAddon_Pack_Cataclysm/`
(empty index -- it's a scaffold, not real data, so it doesn't touch the
synthesis run in progress).

The pack's `.toc` lists `QuestReaderAddon` under `OptionalDeps` for load
ordering/UI purposes, but nothing in the merge mechanism actually depends on
that -- see below.

## 2. Runtime merge and load order

`addon.soundSources` (in `QuestReaderAddon.lua`) is a table keyed by addon
name -> that addon's duration index. Every lookup path (`PlayQuestAudio`,
`PlayGossipAudio`, `PlayItemAudioDirect`, and `QuestAudioLibraryUI`) already
iterated this whole table before this change -- that generic lookup didn't
need to change at all, it was already pack-count-agnostic.

What was missing was how a pack gets *into* that table without the base
addon knowing its name in advance. New mechanism, added at the top of
`QuestReaderAddon.lua`:

- `addon.soundSources` is now initialised at file scope (was previously
  initialised ~200 lines later), so it exists as early as possible.
- `QuestReaderAddon_RegisterSoundPack(packName, soundLengths)` is a global
  function a pack calls to merge itself in.
- Because WoW does not guarantee load order between independent addons, a
  pack calls it defensively:
  ```lua
  if QuestReaderAddon_RegisterSoundPack then
      QuestReaderAddon_RegisterSoundPack(addonName, ThisPackIndex)
  else
      QuestReaderPendingSoundPacks = QuestReaderPendingSoundPacks or {}
      table.insert(QuestReaderPendingSoundPacks, { name = addonName, index = ThisPackIndex })
  end
  ```
  If the base addon hasn't loaded yet, the function doesn't exist, so the
  pack drops itself into a plain global table that exists independent of
  which file runs first.
- `QuestReaderAddon.lua`, immediately after defining the registration
  function, drains that pending table if it's non-empty.

This is safe in both orders:
- **Base loads first:** the function exists, packs call it directly, done.
- **Pack loads first:** pack queues itself; base addon drains the queue the
  moment it loads.

**Absent pack:** nothing to drain, nothing to merge, `addon.soundSources`
just has one fewer entry. Every lookup already treats a missing sound file
as "no audio for this quest" (existing `reportedMissing` path) -- an absent
pack is indistinguishable from a pack that's installed but hasn't voiced
that particular quest yet. No new error path was needed.

The old mechanism (`optionalSoundPacks` / `DetectSoundPacks`, used for the
existing `QuestReaderAddon_TWW_EN` / `_FR` language packs) is left in place
and still works -- it merges into the same `addon.soundSources` table via a
different route (base addon explicitly loads named OptionalDeps). New
expansion packs should use self-registration; it doesn't require editing
the base addon's source list every time a pack ships.

## 3. Quest -> expansion mapping: the honest answer

**This is not reliably derivable from what's in the repo.** Specifically:

- `tools/.cache/QuestV2.csv` (the client's QuestV2 table) has three columns:
  `ID`, `UniqueBitFlag`, `UiQuestDetailsThemeID`. No zone, no expansion.
- Nothing else cached (`Creature.csv`, `CreatureDisplayInfo*.csv`, the
  community listfile, `vo-index.json`) carries a quest-level expansion field.
- `tools/voice_sources.py`'s `--patch` filtering is about which patch a
  piece of *Blizzard reference audio* (`vo_<patch>_<npc>_<nn>.ogg`) was
  recorded for -- an NPC's voice patch, not a quest's expansion. Unrelated
  axis, not reusable for this.
- `QuestReaderAddon_CampaignLore`, mentioned as something to check for
  precedent, does not exist anywhere in this repository (checked via
  ripgrep across the tree, docs, and CLAUDE.md/GEMINI.md) -- there is no
  such module here to draw on.

**What does exist and is partially usable:** `tools/.cache/wowhead/<questID>.html`,
the per-quest pages `wowhead_quests.py` already fetches for quest text, embed
a line in the page's meta description:

```
Added in World of Warcraft: The War Within. Always up to date with the latest patch.
```

`tools/build_sound_packs.py` parses this. Measured against the actual cache
today: **2,810 quest IDs appear in the current sound library; only 269 of
them (10%)** have a cached wowhead page with a parseable "Added in" line.
Everything else — the large majority — has no known expansion.

This is not guessed at. Unmapped quest audio goes into a pack named
`Unsorted` rather than being assigned a best-guess expansion or dropped.
`Unsorted` still ships and still plays; it's just not expansion-sorted.

**How to actually get real coverage**, in rough order of effort:
1. **Free, incremental:** keep running `wowhead_quests.py` as it already
   does for quest text — cache coverage (and therefore expansion coverage)
   grows for free, and re-running `build_sound_packs.py` picks up the
   improvement automatically.
2. **Direct fetch:** scrape wowhead's per-expansion quest listing pages
   (e.g. the filterable quest search scoped to "The War Within"), which
   return the full ID set for an expansion in a handful of paginated
   requests instead of one page per quest. Would need a new script akin to
   `wowhead_quests.py`'s existing rate-limit/backoff handling.
3. **Client-side, if a suitable table exists:** a quest's zone (if it could
   be obtained from a table not currently cached here) joined against
   `Map.db2`'s `ExpansionID` field would give an authoritative mapping
   without scraping. Nothing here currently caches quest-to-zone, so this
   would need a new client-table export, not just a new script.

Do not read "10% mapped" as a small gap needing a quick fix — it means 90%
of quests currently voiced would land in `Unsorted` if packs were built
today. Building real per-expansion packs is blocked on doing (1) or (2)
above, not on anything in this change.

## 4. How many packs, and rough sizes

Measured by running `tools/build_sound_packs.py --dry-run` against the
current library (6,777 clips / ~86.8 KB mean, per the task's own numbers):

| Pack | Clips | Duration | Size |
| --- | ---: | ---: | ---: |
| `QuestReaderAddon_Pack_Midnight` | 333 | 1.4h | 34.0 MB |
| `QuestReaderAddon_Pack_TheWarWithin` | 236 | 0.5h | 13.5 MB |
| `QuestReaderAddon_Pack_Shared` (gossip/items, no quest ID) | 32 | 0.1h | 3.4 MB |
| `QuestReaderAddon_Pack_Unsorted` (quest audio, expansion unknown) | 6,176 | 24.2h | 1,107.8 MB |

At the full projected ~110,000 passages / ~9 GB, and assuming mapping
coverage improves before that point, expect on the order of 8-10 packs (one
per shipped expansion from Classic through the current one, plus `Shared`
and a residual `Unsorted`), each well under both CurseForge's 500 MB
per-file cap and GitHub's size guidance, since even the entire library
today comes in under 1.2 GB.

## 5. What's tested vs. not

**Tested:**
- `luac -p` syntax-checks cleanly on `QuestReaderAddon.lua`,
  `QuestAudioLibraryUI.lua`, `QuestReaderAddon_Settings.lua`, and the
  example pack's `Register.lua` / `SoundLengths.lua`.
- `tools/build_sound_packs.py` run both as `--dry-run` and for real (to a
  scratch temp directory, since the instructions say never to write into
  this repo's `Sounds/`/`SoundLengths.lua` or leave stray output behind).
  The real run produced four well-formed packs, correct manifest.json, and
  every generated shard round-tripped through `luac -p` successfully.
- Confirmed via checksum/file-count that `SoundLengths.lua` and `Sounds/`
  are byte-for-byte unchanged after running the build tool.
- `QuestAudioLibraryUI.lua`'s two `addon.soundSources` loops were read and
  confirmed to already be pack-count-agnostic — no changes were needed
  there.

**Not tested, because it requires a running WoW client:**
- That `QuestReaderAddon_RegisterSoundPack` and the pending-queue fallback
  actually behave correctly under both real load orders in-game. The logic
  mirrors the existing `DetectSoundPacks`/merge pattern already shipping
  for the TWW language packs, but the specific queue-and-drain path is new
  and unexercised by a live client.
- That a pack with real audio in `Sounds/` actually plays back correctly
  end-to-end (path construction, `PlaySoundFile`, etc.) — only the file
  layout and Lua syntax were verified, not runtime playback.
- CurseForge packaging behavior for multiple addon folders shipped from one
  or more repos (out of scope for this change, flagged in
  `docs/automation.md`'s existing distribution section).

**What the owner should do to test it:**
1. Run `python tools/build_sound_packs.py --out packs --dry-run` to see the
   current pack breakdown without writing anything, then without `--dry-run`
   to actually produce `packs/`.
2. Copy `QuestReaderAddon` and one or two built packs (or the
   `packs/QuestReaderAddon_Pack_Cataclysm` scaffold, after populating its
   `SoundLengths.lua`/`Sounds/` with a couple of real test clips) into
   `Interface/AddOns/` in a live install — back up whatever's there first,
   per `CLAUDE.md`.
3. Load into the game with only the base addon enabled: confirm quests with
   no matching pack degrade silently (no Lua error, "no audio" debug message
   via `/qrdebug on`).
4. Enable a pack, reload UI, confirm its clips play. Try disabling the base
   addon's load order relative to the pack (WoW doesn't expose this
   directly, but toggling which addon is alphabetically-ordered gives some
   signal) to get partial confidence on the load-order handling.
5. Open `/qrlibrary` and confirm quest IDs from an enabled pack show up
   alongside base-addon quest IDs.

## 6. Quest -> expansion mapping, updated

`tools/build_sound_packs.py` now reads `tools/quest_expansions.json` (a
direct `{questID: expansion}` map, fetched by a separate scraping job) as
its **primary** source, and falls back to the `tools/.cache/wowhead/<id>.html`
"Added in ..." scrape (section 3, above) only for quest IDs the primary map
doesn't cover. A `null` expansion value in `quest_expansions.json` (quest
exists but Wowhead has no expansion for it) is treated the same as "not
covered" -- it does not crash the build and does not get slugified into a
bogus pack name.

The file is entirely optional: the script checks for it on every run and
does not wait for it. If it is missing, behavior is identical to before.

The build now also prints an explicit, hard-to-miss line to stderr stating
what fraction of clips (by count) landed in `Unsorted`, and writes a
`README_UNSORTED.txt` inside the `Unsorted` pack folder itself explaining
why, so nobody mistakes it for a bug or ships it as a mystery 1+ GB pack
without realizing what's in it. `manifest.json` also carries
`unsorted_clip_count`, `unsorted_share_of_total`, and
`quest_expansions_json_used` for tooling/CI to check.

## 7. Publishing multiple packs on CurseForge -- what the owner must actually do

CurseForge (and `pkgmeta.yaml`/the `curseforge-packager` tool already used by
this repo) does not have a native concept of "one repo, many downloadable
addons." Concretely, each pack that should be independently installable and
independently size-capped needs to be **its own CurseForge project**:

1. **Create a separate CurseForge project per pack** (e.g. "SpeakStone
   Narration - Cataclysm Voices", "... - Unsorted", "... - Shared"), each
   with its own project slug/page. The base `QuestReaderAddon` project stays
   as-is. This is a manual, one-time step per pack in CurseForge's web UI --
   there is no API-only way to do it that also gets you a discoverable page.
2. **Each pack needs its own repo (or its own `pkgmeta.yaml`+tag in a
   monorepo) that CurseForge's packager can build from.** The simplest
   approach given this is currently one repo: give each pack folder under
   `packs/` its own `pkgmeta.yaml` (copy the root one, change `package-as:`
   to that pack's addon name, e.g. `QuestReaderAddon_Pack_Cataclysm`), and
   register each pack folder as a separate "project" in CurseForge pointing
   at the same repo but a different root/tag. CurseForge's packager supports
   building from a subdirectory via the project's repository settings (root
   path), so a monorepo with `packs/<Name>/pkgmeta.yaml` per pack works
   without splitting into N repos.
3. **`.toc` per pack**: already produced correctly by
   `build_sound_packs.py` -- `## Interface:`/`## X-Interface:` are copied
   verbatim from the same values as the base addon's `.toc`
   (`110207, 120000, 120001, 120100` as of this writing). If the base
   addon's Interface line is ever bumped for a new patch, this script's
   hardcoded copy in `write_pack()` must be bumped too -- it is not read
   from `QuestReaderAddon.toc` automatically. **This is a real gap**: today
   the pack `.toc` Interface line is a hardcoded string literal in
   `build_sound_packs.py` (`write_pack()`), not derived from
   `QuestReaderAddon.toc`. A future patch bump to the base addon's
   `## Interface:` line will silently NOT propagate to newly-built packs
   until this script is edited too. Recommended follow-up: have
   `build_sound_packs.py` parse `## Interface:`/`## X-Interface:` out of
   `QuestReaderAddon.toc` at build time instead of hardcoding them.
4. **Each pack's `.toc` already declares `## OptionalDeps: QuestReaderAddon`**,
   which CurseForge's own installer (the app, not the addon loader) uses to
   suggest/require the base addon when a user installs a pack -- worth
   confirming this actually surfaces as expected in the CurseForge app,
   since that's a CurseForge-side behavior, not something this repo
   controls.
5. **Size**: every pack built today is well under CurseForge's 500 MB cap
   (`Unsorted` is the largest at ~1.1 GB total library size *before* this
   split -- individual packs after the split are 0.3-35 MB each). Re-check
   this as the library grows toward the ~9 GB full-game projection --
   `Unsorted` in particular could eventually need splitting further if
   expansion-mapping coverage doesn't improve faster than the library grows.
6. **GitHub**: if packs are also distributed via GitHub Releases (not just
   CurseForge), each pack's built directory should be zipped as a release
   asset per pack, not committed to the repo as tracked binary files --
   committing built `Sounds/` content for every pack into git would recreate
   the exact repo-size problem this whole split is meant to solve.
