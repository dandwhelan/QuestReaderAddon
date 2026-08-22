#!/usr/bin/env python3
"""Regenerate SoundLengths.lua from a directory of generated audio.

SoundLengths.lua is the index the addon consults before playing anything. A
file missing from it is invisible to the addon even when it is present on disk,
so this has to be rebuilt whenever audio is added — it is the last step of
generation, not an optional one.

Durations are read from the audio headers directly, so no ffmpeg or other
external tool is required. Ogg Vorbis and PCM WAV are both understood.

Usage:
    build_soundlengths.py ./generated -o ../SoundLengths.lua
    build_soundlengths.py ./generated --variable QuestReaderTWWSounds -o out.lua
    build_soundlengths.py ./generated --check
"""

import argparse
import os
import re
import struct
import sys

# Quest audio: "<questID>_<description|progress|completion>.<ext>". Gossip
# uses "npc<npcID>_gossip<n>.<ext>", items use "item<itemID>_page<n>.<ext>",
# and world objects use "item_<slug>_page<n>.<ext>".
AUDIO_NAME = re.compile(
    r"^([a-z0-9_]+)_(description|progress|completion|gossip\d+|page\d+)\.(ogg|wav)$",
    re.IGNORECASE)


class AudioError(Exception):
    pass


def ogg_duration(path):
    """Seconds of an Ogg Vorbis file.

    The sample rate comes from the identification header; the total sample
    count is the granule position on the final page. Reading the tail avoids
    walking the whole file.
    """
    with open(path, "rb") as handle:
        head = handle.read(8192)
        marker = head.find(b"\x01vorbis")
        if marker < 0:
            raise AudioError("not an Ogg Vorbis stream")
        sample_rate = struct.unpack_from("<I", head, marker + 12)[0]
        if not sample_rate:
            raise AudioError("zero sample rate")

        size = os.path.getsize(path)
        handle.seek(max(0, size - 65536))
        tail = handle.read()
        page = tail.rfind(b"OggS")
        if page < 0:
            raise AudioError("no Ogg page in tail")
        granule = struct.unpack_from("<Q", tail, page + 6)[0]
        return granule / sample_rate


def wav_duration(path):
    """Seconds of a PCM WAV file, from the fmt and data chunk headers."""
    with open(path, "rb") as handle:
        if handle.read(4) != b"RIFF":
            raise AudioError("not a RIFF file")
        handle.seek(8)
        if handle.read(4) != b"WAVE":
            raise AudioError("not a WAVE file")

        byte_rate = None
        while True:
            header = handle.read(8)
            if len(header) < 8:
                raise AudioError("no data chunk")
            chunk_id, chunk_size = struct.unpack("<4sI", header)
            if chunk_id == b"fmt ":
                fmt = handle.read(chunk_size)
                byte_rate = struct.unpack_from("<I", fmt, 8)[0]
            elif chunk_id == b"data":
                if not byte_rate:
                    raise AudioError("data chunk precedes fmt chunk")
                return chunk_size / byte_rate
            else:
                handle.seek(chunk_size + (chunk_size & 1), os.SEEK_CUR)


MPEG_RATES = {  # version, layer -> bitrate table index
    (3, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (3, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (3, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
MPEG_SAMPLE_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000],
                     0: [11025, 12000, 8000]}


def mp3_duration(path):
    """Seconds of an MPEG audio file.

    Much of the shipped library is MP3 carrying a .wav extension — the game
    sniffs content rather than trusting the extension, so it plays regardless.
    Duration is computed from the first frame header, which is accurate for the
    constant-bitrate encodes used here.
    """
    with open(path, "rb") as handle:
        data = handle.read()

    offset = 0
    if data[:3] == b"ID3":
        # Syncsafe integer: seven bits per byte.
        size = 0
        for byte in data[6:10]:
            size = (size << 7) | (byte & 0x7F)
        offset = 10 + size

    limit = min(len(data) - 4, offset + 200000)
    while offset < limit:
        if data[offset] == 0xFF and (data[offset + 1] & 0xE0) == 0xE0:
            header = data[offset:offset + 4]
            version = (header[1] >> 3) & 0x03
            layer = (header[1] >> 1) & 0x03
            bitrate_index = (header[2] >> 4) & 0x0F
            rate_index = (header[2] >> 2) & 0x03
            layer_number = {1: 3, 2: 2, 3: 1}.get(layer)
            rates = MPEG_RATES.get((version, layer_number))
            if (rates and 0 < bitrate_index < 15 and rate_index < 3
                    and version in MPEG_SAMPLE_RATES):
                bitrate = rates[bitrate_index] * 1000
                if bitrate:
                    return (len(data) - offset) * 8 / bitrate
        offset += 1
    raise AudioError("no MPEG frame header found")


def duration(path):
    """Dispatch on content, not extension — the two disagree in this library."""
    with open(path, "rb") as handle:
        magic = handle.read(4)
    if magic[:4] == b"OggS":
        return ogg_duration(path)
    if magic[:4] == b"RIFF":
        return wav_duration(path)
    if magic[:3] == b"ID3" or (magic[0] == 0xFF and (magic[1] & 0xE0) == 0xE0):
        return mp3_duration(path)
    raise AudioError(f"unrecognised audio container {magic!r}")


def scan(directory):
    """Collect {filename: seconds} for every well-named clip, plus problems."""
    lengths, skipped, broken = {}, [], []
    for root, _, files in os.walk(directory):
        for name in sorted(files):
            if not name.lower().endswith((".ogg", ".wav")):
                continue
            if not AUDIO_NAME.match(name):
                # The addon looks up "<questID>_<passage>", so anything else
                # can never be found and is worth surfacing rather than hiding.
                skipped.append(name)
                continue
            try:
                lengths[name] = round(duration(os.path.join(root, name)), 2)
            except (AudioError, struct.error, OSError) as exc:
                broken.append((name, str(exc)))
    return lengths, skipped, broken


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="directory of generated audio")
    parser.add_argument("-o", "--output")
    parser.add_argument("--variable", default="QuestReaderSoundLengths",
                        help="global the addon reads (default "
                             "QuestReaderSoundLengths)")
    parser.add_argument("--check", action="store_true",
                        help="report what would be written without writing")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        sys.exit(f"Not a directory: {args.directory}")

    lengths, skipped, broken = scan(args.directory)

    if not lengths:
        sys.exit(f"No usable audio in {args.directory}.\n"
                 f"Files must be named <questID>_<passage>.ogg, where passage "
                 f"is description, progress or completion.")

    total = sum(lengths.values())
    print(f"Indexed {len(lengths):,} clip(s), {total / 3600:.1f} hours.",
          file=sys.stderr)
    if skipped:
        print(f"  Ignored {len(skipped)} misnamed file(s) the addon could "
              f"never find: {', '.join(skipped[:5])}"
              f"{' ...' if len(skipped) > 5 else ''}", file=sys.stderr)
    for name, reason in broken:
        print(f"  Unreadable: {name} ({reason})", file=sys.stderr)

    if args.check:
        return 1 if broken else 0

    body = "".join(f'    ["{name}"] = {seconds:.2f},\n'
                   for name, seconds in sorted(lengths.items()))
    lua = f"{args.variable} = {{\n{body}}}\n"

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        out.write(lua)
    finally:
        if args.output:
            out.close()

    if args.output:
        print(f"Wrote {args.output}.", file=sys.stderr)
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
