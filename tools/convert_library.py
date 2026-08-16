#!/usr/bin/env python3
"""Re-encode the shipped library's genuine PCM as Ogg Vorbis.

The library is not what its filenames suggest. Of 5,238 files carrying a
.wav extension, 71% (4,449 files, 962 MB) are already MP3 inside a .wav
wrapper -- the game sniffs content rather than trusting the extension, so
they have always played correctly. Only the remaining 29% (788 files,
392 MB) is genuine PCM. Content is sniffed here the same way, so only that
slice is ever re-encoded: the rest is already compressed, and putting it
through Vorbis too would decode-and-recompress audio that does not need it,
trading quality for bytes that were mostly not there to save. Measured on
the genuine-PCM slice, quality 4 is roughly an 88% reduction.

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


def is_genuine_pcm(path):
    """True only for real PCM WAV, never for MP3 wearing a .wav name.

    A RIFF/WAVE header is necessary but not sufficient: some files in this
    library carry that header around an MP3 stream (a WAVE_FORMAT_MPEGLAYER3
    fmt chunk), and re-encoding those would still be a second lossy pass.
    The two magic-byte families that mean "already compressed" are checked
    for directly, the same way build_soundlengths.py dispatches on content.
    """
    with open(path, "rb") as handle:
        head = handle.read(4)
        if head != b"RIFF":
            return False
        handle.seek(8)
        if handle.read(4) != b"WAVE":
            return False
        # WAVE_FORMAT_PCM is 0x0001, little-endian, two bytes into the fmt
        # chunk's body; anything else (0x0055 = MP3, among others) is not.
        while True:
            chunk = handle.read(8)
            if len(chunk) < 8:
                return False
            chunk_id, size = chunk[:4], int.from_bytes(chunk[4:8], "little")
            if chunk_id == b"fmt ":
                fmt_tag = handle.read(2)
                return fmt_tag == b"\x01\x00"
            handle.seek(size + (size & 1), os.SEEK_CUR)


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

    candidates = sorted(
        os.path.join(root, name)
        for root, _, files in os.walk(args.directory)
        for name in files if name.lower().endswith(".wav"))
    if not candidates:
        sys.exit(f"No .wav files in {args.directory}.")

    sources = [s for s in candidates if is_genuine_pcm(s)]
    already_compressed = len(candidates) - len(sources)
    if not sources:
        sys.exit(f"All {len(candidates):,} .wav file(s) in {args.directory} "
                 f"are already compressed audio in a .wav wrapper. "
                 f"Nothing to convert.")

    before = sum(os.path.getsize(s) for s in sources)
    print(f"{len(sources):,} genuine-PCM file(s), {human(before)}.",
          file=sys.stderr)
    if already_compressed:
        print(f"  ({already_compressed:,} more .wav file(s) are already "
              f"compressed audio and are left alone.)", file=sys.stderr)

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
