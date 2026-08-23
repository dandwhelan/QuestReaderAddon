#!/usr/bin/env python3
"""Cap internal pauses in synthesized clips to improve listening experience.

F5-TTS synthesis leaves stochastic pauses between phrases. The pipeline's
encode step applies a silenceremove filter (stop_duration=0.45, detection=peak,
-40dB threshold) to new clips during generation, but the library of ~6,778
older clips was encoded before this fix. This tool applies the same filter to
the existing library.

The filter is applied at encode time (no GPU, no re-synthesis), only to clips
that actually have internal pauses over the threshold, leaving the rest
untouched. A second lossy pass (re-encoding to Ogg Vorbis q4) costs ~10%
quality, but only for files that need it; unaffected files stay at original
quality.

    cap_pauses.py ../Sounds --dry-run                # estimate impact
    cap_pauses.py ../Sounds -o ../Sounds-pauses      # write to new dir
    cp -r ../Sounds-pauses/. ../Sounds/              # merge back
    python build_soundlengths.py ../Sounds -o ../SoundLengths.lua

Be careful merging back: clips with pauses capped will have shorter durations,
so SoundLengths.lua MUST be rebuilt from the full merged library, not from
the output directory. See convert_library.py for why.

Requires ffmpeg, either on PATH or via `pip install imageio-ffmpeg`.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys


def is_mp3_in_wav(path):
    """True if .wav file contains MP3, not PCM.

    Files like this are already compressed and should not be re-encoded again.
    Sniffs the fmt chunk exactly like convert_library.py does.
    """
    if not path.lower().endswith('.wav'):
        return False

    try:
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
                    return fmt_tag != b"\x01\x00"
                handle.seek(size + (size & 1), os.SEEK_CUR)
    except Exception:
        return False


def get_duration(ffmpeg, path):
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip()) if result.stdout.strip() else None
    except (subprocess.TimeoutExpired, ValueError):
        return None


def has_removable_silence(ffmpeg, path, max_pause, threshold_db):
    """Quick check: does this file have silence over the threshold?

    Uses ffmpeg silencedetect filter. Returns True if any silence > max_pause
    is detected. Note: silencedetect output requires -loglevel info or higher.
    """
    try:
        result = subprocess.run(
            [ffmpeg, "-loglevel", "info", "-i", path,
             "-af", f"silencedetect=n={threshold_db}:d={max_pause}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=60)

        # If silencedetect found any, it will have "silence_end:" in stderr
        return "silence_end:" in result.stderr
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def cap_pauses_job(job):
    """Apply silence cap filter and re-encode.

    Returns: (source_path, target_path, src_size, tgt_size, src_duration,
              tgt_duration, error)
    """
    ffmpeg, source, target, max_pause, threshold_db, quality = job

    try:
        # Get source duration and size
        src_size = os.path.getsize(source)
        src_duration = get_duration(ffmpeg, source)
        if src_duration is None:
            return source, None, src_size, None, src_duration, None, "no duration"

        # Create output directory
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)

        # Apply silence cap and re-encode
        result = subprocess.run(
            [ffmpeg, "-loglevel", "error", "-y", "-i", source,
             "-af", f"silenceremove=stop_periods=-1:stop_duration={max_pause}"
                    f":stop_threshold={threshold_db}:detection=peak",
             "-c:a", "libvorbis", "-q:a", str(quality), target],
            capture_output=True, text=True, timeout=120)

        if result.returncode != 0 or not os.path.exists(target):
            return source, None, src_size, None, src_duration, None, \
                   result.stderr.strip()[:160] if result.stderr else "unknown error"

        # Get target duration and size
        tgt_size = os.path.getsize(target)
        tgt_duration = get_duration(ffmpeg, target)

        return source, target, src_size, tgt_size, src_duration, tgt_duration, None

    except subprocess.TimeoutExpired:
        return source, None, src_size, None, src_duration, None, "timeout"
    except Exception as e:
        return source, None, src_size, None, src_duration, None, str(e)[:160]


def find_ffmpeg():
    """Locate ffmpeg binary."""
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
    """Format byte count as human-readable string."""
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.1f} {unit}"
        size /= 1024


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="directory of .ogg/.wav files")
    parser.add_argument("-o", "--output",
                        help="write modified files here (default: alongside)")
    parser.add_argument("--max-pause", type=float, default=0.45,
                        help="silence cap threshold in seconds (default 0.45)")
    parser.add_argument("--threshold", default="-40dB",
                        help="silence detection threshold (default -40dB)")
    parser.add_argument("--quality", type=int, default=4,
                        help="Vorbis quality for re-encoding (default 4)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--replace", action="store_true",
                        help="delete each original once its pauses are capped")
    parser.add_argument("--dry-run", action="store_true",
                        help="estimate impact on a sample, change nothing")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        sys.exit(f"Not a directory: {args.directory}")

    # Find all audio files
    candidates = sorted(
        os.path.join(root, name)
        for root, _, files in os.walk(args.directory)
        for name in files if name.lower().endswith(('.ogg', '.wav')))

    if not candidates:
        sys.exit(f"No .ogg or .wav files in {args.directory}.")

    ffmpeg = find_ffmpeg()

    # Skip MP3-in-WAV files (already compressed; re-encoding is a second pass)
    to_process = [c for c in candidates if not is_mp3_in_wav(c)]
    mp3_in_wav = len(candidates) - len(to_process)

    print(f"{len(to_process):,} files eligible for pause capping.",
          file=sys.stderr)
    if mp3_in_wav:
        print(f"  ({mp3_in_wav:,} .wav files are already MP3-compressed "
              f"and are skipped to avoid second lossy pass.)",
              file=sys.stderr)

    # Check which ones actually have removable silence (dry-run: sample; real: check all)
    if args.dry_run:
        # Sample from the candidates
        sample = to_process[::max(1, len(to_process) // 12)][:12]
        print(f"\nScanning sample of {len(sample)} files for removable silence...",
              file=sys.stderr)
    else:
        print(f"\nScanning {len(to_process):,} files for removable silence...",
              file=sys.stderr)
        sample = to_process

    affected = []
    for i, path in enumerate(sample, 1):
        if has_removable_silence(ffmpeg, path, args.max_pause, args.threshold):
            affected.append(path)
        if args.dry_run and i % 5 == 0:
            print(f"  {i}/{len(sample)}", file=sys.stderr)

    affected_pct = len(affected) * 100 / len(sample) if sample else 0
    print(f"Found {len(affected)}/{len(sample)} files "
          f"({affected_pct:.0f}%) with removable silence.",
          file=sys.stderr)

    # Project to full candidate set
    if args.dry_run:
        projected_affected = int(len(affected) * len(to_process) / len(sample)) if sample else 0
        print(f"\nProjected for full library: "
              f"{projected_affected:,} of {len(to_process):,} files "
              f"({projected_affected*100/len(to_process):.1f}%) would be rewritten.",
              file=sys.stderr)

        if affected:
            # Test sample conversions on first few affected files
            print(f"\nSampling re-encoding impact on {min(3, len(affected))} clips...",
                  file=sys.stderr)

            import tempfile
            with tempfile.TemporaryDirectory() as scratch:
                jobs = []
                for j, path in enumerate(affected[:3]):
                    target = os.path.join(scratch, f"{j}.ogg")
                    jobs.append((ffmpeg, path, target, args.max_pause,
                               args.threshold, args.quality))

                before_size = 0
                after_size = 0
                before_dur = 0
                after_dur = 0

                for src, tgt, src_sz, tgt_sz, src_dur, tgt_dur, err in \
                        map(cap_pauses_job, jobs):
                    if err:
                        continue
                    before_size += src_sz
                    after_size += tgt_sz
                    before_dur += src_dur
                    after_dur += tgt_dur

                if before_size > 0:
                    print(f"  {human(before_size)} -> {human(after_size)} "
                          f"({1 - after_size/before_size:.0%} smaller)",
                          file=sys.stderr)
                    print(f"  {before_dur:.1f}s -> {after_dur:.1f}s "
                          f"({before_dur - after_dur:.1f}s removed)",
                          file=sys.stderr)

                    # Extrapolate
                    print(f"\nProjected for the full affected slice:", file=sys.stderr)
                    print(f"  {human(before_size * projected_affected / 3)} -> "
                          f"{human(after_size * projected_affected / 3)}",
                          file=sys.stderr)
                    print(f"  Saves ~{human((before_size - after_size) * projected_affected / 3)}",
                          file=sys.stderr)

        print(f"\nNothing written. Re-run without --dry-run to apply.",
              file=sys.stderr)
        return 0

    # Real run: rewrite files that need it
    destination = args.output or args.directory

    def target_for(source):
        relative = os.path.relpath(source, args.directory)
        return os.path.join(destination, os.path.splitext(relative)[0] + ".ogg")

    # Filter to only affected files
    sources = affected
    print(f"\nRewriting {len(sources):,} files with removable silence...",
          file=sys.stderr)

    jobs = [(ffmpeg, s, target_for(s), args.max_pause, args.threshold, args.quality)
            for s in sources]

    total_before = 0
    total_after = 0
    total_dur_before = 0
    total_dur_after = 0
    failures = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for done, (source, target, src_size, tgt_size, src_dur, tgt_dur, error) \
                in enumerate(pool.map(cap_pauses_job, jobs), 1):
            if error:
                failures.append((source, error))
                continue

            total_before += src_size
            total_after += tgt_size
            total_dur_before += src_dur
            total_dur_after += tgt_dur

            if args.replace:
                os.remove(source)

            if done % 250 == 0 or done == len(jobs):
                print(f"  {done:,}/{len(jobs):,}", file=sys.stderr)

    succeeded = len(sources) - len(failures)
    print(f"\nRewritten {succeeded:,} file(s).", file=sys.stderr)

    if total_after:
        print(f"  Size: {human(total_before)} -> {human(total_after)} "
              f"({1 - total_after / total_before:.0%} smaller)",
              file=sys.stderr)

    if total_dur_after:
        print(f"  Duration: {total_dur_before:.0f}s -> {total_dur_after:.0f}s "
              f"({total_dur_before - total_dur_after:.0f}s removed)",
              file=sys.stderr)

    for source, error in failures[:5]:
        print(f"  failed {os.path.basename(source)}: {error}", file=sys.stderr)
    if len(failures) > 5:
        print(f"  ... {len(failures) - 5} more failures", file=sys.stderr)

    if args.output:
        print(f"\nThe rewritten files are in {destination}, which holds only "
              f"the affected\nslice of the library -- do not index from it.\n"
              f"\nMerge them in, then rebuild the index:\n"
              f"    cp -r {destination}/. {args.directory}/\n"
              f"    python build_soundlengths.py {args.directory} "
              f"-o ../SoundLengths.lua\n"
              f"\nCheck the entry count goes up, not down. "
              f"Duration will change, so rebuild\nfrom the full merged library, "
              f"not from the output directory.",
              file=sys.stderr)
    else:
        print(f"\nRebuild the index (duration has changed):\n"
              f"    python build_soundlengths.py {args.directory} "
              f"-o ../SoundLengths.lua",
              file=sys.stderr)

    if not args.replace:
        print(f"\nOriginals kept. Remove them once the index is rebuilt and "
              f"you have confirmed\nplayback is correct in game.",
              file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
