#!/usr/bin/env python3
"""One command: pull a batch from Speakstone, synthesise it, index it.

This is the manual-but-single-step version of the pipeline -- phase 1 of the
automation plan in docs/automation.md. It removes the copy-paste between
Speakstone, synthesize.py, convert_library.py and build_soundlengths.py, and
deliberately keeps every judgement call with you:

  * It never commits and never pushes.
  * It never tells Speakstone anything has been voiced, unless you pass
    --tell-speakstone (it still never commits or pushes even then).
  * It never touches Sounds/ or SoundLengths.lua unless you pass --install.
  * --audition sends generated clips to Speakstone's review queue instead of
    marking them voiced -- a human approves or rejects each one there, and
    that verdict is what eventually sets voiced_at. It refuses to run
    together with --tell-speakstone, which sets voiced_at immediately: the
    two flags disagree about whether a clip has been checked yet.
  * NPCs with no reference audio are reported, never quietly given a
    stand-in voice -- picking a donor is a judgement call, not a default.

Typical use, in two steps so there is a look between them:

    run_batch.py                      # fetch, synthesise, convert, report
    <listen to tools/generated/>
    run_batch.py --install --skip-fetch --skip-synth   # into Sounds/, reindex

Environment:
    SPEAKSTONE_KEY   export API key. Required unless --input is given.
    SPEAKSTONE_URL   defaults to https://speakstone.beanw.co.uk
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
DEFAULT_SITE = os.environ.get("SPEAKSTONE_URL", "https://speakstone.beanw.co.uk")

# ["<name>.<ext>"] = <seconds>, -- one entry per clip in the index.
INDEX_ENTRY = re.compile(r'^\s*\["([^"]+)"\]\s*=', re.MULTILINE)


class Abort(Exception):
    """A gate refused to let the run continue. Nothing has been damaged."""


def log(message):
    print(message, flush=True)


def run(command, **kwargs):
    """Run a subprocess, streaming its output, and abort on failure."""
    log(f"\n$ {' '.join(str(part) for part in command)}\n")
    result = subprocess.run(command, **kwargs)
    if result.returncode != 0:
        raise Abort(f"{command[0]} exited {result.returncode}")
    return result


def python_for_synthesis():
    """F5-TTS lives in its own venv; the ambient interpreter will not do.

    .venv-f5 is the default engine's environment. Falling back to sys.executable
    when it is missing produces a confusing ImportError deep inside synthesize.py
    rather than a clear message here.
    """
    candidates = [
        TOOLS / ".venv-f5" / "Scripts" / "python.exe",
        TOOLS / ".venv-f5" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise Abort(
        "No .venv-f5 found under tools/. F5-TTS needs its own environment; "
        "see tools/README.md. Synthesis cannot run on this machine."
    )


def resolve_reference(explicit):
    """Find the extracted in-game clips synthesis clones from.

    Not simply `tools/reference-audio`, because it is gitignored and therefore
    does not travel with a clone -- so on a machine with more than one checkout
    it commonly lives beside a different one than the code you are running.
    Rather than fail with a path the user then has to go hunting for, look in
    the obvious places and say exactly which were tried.
    """
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("QR_REFERENCE_AUDIO"):
        candidates.append(Path(os.environ["QR_REFERENCE_AUDIO"]))
    candidates.append(TOOLS / "reference-audio")
    # A sibling checkout of this same addon, which is where the extraction
    # commonly ends up when the repo has been cloned more than once.
    candidates.append(Path.home() / "QuestReaderAddon" / "tools" / "reference-audio")

    for candidate in candidates:
        if candidate.is_dir() and any(candidate.rglob("*.ogg")):
            if candidate != candidates[0] or explicit is None:
                log(f"Using reference audio: {candidate}")
            return candidate

    tried = "\n  ".join(str(c) for c in candidates)
    raise Abort(
        "No reference audio found. Synthesis clones from real in-game clips; see "
        "tools/README.md for extracting them with wow.export.\n\nLooked in:\n  "
        + tried
        + "\n\nPass --reference, or set QR_REFERENCE_AUDIO."
    )


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


def fetch_batch(site, key, destination, everything):
    query = "all=1&format=json" if everything else "format=json"
    url = f"{site}/api/export?{query}"
    log(f"Fetching {url}")

    request = urllib.request.Request(url, headers={"X-Api-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = "check SPEAKSTONE_KEY" if error.code == 401 else error.reason
        raise Abort(f"Speakstone returned HTTP {error.code} ({detail}).")
    except urllib.error.URLError as error:
        raise Abort(f"Could not reach {site}: {error.reason}")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        raise Abort("Speakstone did not return JSON. Wrong URL, or signed out?")

    destination.write_text(payload, encoding="utf-8")
    return data.get("passages", [])


# --------------------------------------------------------------------------
# Index rebuild, with the gate that matters
# --------------------------------------------------------------------------


def count_index_entries(path):
    if not path.exists():
        return 0
    return len(INDEX_ENTRY.findall(path.read_text(encoding="utf-8", errors="replace")))


def rebuild_index(sounds_dir, index_path, expected_new):
    """Rebuild SoundLengths.lua, refusing to shrink it.

    The index is real user data, not a build artifact: a clip missing from it
    is invisible to the addon even when the audio file exists. A rebuild that
    silently drops entries is worse than no rebuild, so this writes to a temp
    file, counts, and only moves it into place if the count went the right way.
    The backup is taken regardless, because being wrong about that is expensive.
    """
    before = count_index_entries(index_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = index_path.with_suffix(f".lua.backup-{stamp}")

    if index_path.exists():
        shutil.copy2(index_path, backup)
        log(f"Backed up index to {backup.name} ({before:,} entries)")

    temp = index_path.with_suffix(".lua.new")
    run([sys.executable, str(TOOLS / "build_soundlengths.py"), str(sounds_dir), "-o", str(temp)])

    after = count_index_entries(temp)
    log(f"\nIndex entries: {before:,} before, {after:,} rebuilt (+{after - before:,})")

    if after < before:
        temp.unlink(missing_ok=True)
        raise Abort(
            f"Rebuilt index has {before - after:,} FEWER entries than the current one. "
            f"Refusing to install it. The current index is untouched; the rebuild was "
            f"written nowhere. Investigate before retrying."
        )

    if expected_new and after < before + expected_new:
        log(
            f"  note: expected about {expected_new:,} new entries, got {after - before:,}. "
            f"Some clips may have failed to generate -- worth a look before publishing."
        )

    temp.replace(index_path)
    log(f"Installed rebuilt index ({after:,} entries).")
    return after


def tell_speakstone(site, sound_files):
    """POST to /api/voiced so the site stops offering what we just installed.

    Same auth as fetch_batch (X-Api-Key), no DB access or repo checkout
    needed on this machine -- the site does its own update.
    """
    key = os.environ.get("SPEAKSTONE_KEY")
    if not key:
        raise Abort("SPEAKSTONE_KEY is not set -- cannot tell Speakstone.")

    url = f"{site}/api/voiced"
    body = json.dumps({"soundFiles": sound_files}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = "check SPEAKSTONE_KEY" if error.code == 401 else error.reason
        raise Abort(f"POST {url} returned HTTP {error.code} ({detail}).")
    except urllib.error.URLError as error:
        raise Abort(f"Could not reach {site}: {error.reason}")

    log(f"  sent {result['received']:,}, matched {result['matchedPassages']:,} passage(s), "
        f"{result['unmatched']:,} unmatched")


def upload_for_audition(site, made, output_dir, batch_label):
    """POST each generated clip to the review queue instead of telling
    Speakstone it is voiced.

    One request per clip, not a batch endpoint -- /api/audition/upload takes
    raw audio as the body (same reasoning as fetch/tell: base64-in-JSON costs
    a third more bytes, multipart needs a parser for one part), so there is
    nowhere to put more than one clip per request anyway.
    """
    key = os.environ.get("SPEAKSTONE_KEY")
    if not key:
        raise Abort("SPEAKSTONE_KEY is not set -- cannot queue for audition.")

    for name in made:
        data = (output_dir / name).read_bytes()
        query = urllib.parse.urlencode({"file": name, "batch": batch_label})
        url = f"{site}/api/audition/upload?{query}"
        request = urllib.request.Request(
            url, data=data, method="POST",
            headers={"X-Api-Key": key, "Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = "check SPEAKSTONE_KEY" if error.code == 401 else error.reason
            raise Abort(f"POST {url} returned HTTP {error.code} ({detail}).")
        except urllib.error.URLError as error:
            raise Abort(f"Could not reach {site}: {error.reason}")

        # Still worth queuing even with no matching passage (the shipped
        # library predates Speakstone for some filenames) -- just say so,
        # the same way the upload route itself does.
        note = "" if result.get("knownPassage", True) else "  (no passage on the site for this file)"
        log(f"  queued {name}{note}")


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------

HISTORY = TOOLS / "run_history.json"


def record_run(passages, clips, seconds, characters):
    """Append this run's throughput to run_history.json.

    Scheduling decisions for phase 2 -- whether a batch is a coffee break or an
    overnight job, whether it needs checkpointing -- depend entirely on
    seconds-per-clip on the actual GPU, and guessing that number wrong designs
    the wrong system. So measure it every run and let the history accumulate
    rather than asking anyone to estimate.
    """
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "passages": passages,
        "clips": clips,
        "seconds": round(seconds, 1),
        "characters": characters,
        "seconds_per_clip": round(seconds / clips, 2) if clips else None,
        "chars_per_second": round(characters / seconds, 1) if seconds else None,
    }

    history = []
    if HISTORY.exists():
        try:
            history = json.loads(HISTORY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []
    history.append(entry)
    HISTORY.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return entry, history


def report_timing(entry, history):
    if not entry["seconds_per_clip"]:
        return
    log(f"\n  {entry['clips']:,} clip(s) in {entry['seconds']:.0f}s "
        f"-- {entry['seconds_per_clip']:.1f}s per clip")

    rates = [h["seconds_per_clip"] for h in history if h.get("seconds_per_clip")]
    if len(rates) > 1:
        average = sum(rates) / len(rates)
        log(f"  across {len(rates)} run(s), average {average:.1f}s per clip")
        # The number that actually answers "can this run unattended?"
        for size in (50, 250, 1000):
            minutes = size * average / 60
            shape = "minutes" if minutes < 30 else "hours" if minutes < 600 else "overnight"
            log(f"    {size:>5} clips ~ {minutes:>6.0f} min  ({shape})")


# --------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", default=DEFAULT_SITE)
    parser.add_argument("--input", type=Path,
                        help="use this passages.json instead of fetching")
    parser.add_argument("--reference", type=Path, default=None,
                        help="directory of reference clips; falls back to "
                             "$QR_REFERENCE_AUDIO then tools/reference-audio")
    parser.add_argument("--output", type=Path, default=TOOLS / "generated",
                        help="where synthesised audio lands (default tools/generated)")
    parser.add_argument("--voice-bank", type=Path, default=REPO / "voicebank.json")
    parser.add_argument("--sounds", type=Path, default=REPO / "Sounds")
    parser.add_argument("--index", type=Path, default=REPO / "SoundLengths.lua")
    parser.add_argument("--new-only", action="store_true",
                        help="only passages not pulled before (default: everything unvoiced)")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-synth", action="store_true")
    parser.add_argument("--install", action="store_true",
                        help="copy generated audio into Sounds/ and rebuild the index")
    parser.add_argument("--tell-speakstone", action="store_true",
                        help="after --install, POST to /api/voiced so newly "
                             "voiced passages stop being offered for export")
    parser.add_argument("--audition", action="store_true",
                        help="POST each generated clip to /api/audition/upload "
                             "for a human to listen to, instead of marking it "
                             "voiced -- mutually exclusive with --tell-speakstone")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report what would be synthesised, then stop")
    parser.add_argument("--watch", type=int, metavar="MINUTES",
                        help="keep running: check every MINUTES for new work "
                             "(implies --install once a batch is synthesised)")
    args = parser.parse_args()

    # These disagree about whether a clip has been checked: --audition holds
    # voiced_at back until a human approves it there, --tell-speakstone sets
    # voiced_at the moment the file lands on disk. Doing both would mean the
    # site claims a passage is done while its audio is still sitting in the
    # unreviewed queue -- refuse rather than let the last one silently win.
    if args.audition and args.tell_speakstone:
        raise Abort("--audition and --tell-speakstone contradict each other -- pick one.")

    # --watch is phase 2: leave it running on the machine with the GPU and it
    # picks work up whenever Speakstone has some. Kept in this script rather
    # than a separate daemon because everything it needs -- the gates, the
    # lock, the timing -- already lives here, and a second implementation of
    # any of that would be a second thing to get wrong.
    if args.watch:
        args.install = True
        log(f"Watching {args.site} every {args.watch} minute(s). Ctrl-C to stop.")
        while True:
            try:
                once(args)
            except Abort as error:
                # A bad batch must not kill an unattended watcher; the next
                # cycle may well succeed, and the gates mean nothing was
                # damaged by the failure.
                log(f"\nSTOPPED this cycle: {error}")
            log(f"\nSleeping {args.watch} minute(s)...\n")
            time.sleep(args.watch * 60)

    return once(args)


def once(args):

    started = time.time()
    batch_path = args.input or (TOOLS / "site_batch.json")

    # A second run while the first is mid-synthesis would fight over the same
    # output directory and index. Cheap to prevent, painful to debug.
    lock = TOOLS / ".run_batch.lock"
    if lock.exists():
        raise Abort(
            f"{lock.name} exists -- another run is in progress, or one crashed. "
            f"Delete it if you are sure nothing else is running."
        )
    lock.write_text(str(os.getpid()), encoding="utf-8")

    try:
        # ---- fetch -------------------------------------------------------
        if args.skip_fetch or args.input:
            if not batch_path.exists():
                raise Abort(f"{batch_path} does not exist and fetching was skipped.")
            passages = json.loads(batch_path.read_text(encoding="utf-8")).get("passages", [])
            log(f"Using existing {batch_path.name}: {len(passages):,} passage(s)")
        else:
            key = os.environ.get("SPEAKSTONE_KEY")
            if not key:
                raise Abort("SPEAKSTONE_KEY is not set. Export it, or pass --input.")
            passages = fetch_batch(args.site, key, batch_path, everything=not args.new_only)
            log(f"Fetched {len(passages):,} passage(s) into {batch_path.name}")

        if not passages:
            log("\nNothing to do -- no unvoiced passages waiting.")
            return 0

        speakers = {}
        for passage in passages:
            speakers[passage.get("npcName") or "(no speaker)"] = (
                speakers.get(passage.get("npcName") or "(no speaker)", 0) + 1
            )
        log(f"\n{len(speakers):,} distinct speaker(s). Largest:")
        for name, count in sorted(speakers.items(), key=lambda kv: -kv[1])[:8]:
            log(f"  {count:>4}  {name}")

        if args.dry_run:
            log("\n--dry-run: stopping before synthesis.")
            return 0

        # ---- synthesise --------------------------------------------------
        if not args.skip_synth:
            args.reference = resolve_reference(args.reference)
            args.output.mkdir(parents=True, exist_ok=True)
            before_files = {p.name for p in args.output.glob("*") if p.is_file()}

            command = [
                str(python_for_synthesis()), str(TOOLS / "synthesize.py"), str(batch_path),
                "--reference", str(args.reference),
                "-o", str(args.output),
            ]
            if args.voice_bank.exists():
                command += ["--voice-bank", str(args.voice_bank)]
            else:
                log(f"\nnote: {args.voice_bank.name} not found -- NPCs without their own "
                    f"reference audio will be reported rather than given a stand-in.")
            synth_started = time.time()
            run(command, cwd=TOOLS)
            synth_seconds = time.time() - synth_started

            after_files = {p.name for p in args.output.glob("*") if p.is_file()}
            made = sorted(after_files - before_files)
            log(f"\nSynthesised {len(made):,} new file(s) into {args.output}")

            if made:
                characters = sum(len(p.get("text") or "") for p in passages)
                entry, history = record_run(len(passages), len(made), synth_seconds, characters)
                report_timing(entry, history)
        else:
            made = sorted(p.name for p in args.output.glob("*") if p.is_file())
            log(f"\nSkipping synthesis; {len(made):,} file(s) already in {args.output}")

        # ---- convert -----------------------------------------------------
        # convert_library.py exits non-zero when there is nothing to convert,
        # which is not a failure here -- a rerun over an already-Ogg directory
        # is the normal --skip-synth path. Only call it when it has work.
        if made and any(args.output.glob("*.wav")):
            log("\nConverting genuine PCM to Ogg (already-compressed files are left alone)")
            run([sys.executable, str(TOOLS / "convert_library.py"), str(args.output), "--replace"])

        # ---- audition ------------------------------------------------------
        # Deliberately before --install: queuing for review is the point of
        # this flag, and it should work even on a run that never touches
        # Sounds/ (e.g. --audition without --install, to get a batch in front
        # of a human ear before deciding whether to ship it at all).
        if args.audition:
            if not made:
                log("\nNothing new to audition.")
            else:
                batch_label = batch_path.stem if args.input else datetime.now().strftime("%Y-%m-%d")
                log(f"\nQueuing {len(made):,} clip(s) for audition (batch={batch_label})...")
                upload_for_audition(args.site, made, args.output, batch_label)

        # ---- install -----------------------------------------------------
        if not args.install:
            log(
                f"\nDone in {time.time() - started:.0f}s. Nothing in Sounds/ or "
                f"SoundLengths.lua was touched.\n"
                f"\nListen to {args.output}, then:\n"
                f"  run_batch.py --install --skip-fetch --skip-synth\n"
            )
            return 0

        copied = 0
        installed = []
        args.sounds.mkdir(parents=True, exist_ok=True)
        for name in sorted(p.name for p in args.output.glob("*") if p.is_file()):
            source = args.output / name
            target = args.sounds / name
            # Never overwrite shipped audio silently. A name collision means
            # either a re-generation you meant, or a bug -- either way, say so.
            if target.exists():
                log(f"  exists, skipped: {name}")
                continue
            shutil.copy2(source, target)
            copied += 1
            installed.append(name)
        log(f"\nCopied {copied:,} new file(s) into {args.sounds.name}/")

        rebuild_index(args.sounds, args.index, expected_new=copied)

        if args.tell_speakstone:
            log(f"\nTelling Speakstone ({args.site})...")
            if installed:
                tell_speakstone(args.site, installed)
            else:
                log("  nothing new installed, nothing to send.")
            log(
                f"\nDone in {time.time() - started:.0f}s.\n"
                f"\nSpeakstone has been told. Still nothing committed here:\n"
                f"  git add -A && git commit   (review the diff first)\n"
            )
        else:
            log(
                f"\nDone in {time.time() - started:.0f}s.\n"
                f"\nNothing has been committed and Speakstone has not been told. Next:\n"
                f"  1. git add -A && git commit   (review the diff first)\n"
                f"  2. tell Speakstone what is now voiced:\n"
                f"     drag SoundLengths.lua into /admin/export, run the site's\n"
                f"     npm run import:library, or pass --tell-speakstone next time\n"
            )
        return 0

    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abort as error:
        print(f"\nSTOPPED: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
