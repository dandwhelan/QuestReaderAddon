#!/usr/bin/env python3
"""Generate quest voiceovers from harvested text and extracted reference audio.

Takes the passages produced by wowhead_quests.py or harvest_export.py, matches
each speaker to the reference clips extracted for that NPC, and synthesizes one
audio file per passage named as the addon expects: <questID>_<passage>.ogg.

The default engine is Coqui XTTS-v2, which clones zero-shot from a few seconds
of reference audio and outputs at 24 kHz — the same rate as the audio the addon
already ships. It needs a GPU to be practical and is imported only when
generation actually runs, so planning works on any machine.

Work is resumable. Existing output files are skipped, so an interrupted run
continues where it stopped rather than regenerating everything.

Usage:
    synthesize.py passages.json --reference ./reference-audio --dry-run
    synthesize.py passages.json --reference ./reference-audio -o ./generated
    synthesize.py passages.json --reference ./ref -o ./gen --only 95300,95301
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_sources import normalize as fold_name

# Clips used to condition the clone. More does not reliably improve the voice,
# and every extra second is loaded per NPC.
REFERENCE_CLIPS = 6
XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


def index_reference(directory):
    """Map folded NPC name -> list of reference clips.

    wow.export preserves the sound/creature/<npc>/ layout, so each leaf
    directory is one NPC's voice.
    """
    voices = {}
    for root, _, files in os.walk(directory):
        clips = sorted(os.path.join(root, f) for f in files
                       if f.lower().endswith((".ogg", ".wav", ".mp3")))
        if clips:
            voices.setdefault(fold_name(os.path.basename(root)), []).extend(clips)
    return voices


def load_voice_bank(path, voices):
    """Map each NPC to its group's donor clips.

    Produces {folded npc name: clips} covering every NPC in the bank, not only
    those the bank flagged. The bank records which NPCs have no audio *in the
    game*; synthesis needs a stand-in whenever audio was not *extracted here*,
    which is a larger set. The donor is only consulted after an NPC's own clips
    fail to resolve, so offering one for everybody costs nothing.
    """
    with open(path, encoding="utf-8") as handle:
        bank = json.load(handle)

    fallback, unusable = {}, set()
    for group_name, group in bank.items():
        donor = group.get("donor")
        clips = voices.get(donor["folder"]) if donor else None
        if not clips:
            # The bank names a donor whose audio was never extracted; recording
            # it as unusable is more honest than silently leaving NPCs unvoiced.
            if donor:
                unusable.add(f"{group_name} (donor {donor['folder']})")
            continue
        for member in group.get("members", []):
            if member.get("npcName"):
                fallback[fold_name(member["npcName"])] = clips
    return fallback, unusable


def match_voice(voices, npc_name, fallback=None):
    """Find an NPC's reference clips, tolerating title differences."""
    if not npc_name:
        return None
    key = fold_name(npc_name)
    if key in voices:
        return voices[key]
    # Sound folders often carry an epithet the quest data omits, and the
    # reverse. Accept a unique containment either way.
    candidates = [k for k in voices if key in k.split("_") or key in k]
    if len(candidates) == 1:
        return voices[candidates[0]]
    # No audio of its own: fall back to the donor for this NPC's race and sex.
    if fallback:
        return fallback.get(key)
    return None


def encode_ogg(wav_path, ogg_path, quality=4):
    """Re-encode to Ogg Vorbis, which is what the game and addon both use."""
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", wav_path,
         "-c:a", "libvorbis", "-q:a", str(quality), ogg_path],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[:200]}")
    os.remove(wav_path)


def load_engine(device):
    try:
        from TTS.api import TTS
    except ImportError:
        sys.exit(
            "Coqui TTS is not installed, so audio cannot be generated.\n"
            "    pip install TTS\n"
            "Use --dry-run to plan the work without it.")
    print(f"Loading {XTTS_MODEL} on {device} ...", file=sys.stderr)
    return TTS(XTTS_MODEL).to(device)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("passages", help="passages.json")
    parser.add_argument("--reference", required=True,
                        help="directory of extracted reference audio")
    parser.add_argument("-o", "--output", default="./generated")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the plan without generating")
    parser.add_argument("--only", help="comma-separated quest IDs")
    parser.add_argument("--device", default="cuda",
                        help="cuda or cpu (default cuda)")
    parser.add_argument("--language", default="en")
    parser.add_argument("--keep-wav", action="store_true",
                        help="skip Ogg encoding and leave WAV output")
    parser.add_argument("--voice-bank",
                        help="voicebank.json, giving NPCs without reference "
                             "audio a stand-in voice for their race and sex")
    args = parser.parse_args()

    try:
        with open(args.passages, encoding="utf-8") as handle:
            passages = json.load(handle)["passages"]
    except FileNotFoundError:
        sys.exit(f"No such file: {args.passages}\n"
                 f"Produce it with wowhead_quests.py or harvest_export.py.")
    except (KeyError, json.JSONDecodeError) as exc:
        sys.exit(f"Could not read {args.passages}: {exc}")

    if not os.path.isdir(args.reference):
        sys.exit(f"No such directory: {args.reference}\n"
                 f"Extract reference audio first; see the README.")

    voices = index_reference(args.reference)
    if not voices:
        sys.exit(f"No audio found under {args.reference}.")
    print(f"{len(voices):,} voice(s) available in {args.reference}.",
          file=sys.stderr)

    fallback, unusable = {}, set()
    if args.voice_bank:
        try:
            fallback, unusable = load_voice_bank(args.voice_bank, voices)
        except FileNotFoundError:
            sys.exit(f"No such file: {args.voice_bank}\n"
                     f"Build one with voice_bank.py.")
        print(f"Voice bank covers {len(fallback):,} NPC(s) with a stand-in.",
              file=sys.stderr)
        for entry in sorted(unusable)[:5]:
            print(f"  donor audio not extracted for {entry}", file=sys.stderr)

    wanted = None
    if args.only:
        wanted = {q.strip() for q in args.only.split(",") if q.strip()}

    os.makedirs(args.output, exist_ok=True)

    planned, done, novoice = [], 0, {}
    for passage in passages:
        quest_id = str(passage["questID"])
        if wanted and quest_id not in wanted:
            continue
        if not passage.get("text"):
            continue

        name = f"{quest_id}_{passage['passage']}"
        target = os.path.join(args.output,
                              name + (".wav" if args.keep_wav else ".ogg"))
        if os.path.exists(target):
            done += 1
            continue

        clips = match_voice(voices, passage.get("npcName"), fallback)
        if not clips:
            # Without reference audio there is nothing to clone from. These
            # need a fallback voice decided per NPC, not a silent skip.
            novoice.setdefault(passage.get("npcName") or "(no NPC)", 0)
            novoice[passage.get("npcName") or "(no NPC)"] += 1
            continue

        planned.append((target, passage["text"], clips[:REFERENCE_CLIPS],
                        passage.get("npcName")))

    print(f"\n  to generate : {len(planned):>5}", file=sys.stderr)
    print(f"  already done: {done:>5}", file=sys.stderr)
    print(f"  no voice    : {sum(novoice.values()):>5}"
          f" across {len(novoice)} NPC(s)", file=sys.stderr)

    if novoice:
        print("\nNPCs with no reference audio, needing a fallback voice:",
              file=sys.stderr)
        for npc, count in sorted(novoice.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {count:>4}  {npc}", file=sys.stderr)

    if args.dry_run:
        print("\nDry run; nothing written.", file=sys.stderr)
        return 0
    if not planned:
        print("\nNothing to do.", file=sys.stderr)
        return 0

    engine = load_engine(args.device)
    failures = 0
    for position, (target, text, clips, npc) in enumerate(planned, 1):
        wav_target = target[:-4] + ".wav" if target.endswith(".ogg") else target
        try:
            engine.tts_to_file(text=text, speaker_wav=clips,
                               language=args.language, file_path=wav_target)
            if not args.keep_wav:
                encode_ogg(wav_target, target)
        except Exception as exc:  # engine and codec failures are both fatal
            failures += 1                                # to this clip only
            print(f"  failed {os.path.basename(target)} ({npc}): {exc}",
                  file=sys.stderr)
            continue
        if position % 25 == 0 or position == len(planned):
            print(f"  {position}/{len(planned)}", file=sys.stderr)

    generated = len(planned) - failures
    print(f"\nGenerated {generated:,} clip(s) into {args.output}.",
          file=sys.stderr)
    if failures:
        print(f"{failures} failed; rerun to retry only those.", file=sys.stderr)
    print(f"Rebuild the addon index next:\n"
          f"    python3 build_soundlengths.py {args.output} "
          f"-o ../SoundLengths.lua", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
