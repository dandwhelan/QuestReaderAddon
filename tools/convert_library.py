#!/usr/bin/env python3
"""Re-encode the shipped WAV library as Ogg Vorbis.

The addon ships 5,237 clips as uncompressed 16-bit PCM, about 1.4 GB. The game
stores its own voice-over as Ogg Vorbis and PlaySoundFile reads it natively, so
the WAVs cost users a large download for no benefit. Re-encoding recovers most
of that: measured on the shipped audio, quality 4 is roughly an 88% reduction.

Size matters more here than in a normal audio library. Every past content drop
is retained in git history, so the repository is already several times the size
of the working tree, and each future patch of WAVs compounds it.

Conversion does not touch the originals unless --replace is given, and the
index the addon reads is rebuilt separately:

    convert_library.py ../Sounds --dry-run
    convert_library.py ../Sounds -o ../Sounds-ogg
    build_soundlengths.py ../Sounds-ogg -o ../SoundLengths.lua

Requires ffmpeg, either on PATH or via `pip install imageio-ffmpeg`.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys

# Vorbis quality for 24 kHz mono speech. 4 lands near 45 kbps and was
# indistinguishable from the source in listening; lower starts to add artefacts
# on sibilants, higher buys little for speech.
DEFAULT_QUALITY = 4


def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found.\n"
                 "Install it, or: pip install imageio-ffmpeg")


def human(count):
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.1f} {unit}"
        size /= 1024


def convert(job):
    ffmpeg, source, target, quality = job
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    result = subprocess.run(
        [ffmpeg, "-loglevel", "error", "-y", "-i", source,
         "-c:a", "libvorbis", "-q:a", str(quality), target],
        capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(target):
        return source, None, result.stderr.strip()[:160]
    return source, os.path.getsize(target), None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="directory of .wav files")
    parser.add_argument("-o", "--output",
                        help="where to write .ogg files (default: alongside)")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                        help=f"Vorbis quality 0-10 (default {DEFAULT_QUALITY})")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--replace", action="store_true",
                        help="delete each .wav once its .ogg is written")
    parser.add_argument("--dry-run", action="store_true",
                        help="estimate the saving from a sample, change nothing")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        sys.exit(f"Not a directory: {args.directory}")

    sources = sorted(
        os.path.join(root, name)
        for root, _, files in os.walk(args.directory)
        for name in files if name.lower().endswith(".wav"))
    if not sources:
        sys.exit(f"No .wav files in {args.directory}.")

    before = sum(os.path.getsize(s) for s in sources)
    print(f"{len(sources):,} file(s), {human(before)}.", file=sys.stderr)

    ffmpeg = find_ffmpeg()
    destination = args.output or args.directory

    def target_for(source):
        relative = os.path.relpath(source, args.directory)
        return os.path.join(destination, os.path.splitext(relative)[0] + ".ogg")

    if args.dry_run:
        # Convert a spread of the library to a scratch location and extrapolate,
        # rather than guessing a ratio that depends on the source material.
        sample = sources[::max(1, len(sources) // 12)][:12]
        import tempfile
        with tempfile.TemporaryDirectory() as scratch:
            jobs = [(ffmpeg, s, os.path.join(scratch, f"{i}.ogg"), args.quality)
                    for i, s in enumerate(sample)]
            results = [convert(j) for j in jobs]
        sampled_before = sum(os.path.getsize(s) for s, size, _ in results if size)
        sampled_after = sum(size for _, size, _ in results if size)
        if not sampled_after:
            sys.exit("Every sample conversion failed; check the ffmpeg install.")
        ratio = sampled_after / sampled_before
        print(f"\nSampled {len(sample)} file(s) at quality {args.quality}:",
              file=sys.stderr)
        print(f"  {human(sampled_before)} -> {human(sampled_after)} "
              f"({1 - ratio:.0%} smaller)", file=sys.stderr)
        print(f"\nProjected for the full library:", file=sys.stderr)
        print(f"  {human(before)} -> {human(before * ratio)} "
              f"(saves {human(before * (1 - ratio))})", file=sys.stderr)
        print(f"\nNothing written. Re-run without --dry-run to convert.",
              file=sys.stderr)
        return 0

    jobs = [(ffmpeg, s, target_for(s), args.quality) for s in sources]
    after, failures = 0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for done, (source, size, error) in enumerate(
                pool.map(convert, jobs), 1):
            if error is not None:
                failures.append((source, error))
                continue
            after += size
            if args.replace:
                os.remove(source)
            if done % 250 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)}", file=sys.stderr)

    converted = len(sources) - len(failures)
    print(f"\nConverted {converted:,} file(s).", file=sys.stderr)
    if after:
        print(f"  {human(before)} -> {human(after)} "
              f"({1 - after / before:.0%} smaller)", file=sys.stderr)
    for source, error in failures[:5]:
        print(f"  failed {os.path.basename(source)}: {error}", file=sys.stderr)
    if len(failures) > 5:
        print(f"  ... {len(failures) - 5} more failures", file=sys.stderr)

    print(f"\nRebuild the index so the addon can find the new files:\n"
          f"    python3 build_soundlengths.py {destination} "
          f"-o ../SoundLengths.lua", file=sys.stderr)
    if not args.replace:
        print(f"Originals kept. Remove them once the index is rebuilt and "
              f"playback is confirmed.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
