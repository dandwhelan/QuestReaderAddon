#!/usr/bin/env python3
"""List the quests that have no audio yet.

This is the start of the pipeline: everything downstream operates on the quest
IDs it produces. It compares every quest in the game against the ones the addon
already voices, and writes the difference.

The game's full quest list comes from the QuestV2 client table, published as
CSV by wago.tools. Coverage comes from SoundLengths.lua, the index the addon
consults before playing anything — a file absent from it is invisible to the
addon even if it is on disk, so it is the authoritative record of what exists.

Most quest IDs are internal or technical entries that no player ever sees, so
the raw difference is far larger than the real workload. --min-id narrows it to
recent content, which is usually what you want: quest IDs increase over time, so
the highest ID the addon already covers is a reasonable floor for "new since it
was last updated".

Usage:
    missing_quests.py -o missing.txt
    missing_quests.py --min-id 95245 -o missing.txt
    missing_quests.py --stats
"""

import argparse
import os
import re
import shutil
import sys
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
QUESTV2_URL = "https://wago.tools/db2/QuestV2/csv"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
QUESTV2_PATH = os.path.join(CACHE_DIR, "QuestV2.csv")
DEFAULT_SOUNDLENGTHS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "SoundLengths.lua")

# ["<questID>_<passage>.wav"] = <seconds>,
COVERED = re.compile(r'\["(\d+)_[a-z]+\.\w+"\]')


def download(url, path):
    """Fetch to a file, replacing it only once complete.

    wago.tools serves 403 to urllib's default User-Agent, so one has to be
    set. Writing through a temporary also keeps an interrupted download from
    leaving a truncated cache that later runs would reuse without noticing.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = path + ".part"
    with urllib.request.urlopen(request, timeout=120) as response, \
            open(partial, "wb") as handle:
        shutil.copyfileobj(response, handle)
    os.replace(partial, path)


def load_covered(path):
    """Quest IDs the addon already has audio for."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return {int(m) for m in COVERED.findall(handle.read())}
    except FileNotFoundError:
        sys.exit(f"No such file: {path}\n"
                 f"Expected the addon's SoundLengths.lua. Pass its location "
                 f"with --sound-lengths if it is not beside this tool.")


def load_all_quests(refresh=False):
    """Every quest ID in the client, from the published QuestV2 table."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if refresh or not os.path.exists(QUESTV2_PATH):
        print(f"Downloading QuestV2 from {QUESTV2_URL} ...", file=sys.stderr)
        download(QUESTV2_URL, QUESTV2_PATH)

    quest_ids = set()
    with open(QUESTV2_PATH, encoding="utf-8", errors="replace") as handle:
        next(handle, None)  # header
        for line in handle:
            field = line.split(",", 1)[0]
            if field.isdigit():
                quest_ids.add(int(field))
    return quest_ids


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output")
    parser.add_argument("--sound-lengths", default=DEFAULT_SOUNDLENGTHS,
                        help="path to the addon's SoundLengths.lua")
    parser.add_argument("--min-id", type=int,
                        help="only quests above this ID; omit to use the "
                             "highest ID already covered")
    parser.add_argument("--all", action="store_true",
                        help="every uncovered quest, including old expansions")
    parser.add_argument("--refresh", action="store_true",
                        help="redownload QuestV2 instead of using the cache")
    parser.add_argument("--stats", action="store_true",
                        help="summarise coverage without writing a list")
    args = parser.parse_args()

    covered = load_covered(args.sound_lengths)
    if not covered:
        sys.exit(f"No quest IDs found in {args.sound_lengths}.")
    all_quests = load_all_quests(args.refresh)

    high_water = max(covered)
    floor = None if args.all else (args.min_id if args.min_id is not None
                                   else high_water)

    missing = sorted(q for q in all_quests
                     if q not in covered and (floor is None or q > floor))

    print(f"Quests in client        : {len(all_quests):>7,}", file=sys.stderr)
    print(f"Voiced by the addon     : {len(covered):>7,}", file=sys.stderr)
    print(f"Highest voiced quest ID : {high_water:>7,}", file=sys.stderr)
    if floor is not None:
        print(f"Counting quests above   : {floor:>7,}", file=sys.stderr)
    print(f"Missing audio           : {len(missing):>7,}", file=sys.stderr)

    if args.stats:
        # Where the gap sits, so a patch's worth can be tackled at a time.
        print("\nBy ID block:", file=sys.stderr)
        blocks = {}
        for quest_id in missing:
            blocks[quest_id // 1000 * 1000] = \
                blocks.get(quest_id // 1000 * 1000, 0) + 1
        for block in sorted(blocks):
            if blocks[block] >= 5:
                print(f"  {block:>6,}-{block + 999:<6,} {blocks[block]:>5}",
                      file=sys.stderr)
        return 0

    if not missing:
        print("\nNothing missing. The addon covers every quest above the "
              "floor.", file=sys.stderr)
        return 0

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        out.write("\n".join(str(q) for q in missing) + "\n")
    finally:
        if args.output:
            out.close()

    if args.output:
        print(f"\nWrote {len(missing):,} quest ID(s) to {args.output}.",
              file=sys.stderr)
        print(f"Not all will be real player-facing quests; the next step "
              f"filters them:\n"
              f"    python3 wowhead_quests.py --ids {args.output} "
              f"-o passages.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
