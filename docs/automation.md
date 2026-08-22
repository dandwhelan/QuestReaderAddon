# Automating generation and publishing

How a passage gets from someone pasting a harvest into Speakstone to a voiced
clip shipped in the addon, and how much of that can safely run without a human.

Written after measuring the actual constraints rather than assuming them. The
short version: **synthesis automates well, publishing does not, and the reason
is the git repository, not the pipeline.**

## Phase 0: the measurement, and what it found

### Audio conversion saves less than it looks like it should

`convert_library.py --dry-run` against the current `Sounds/`:

```
788 genuine-PCM file(s), 392.5 MB.
  (4,450 more .wav file(s) are already compressed audio and are left alone.)
Projected: 392.5 MB -> 49.9 MB (saves 342.6 MB)
```

So of a 1.6 GB working tree, conversion recovers **343 MB — about 21%**. The
other 962 MB of `.wav` is MP3 inside a wav wrapper; re-encoding it would be a
second lossy pass for bytes that were never there to save, which is why
`convert_library.py` sniffs the `fmt` chunk and skips them.

Worth doing. Not a solution.

### …and it turned out to have been done already

Running the conversion for real produced 788 files that **already existed in
`Sounds/` at identical sizes**. Same audio, different bytes only because an Ogg
stream carries a random serial number in its container. These were converted
in some earlier pass and the `.wav` originals were kept alongside them, exactly
as the tool's "originals kept" behaviour intends.

So there is nothing to convert. What there is, is redundancy:

```
790 .wav files that already have a sibling .ogg
  .ogg present, indexed, non-trivial : 790 / 790
  reclaimable by deleting those .wav : 393.3 MB
```

All 790 verified individually: the `.ogg` exists on disk, is listed in
`SoundLengths.lua`, and is not a truncated stub. `QuestReaderAddon.lua` sets
`SOUND_EXTENSIONS = { ".ogg", ".wav" }`, so the addon reaches for `.ogg` first
regardless.

Deleting them is therefore safe *and* is still not something to do
automatically — `CLAUDE.md` puts that after confirmed in-game playback, and
confirming playback is not something a script can do:

```bash
# after confirming playback in game
python - <<'PY'
import os
removed = bytes_freed = 0
for root, _, files in os.walk("Sounds"):
    for f in files:
        if not f.lower().endswith(".wav"):
            continue
        ogg = os.path.join(root, os.path.splitext(f)[0] + ".ogg")
        if os.path.exists(ogg):
            wav = os.path.join(root, f)
            bytes_freed += os.path.getsize(wav)
            os.remove(wav); removed += 1
print(f"removed {removed:,} redundant .wav, freed {bytes_freed/1024/1024:,.1f} MB")
PY
```

### The actual constraint is git history

| | |
| --- | --- |
| `Sounds/` working tree | 1.6 GB, 7,565 files |
| `.git` | **4.3 GB** |
| Remote | `github.com/dandwhelan/QuestReaderAddon` |

GitHub recommends repositories stay under 1 GB and strongly recommends under
5 GB. This one is at 4.3 GB of history and the entire premise of automation is
to add audio to it on a schedule.

**Converting to Ogg does not help this and slightly hurts it.** Git keeps every
blob it has ever seen; re-encoding writes ~50 MB of new objects while the
original 392 MB stays in history permanently. The working tree shrinks, the
repository grows.

There is a second version of the same problem facing users: a 1.6 GB addon is
already a large download, and it only goes one way.

### The options, honestly

None of these are free wins.

**Leave it.** Works until it doesn't. Pushes get slower; at some point GitHub
starts pushing back. No work today, a forced migration later at a worse moment.

**Convert to Ogg, keep everything in git.** Recovers 343 MB of working tree and
of every future clone's checkout. Cheap, safe, non-destructive, does nothing
for the 4.3 GB. Reasonable as a first move regardless of what else is decided.

**Move audio out of git history entirely** (`git filter-repo`, then distribute
audio another way). This is the only option that actually shrinks `.git` —
plausibly to tens of MB. It rewrites history, so every existing clone breaks
and the push is a force-push. Not something to do casually, and not something
to do without deciding the distribution question below first.

**Git LFS.** Moves blobs out of the main pack. GitHub's free LFS allowance is
1 GB storage and 1 GB/month bandwidth; the library is past that already, so
this costs money from day one. Migration still rewrites history. The
CurseForge packager's behaviour with LFS pointers also needs verifying before
committing to it.

**GitHub Releases as the audio channel.** Release assets do not count toward
repository size. Audio ships as versioned archives, the code repo stays small.
The complication is that the CurseForge packager builds from a git clone, so
audio not in the repo is audio not in the package unless the packaging step
assembles it separately.

**Recommendation:** do the Ogg conversion now — it is safe and independently
worthwhile. Treat the history question as a separate, deliberate decision,
because every real fix for it is destructive and depends on where audio should
live long-term. Do not let automation start writing into a repository that has
not had that decision made.

## The machines

**Gaming PC** — the only one that can synthesise. F5-TTS needs CUDA. This is
the whole job.

**Pi 5** — cannot run F5-TTS in any useful sense: no CUDA, and CPU inference on
ARM would be hours per clip. It is a good orchestrator though: polling
Speakstone, holding a queue, running `build_soundlengths.py` (pure Python),
git operations, waking the PC. Orchestrator, not worker.

The PC not being always-on is the scheduling problem. Two workable shapes:
Pi wakes it with Wake-on-LAN when a batch is worth running, or the PC checks
for work whenever it happens to be on. The second is far simpler and is
probably sufficient for a single operator.

## Phase 1: `tools/run_batch.py`

One command that replaces the copy-paste between Speakstone, `synthesize.py`,
`convert_library.py` and `build_soundlengths.py`.

```bash
export SPEAKSTONE_KEY=...          # the site's export API key
python tools/run_batch.py          # fetch, synthesise, convert, report
# listen to tools/generated/
python tools/run_batch.py --install --skip-fetch --skip-synth
```

What it deliberately does **not** do:

- never commits, never pushes
- never tells Speakstone anything has been voiced
- never touches `Sounds/` or `SoundLengths.lua` without `--install`
- never assigns a stand-in voice to an NPC with no reference audio

That last one is a rule from `CLAUDE.md`: choosing a donor voice is a
judgement call, not a default. NPCs without their own clips are reported so a
human can decide, rather than quietly getting whatever the fallback is.

### The gates

**Index rebuild refuses to shrink.** `SoundLengths.lua` is real user data — a
clip missing from it is invisible to the addon even when the file exists on
disk. The rebuild writes to a temporary file, counts entries, and only moves it
into place if the count did not go down. A timestamped backup is taken
regardless. If the count drops the run aborts and the live index is untouched.

It also warns, without failing, when the new entry count is lower than the
number of clips just generated — usually meaning some synthesis silently
failed.

**Never overwrites shipped audio.** On `--install`, a generated file whose name
already exists in `Sounds/` is skipped and reported. A collision is either a
deliberate regeneration or a bug; both deserve to be seen.

**A lock file** prevents two runs fighting over the same output directory and
index.

### Reference audio

Synthesis clones from clips extracted with wow.export, conventionally
`tools/reference-audio` (`sound/creature/<npc>/*.ogg` and friends). That
directory is gitignored, so it does not travel with a clone — on a machine with
more than one checkout it commonly sits beside a *different* one than the code
being run. `run_batch.py` therefore looks in several places and names all of
them if it finds nothing:

1. `--reference`
2. `$QR_REFERENCE_AUDIO`
3. `tools/reference-audio`
4. `~/QuestReaderAddon/tools/reference-audio`

### Note: there are currently two checkouts on this machine

| Path | Commit | Reference audio | Harvester `$n` fix |
| --- | --- | --- | --- |
| `Documents/Projects/questwow` | `f5e9ab3` (= `origin/main`) | no | **yes**, uncommitted |
| `~/QuestReaderAddon` | `225535e` (older) | **yes**, 1,648 clips | no |

They share history — the Documents checkout is ahead. Worth reconciling before
either is used for a real run, since the uncommitted harvester change (which
strips the player's character name before it is ever written) exists in only
one of them, and the reference audio exists in only the other.

## Phase 2: unattended running

Two halves, deliberately one-directional — the Pi never reaches into the PC,
so there are no credentials for it to hold and nothing to leave half-done if
it reboots mid-cycle.

**On the GPU machine**, leave this running:

```bash
export SPEAKSTONE_KEY=...
python tools/run_batch.py --watch 30
```

It checks Speakstone every 30 minutes, and when work appears runs the same
gated path as a manual run (`--watch` implies `--install`). A batch that fails
logs and waits for the next cycle instead of killing the watcher — the gates
mean a failure damages nothing, so there is no reason to stop.

**On the Pi**, if the GPU machine sleeps:

```bash
export SPEAKSTONE_KEY=...
python tools/wake_when_work.py --mac AA:BB:CC:DD:EE:FF --host gaming-pc --watch 30
```

Polls for pending work and sends a Wake-on-LAN packet when there is some. It
checks whether the machine is already up first, and it asks Speakstone for
`limit=1` without `pull=1` — polling must never consume the batch the GPU box
is about to request, and there is no reason to drag megabytes of text over the
wire to learn whether a number is zero.

Cron equivalent, if you would rather not leave it running:

```cron
*/30 * * * * SPEAKSTONE_KEY=... /usr/bin/python3 /path/to/tools/wake_when_work.py --mac AA:BB:CC:DD:EE:FF
```

### Timing measures itself

Every run appends to `tools/run_history.json` and reports seconds-per-clip,
then projects what 50, 250 and 1,000 clips would cost. Scheduling decisions —
whether batches need checkpointing, whether they belong in quiet hours —
depend entirely on that number on the actual GPU, and guessing it designs the
wrong system. After the first real run there will be a measured figure to
design against instead of an estimate.

**Phase 3 — audition on Speakstone.** The site already holds the passage text
and has an owner-only area. Adding an audio review step there — clip and text
side by side, approve or reject, publish only what passed — would give a
phone-friendly review queue and make marking-as-voiced automatic on approval.

The automated quality gates worth having either way: duration against expected
characters-per-second (duration is already computed for the index), silence
detection, and loudness against the −16 LUFS target the pipeline already aims
for. None of them catch "it pronounced the name wrong", which is why a human
ear stays in the loop before anything reaches users.
