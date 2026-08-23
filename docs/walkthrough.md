# Operator walkthrough

Step-by-step process for running the SpeakStone Narration pipeline end to
end: harvesting quest text, generating voice clips, installing them into the
addon, building expansion packs, and publishing. Written so someone who has
never run this before (including a future version of the owner) can follow
it cold.

This assumes Windows, and `python` on PATH pointed at a normal (non-venv)
Python 3.13 install for anything that isn't synthesis itself. Use `python`,
not `python3`, throughout — that's what's actually on this machine.

Read `CLAUDE.md` and `tools/README.md` before making changes to any of this;
this document is the runbook, those are the reference and the safety rules.

## 0. Prerequisites

- **ffmpeg** on PATH (or `pip install imageio-ffmpeg`). Needed for loudness
  normalization, Ogg encoding, and reference-clip preparation. Not needed by
  `build_soundlengths.py` itself — it reads audio headers directly.
- **`.venv-f5`** — a dedicated virtualenv for the F5-TTS engine, which is the
  default engine (not XTTS-v2, despite what an older paragraph in
  `tools/README.md` still says). Set it up once:
  ```
  python -m venv .venv-f5
  .venv-f5\Scripts\pip install torch==2.8.* --index-url <cu128 wheel index>
  .venv-f5\Scripts\pip install f5-tts
  .venv-f5\Scripts\pip uninstall -y torchcodec
  ```
  The `torchcodec` uninstall is required, not tidying: torch ≥2.9 pulls in
  torchcodec, which needs FFmpeg *shared* libraries; the usual Windows ffmpeg
  builds are static-only, so torchcodec fails with a cryptic `WinError 127`
  — after the model has already loaded, which makes it look like a model
  problem, not a codec one. `run_batch.py` looks specifically for
  `.venv-f5`'s python and hard-errors if it's missing, rather than silently
  falling back to the ambient interpreter (which would produce a confusing
  `ImportError` two steps later instead of a clear message now).
- **Reference audio**, extracted from the game client via wow.export, at
  `C:\Users\danw\wow.export\sound` by convention. The pipeline actually
  looks in this order: `--reference` flag, then `$QR_REFERENCE_AUDIO`
  env var, then `tools/reference-audio`, then
  `~/QuestReaderAddon/tools/reference-audio`. Reference audio is gitignored
  and does not travel with a fresh clone — if you're on a new machine or a
  fresh checkout, you need to extract or copy it in before synthesis will
  find any voices at all.
- **`SPEAKSTONE_KEY`** environment variable — the API key for the companion
  website (Speakstone) that queues quest text and receives "this is now
  voiced" updates. Required for fetching new work (`--input` lets you skip
  this and supply passages from a local file instead), for
  `--tell-speakstone`, and for `--audition`. `SPEAKSTONE_URL` defaults to
  `https://speakstone.beanw.co.uk` and rarely needs overriding.

## 1. Harvesting quest text

Two independent sources feed the same pipeline; you don't need both.

- **Wowhead scrape** (bulk, for content already publicly documented):
  ```
  python tools/missing_quests.py     # diff QuestV2 against SoundLengths.lua
  python tools/wowhead_quests.py     # fetch text for the missing IDs
  ```
  `wowhead_quests.py` rate-limits itself (1.5s default delay) and retries
  403/429 with backoff, writing partial progress if a run gets cut off. If
  you need it faster, don't lower `--delay` — raise it, or just let it keep
  running; running two fetches against the cache at once roughly halves the
  effective pacing and has caused a rate-limit incident before.
- **Live harvest** (for gossip text and anything Wowhead doesn't have —
  there is no gossip-text table anywhere in the client, so this is the only
  source for it): enable `QuestReaderHarvester` in-game and just play. Text
  lands in that addon's SavedVariables file, then:
  ```
  python tools/harvest_export.py
  ```
  which converts the harvester's capture into the same format the Wowhead
  fetch produces, including reverting the game's actual placeholder tokens
  (`$n`, `$g`) back to a form `synthesize.py` understands.

Either way, this step only produces *text*, not audio.

## 2. Running synthesis — dry-run first, always

```
python tools\.venv-f5\Scripts\python.exe tools/synthesize.py --dry-run
```

(or just `python tools/synthesize.py --dry-run` if you've activated
`.venv-f5`). The dry run reports, without writing anything: how many
passages would be generated, how many are already done, how many have "no
voice" (see below), any residual placeholder tokens still sitting in text
that should have been substituted away, and any NPC that falls back to a
stand-in voice. Read that output before running for real — this is where
you catch a placeholder that will otherwise get read aloud in-game months
later, or a passage that's about to fail because it stripped down to an
empty string.

**"No voice" in the output** means the passage's speaking NPC has no
reference audio *and* no assigned voice-bank donor — not that something
crashed. These are counted and reported, never silently defaulted to
whatever voice happens to be lying around. See "NPCs with no reference
audio" below for what to do about it.

Once the dry run looks right, drop `--dry-run` to actually generate. Output
lands in `tools/generated/` by default, not into `Sounds/` — nothing here
touches the live library yet.

If nothing new was ready to synthesize, `synthesize.py` prints "Nothing to
do." and exits 0. That's a normal, successful outcome, not a failure —
contrast with `convert_library.py` below, which is the opposite.

## 3. Installing clips and rebuilding the index

Don't run `build_soundlengths.py` directly against the real
`SoundLengths.lua` by hand unless you understand what you're doing — prefer
`tools/run_batch.py --install`, which wraps the same rebuild with a safety
gate that `build_soundlengths.py` itself does not have.

What `--install` actually does: copies every file out of the generated
output directory into `Sounds/`, **skipping (never overwriting) any filename
that already exists** and logging the skip, then rebuilds the index.

### The index-shrink gate, and why it exists

`SoundLengths.lua` is the addon's actual index — a clip missing from it is
invisible to the addon even when the audio file is sitting right there on
disk. `build_soundlengths.py` on its own is a dumb regenerate-from-directory
tool: point it at `Sounds/` and it writes whatever it finds, full stop, with
no memory of what used to be there.

The gate lives one level up, in `run_batch.py`'s install step:

1. It rebuilds into a temporary file, never touching the real index directly.
2. It takes a timestamped backup of the current index unconditionally,
   before comparing anything.
3. It counts entries in the old index and the new one.
4. **It only replaces the live index if the new count is greater than or
   equal to the old one.** If the rebuild has *fewer* entries than what's
   already live, the run aborts with an explicit message, the temp file is
   discarded, and the real `SoundLengths.lua` is left completely untouched.

Why this matters: a rebuild that silently drops entries is worse than no
rebuild at all, because the files are still on disk and look fine on
inspection — the only symptom is a specific clip going silent in-game, which
nobody notices until someone plays that exact quest. This has already
happened by accident once; the gate exists specifically because of that.

It also warns (without aborting) if the new count is less than
old-count-plus-newly-generated-count — usually a sign some clips failed to
generate silently, worth investigating even though it's not fatal.

If you ever do run `build_soundlengths.py` directly against the real file —
don't, but if you must — take your own backup first and manually verify the
entry count went up by the expected amount afterward.

## 4. Building and publishing packs

Once there's enough new audio to be worth a pack refresh:

```
python tools/build_sound_packs.py --dry-run   # see counts/sizes first
python tools/build_sound_packs.py
```

This classifies every clip: quest passages get mapped to an expansion pack
by quest ID where that mapping is known, everything else (gossip, book
pages) goes to a shared `Shared` pack, and anything that can't be mapped
lands in `Unsorted` — never guessed at, never dropped. Quest-to-expansion
mapping is a genuinely incomplete, ongoing problem: it comes from a
"Added in World of Warcraft: <Expansion>" line scraped off cached Wowhead
pages, which only exists for a fraction of quest IDs in the library at any
given time (well under half, historically). Don't expect `Unsorted` to be
small.

Each pack folder (`QuestReaderAddon_Pack_<Expansion>/`) is a real, independent
addon — its own `.toc` (with the `## Interface:` line read live off the base
addon's own `.toc`, not hardcoded), its own `SoundLengths.lua` under a
pack-specific global variable name, a small `Register.lua` that calls
`QuestReaderAddon_RegisterSoundPack`, and its own `Sounds/`.
`build_sound_packs.py` refuses to overwrite an existing pack output
directory rather than clobbering it — if you need to force a rebuild, remove
the old output first.

To publish packs to their own per-pack GitHub repos (each needs its own
CurseForge project too, which is a manual one-time step CurseForge doesn't
support automating):
```
python tools/publish_packs.py --dry-run
python tools/publish_packs.py
```
This builds fresh, then for each pack (skipping `Unsorted` deliberately —
publishing "not yet mapped" audio would ship exactly the bloat the split
exists to avoid) does a shallow clone of that pack's repo, replaces its
contents, and pushes only if something actually changed. If a pack's repo
doesn't exist yet on GitHub, it's skipped with a message rather than failing
the whole run — create the repo first.

## 5. Telling the website what's voiced

`python tools/run_batch.py --tell-speakstone` (after `--install`, and only
meaningful after it — this step only has newly-installed filenames to report
if `--install` actually ran) posts the list of newly-installed filenames to
Speakstone's `/api/voiced` endpoint. Concretely, this tells the separate
Speakstone website's own backend which quest passages now have real audio,
so its export queue stops re-offering already-voiced work on the next fetch.
The response reports how many filenames were received, matched to a known
passage, and left unmatched.

`--tell-speakstone` and `--audition` are mutually exclusive — they disagree
about when a clip counts as "checked": `--audition` defers that until a
human reviews it, `--tell-speakstone` marks it immediately. Don't pass both.

If you don't use `--tell-speakstone`, `run_batch.py` prints manual
next-steps instead: commit and push the addon changes, then either drag the
new `SoundLengths.lua` into Speakstone's `/admin/export` page or run
`npm run import:library` on the Speakstone side.

## 6. The one-command pipeline and the audition flow

For routine batches, `tools/run_batch.py` chains most of the above:

```
python tools/run_batch.py --dry-run           # see what it would fetch/do
python tools/run_batch.py                     # fetch, synthesize, install
python tools/run_batch.py --tell-speakstone   # ...and report back
python tools/run_batch.py --audition          # ...instead, queue for review
python tools/run_batch.py --watch 30          # loop forever, every 30 min
```

Order of operations inside a single run: acquire a lock file
(`.run_batch.lock` — a second concurrent run aborts rather than racing) →
fetch new passages from Speakstone (unless `--input`/`--skip-fetch`) →
report a speaker breakdown → stop here if `--dry-run` → synthesize (unless
`--skip-synth`), timing the run into `tools/run_history.json` → shrink any
newly-generated genuine-PCM `.wav` files via `convert_library.py --replace`
→ upload for audition if `--audition` → install + rebuild the index if
`--install` (which `--watch` implies) → tell Speakstone, or print manual
next-steps.

One deliberately quiet bit of plumbing: the convert step calls
`convert_library.py --replace` only when there's actually something to
convert, because **`convert_library.py` exits non-zero when there's nothing
to do** — a real "nothing to convert" outcome, not a failure, but a nonzero
exit all the same. Earlier this was treated as a fatal error by the caller,
which made the whole install path unreachable whenever a batch had nothing
compressible in it. It's handled correctly now, but it's a sharp edge if you
ever call `convert_library.py` yourself and check its exit code naively.

**`--audition`** is the "does this clip actually sound right" gate. It posts
each freshly generated clip's raw audio bytes (not JSON, not multipart — a
plain octet-stream body, deliberately, since base64-in-JSON costs a third
more bytes and multipart needs a parser for one part) to
`/api/audition/upload` on Speakstone, tagged with a batch label (the input
filename's stem, or today's date if none). It runs before the install step
so it works standalone even without `--install`. Automated checks — is the
duration sane for the text length, is there detectable silence, is the
loudness on target — can catch some failure modes, but nothing here catches
"it pronounced the name wrong"; a human still has to listen.

**`--watch MINUTES`** implies `--install` and loops forever, sleeping
`MINUTES * 60` seconds between cycles. A single bad batch (an `Abort`) is
logged and the watcher keeps going rather than dying — so leaving a machine
on `--watch` overnight is meant to be safe against one bad cycle poisoning
the rest.

**`tools/wake_when_work.py`** is a separate, much smaller piece meant for a
low-power machine (e.g. a Pi) that isn't the one doing synthesis: it polls
Speakstone for whether *any* work is waiting (`limit=1`, deliberately
**without** `pull=1` — polling must never consume the batch the GPU machine
is about to request), and if there's work and the target machine looks
asleep (a TCP probe against its RDP port), sends it a Wake-on-LAN packet. It
never SSHes in, never drives the actual run, and needs no credentials for
the target machine — it only wakes it up and leaves the rest to that
machine's own `run_batch.py --watch`.

## 7. NPCs with no reference audio — expanding the voice bank

When a dry run reports an NPC with "no voice," it means that NPC has no
usable reference clips of their own *and* isn't yet covered by the voice
bank fallback. To fix it:

1. `python tools/npc_traits.py` resolves the NPC's race and sex from client
   data (via `Creature` → `CreatureDisplayInfo` → `CreatureDisplayInfoExtra`;
   this chain is incomplete for some newer or unusual models, so it won't
   always succeed).
2. `python tools/voice_bank.py`, fed the actual NPC names that need
   resolving (from `passages.json`, not from a raw patch listing — a patch
   listing is sound-folder names and includes generic voice pools alongside
   real creatures, which is the wrong input here), assigns each a donor:
   preferably one of the ~160 generic voice pools the game ships for exactly
   this (matched only when *every* word of the race name appears in the pool
   name — partial-word matching has put night elves on a blood elf pool
   before, since both contain "elf"), falling back to the best-recorded
   named NPC of the same race and sex if no pool fits.
3. The result is written into `voicebank.json`. Read the donor choice back
   before committing — a bad donor match (wrong gender read as neutral, an
   inappropriate tone) is a judgement call worth documenting in the commit
   message, not something to accept silently just because the script picked
   something.
4. Re-run `synthesize.py --dry-run` and confirm the NPC no longer shows up
   under "no voice."

A handful of race/sex groups still have no genuinely good donor at all.
That's an explicit, documented gap rather than a forced bad match — leave it
unfilled and reported if nothing reasonable exists yet.

## Traps

Real gotchas already hit in production, worth knowing before you touch
anything:

- **Postgres pooler `max: 1` is wrong.** Against a transaction pooler,
  concurrent queries pipeline onto a single connection and hang — this has
  caused at least two apparent full-site outages that looked like a crash
  but were actually one wedged connection with no timeout. The pool is
  meant to run at `max: 5`; don't tune it back down. (This setting lives in
  the separate Speakstone website's own codebase, not in this repo — this
  repo only talks to Speakstone over its API.)
- **`convert_library.py` exits non-zero for "nothing to do."** A nonzero
  exit from this specific tool does not mean it failed — it means there was
  nothing convertible in the input, which is a normal outcome for a batch
  with no new genuine-PCM `.wav` files. `run_batch.py` now guards against
  treating this as a fatal error, but if you ever script around this tool
  yourself, don't check its exit code the naive way.
- **Angle brackets mean two different things depending on context.** Beside
  other dialogue, `<...>` is a stage direction and gets dropped (e.g.
  `<Brek laughs.>`). When the brackets wrap the *entire* passage, it's item
  or object description text and the brackets must be stripped but the
  words kept (e.g. `<The idol appears to be a figure of Ohn'ahra...>` needs
  to survive as spoken text). Getting this backwards either reads stage
  directions aloud, or empties an entire passage down to nothing — the
  latter crashes the TTS engine outright rather than just sounding wrong.
- **WoW 12.x returns tagged "secret" values** where plain strings used to
  come back from certain API calls (unit GUIDs and similar). Code that
  touches these has to guard with `issecretvalue()` / `SecretUtil` and wrap
  anything doing `strsplit` on a GUID in `pcall`, or it errors outright on
  the current client. `QuestReaderAddon.lua`'s GUID-parsing helpers already
  do this; don't remove the guard thinking it's dead code, and don't share
  that logic with the harvester addon by reference — the two addons never
  load together, so there's no clean way to share the code without adding a
  third file just to hold it.
- **The pronunciation lexicon's longest-name-first matching is load-bearing,
  not cosmetic.** Some in-universe names are literal prefixes of longer ones
  (`Amani` inside `Amani'Zar`). If the shorter name is checked first, it
  matches inside the longer one and mangles it. Sorting the lexicon any
  other way (insertion order, alphabetical) breaks specific names silently,
  with no error — you only find out by listening.
- **A rebuilt `SoundLengths.lua` that comes out smaller than the one it's
  replacing is a sign something is badly wrong, not a coincidence to
  shrug off.** See the index-shrink gate above — this is exactly the failure
  mode it exists to catch.
- **Anything under `tools/.cache/`, `tools/generated_*`, and `tools/regen/`
  is disposable** — safe to delete, rebuildable from scratch. `Sounds/`,
  `SoundLengths.lua`, `voicebank.json`, `tools/pronunciations.json`, and a
  live WoW AddOns install are real data; never treat them the same way.
- **Verify what an automated report tells you before acting on it.**
  Confidently-stated numbers from a prior pass (a stale repo-size figure, an
  overstated size-saving estimate) have turned out wrong before. If a
  number matters for a decision — how big something is, how much a change
  will save — re-measure it rather than trusting a cached claim, including
  claims in this document if enough time has passed since it was written.
