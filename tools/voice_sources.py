#!/usr/bin/env python3
"""Locate the in-game voice-over files that can be used as cloning references.

World of Warcraft stores creature speech under paths of the form

    sound/creature/<npc_name>/vo_<context>_<nn>.ogg

so the community listfile, which maps every FileDataID to its path, is enough to
find every recorded line for a given NPC without touching the game client. This
script builds that index and emits the FileDataID lists that wow.export consumes,
so the extraction step only pulls the files actually needed.

Usage:
    voice_sources.py index                      # fetch + cache the listfile index
    voice_sources.py lookup "Alleria Windrunner"
    voice_sources.py report npcs.txt            # reference-audio coverage report
    voice_sources.py manifest npcs.txt -o ids.txt

The listfile is ~150 MB and is cached after the first run.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

LISTFILE_URL = (
    "https://github.com/wowdev/wow-listfile/releases/latest/download/"
    "community-listfile.csv"
)
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
LISTFILE_PATH = os.path.join(CACHE_DIR, "community-listfile.csv")
INDEX_PATH = os.path.join(CACHE_DIR, "vo-index.json")

# sound/creature/<npc>/vo_....ogg  — the "vo_" prefix is what separates spoken
# dialogue from footsteps, aggro grunts and other incidental creature audio.
VO_PATTERN = re.compile(r"^sound/creature/([^/]+)/(vo_[^/]+\.ogg)$", re.IGNORECASE)

# vo_<patch>_<npc>_<nn>.ogg — the patch a line was recorded for.
PATCH_PATTERN = re.compile(r"^vo_(\d+)_", re.IGNORECASE)

# Enough reference audio for a zero-shot clone to hold the voice's character.
# Below this an NPC is better served by a fallback voice than a weak clone.
MIN_VIABLE_CLIPS = 5


def normalize(name):
    """Fold a display name into the listfile's folder convention.

    "Alleria Windrunner" and "First Arcanist Thalyssra" become
    "alleria_windrunner" and "first_arcanist_thalyssra".
    """
    name = name.strip().lower()
    name = re.sub(r"[''`]", "", name)
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_")


def download_listfile():
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"Downloading listfile from {LISTFILE_URL} ...", file=sys.stderr)
    urllib.request.urlretrieve(LISTFILE_URL, LISTFILE_PATH)
    size_mb = os.path.getsize(LISTFILE_PATH) / (1024 * 1024)
    print(f"  cached {size_mb:.0f} MB at {LISTFILE_PATH}", file=sys.stderr)


def build_index():
    """Parse the listfile into {npc_folder: [[file_data_id, filename], ...]}."""
    if not os.path.exists(LISTFILE_PATH):
        download_listfile()

    index = defaultdict(list)
    with open(LISTFILE_PATH, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\n").split(";", 1)
            if len(parts) != 2:
                continue
            file_data_id, path = parts
            match = VO_PATTERN.match(path)
            if match:
                index[match.group(1).lower()].append(
                    [int(file_data_id), match.group(2)]
                )

    for clips in index.values():
        clips.sort()

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as handle:
        json.dump(index, handle)

    total = sum(len(v) for v in index.values())
    print(f"Indexed {total:,} voice files across {len(index):,} NPCs.", file=sys.stderr)
    return index


def load_index(rebuild=False):
    if rebuild or not os.path.exists(INDEX_PATH):
        return build_index()
    with open(INDEX_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def tokens(name):
    return [t for t in normalize(name).split("_") if t]


def contiguous_run(needle, haystack):
    """True when `needle` appears as a run of whole tokens inside `haystack`.

    Matching on tokens rather than raw substrings keeps short folder names from
    colliding with unrelated NPCs — "bo" is a substring of "voidbound" but is
    not one of its tokens.
    """
    n, m = len(needle), len(haystack)
    if not n or n > m:
        return False
    return any(haystack[i:i + n] == needle for i in range(m - n + 1))


def resolve(index, name):
    """Find the listfile folder for an NPC name.

    Returns (folder, clips) on a confident match, or (None, candidates) when the
    name is ambiguous or absent. Titles and epithets differ between quest data
    and sound paths in both directions — quest data says "Xal'atath" where the
    folder is "xalatath_blade_of_the_black_empire", and says "Lady Jaina
    Proudmoore" where the folder may drop the title — so both containments are
    accepted, but only across whole tokens.
    """
    key = normalize(name)
    if key in index:
        return key, index[key]

    query = tokens(name)
    candidates = sorted(
        k for k in index
        if contiguous_run(query, tokens(k)) or contiguous_run(tokens(k), query)
    )

    if len(candidates) == 1:
        return candidates[0], index[candidates[0]]
    if len(candidates) > 1:
        # Prefer a folder that starts with the queried name over one that merely
        # contains it, which picks the character over same-named relatives.
        leading = [k for k in candidates if tokens(k)[:len(query)] == query]
        if len(leading) == 1:
            return leading[0], index[leading[0]]
        return None, candidates
    return None, []


def read_names(path):
    try:
        with open(path, encoding="utf-8") as handle:
            names = [ln.strip() for ln in handle
                     if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        sys.exit(
            f"No such file: {path}\n"
            f"To run against the NPCs voiced for a patch, skip the file "
            f"entirely:\n"
            f"    {os.path.basename(sys.argv[0])} {sys.argv[1]} "
            f"--patch 120 1200 1205 1207 121"
        )
    if not names:
        sys.exit(f"{path} contains no names.")
    return names


def cmd_index(args):
    build_index()
    return 0


def npcs_for_patches(index, patches, min_clips=0):
    """Rank the NPCs whose speech was recorded for particular patches.

    Voice files are named vo_<patch>_<npc>_<nn>.ogg, so the patch a line was
    recorded for is readable straight off the filename. That makes it possible
    to narrow 3,678 voiced NPCs down to the few hundred involved in recent
    content, without needing quest data first.

    Note the prefixes are not zero-padded consistently: 12.1 appears as "121"
    while 12.0.7 appears as "1207", so patches are matched as literal strings.
    """
    wanted = set(patches)
    counts = {}
    for folder, clips in index.items():
        hits = sum(1 for _, filename in clips
                   if (m := PATCH_PATTERN.match(filename)) and m.group(1) in wanted)
        if hits:
            counts[folder] = hits

    if not counts:
        sys.exit(f"No voice-over found for patch(es): {', '.join(patches)}")

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if min_clips:
        ranked = [(f, c) for f, c in ranked if c >= min_clips]
    return ranked


def select_names(index, args):
    """Resolve the NPC list a command should operate on.

    Accepts either a names file or --patch, so the common case of "the NPCs
    voiced for this patch" does not require generating an intermediate file
    first.
    """
    if getattr(args, "patch", None):
        return [folder.replace("_", " ")
                for folder, _ in npcs_for_patches(index, args.patch)]
    if getattr(args, "names_file", None):
        return read_names(args.names_file)
    sys.exit(
        "Give this command a list of NPCs, either way:\n"
        "  --patch 120 1200 1205 1207 121   (NPCs voiced for those patches)\n"
        "  npcs.txt                         (a file with one name per line)"
    )


def cmd_patch(args):
    index = load_index()
    ranked = npcs_for_patches(index, args.patches, args.min_clips)

    body = "\n".join(folder.replace("_", " ") for folder, _ in ranked)
    header = (f"# NPCs with voice-over recorded for patch(es) "
              f"{', '.join(args.patches)}\n"
              f"# Generated from the community listfile by voice_sources.py\n")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(header + body + "\n")
        print(f"Wrote {len(ranked)} NPC names to {args.output}.", file=sys.stderr)
    else:
        print(header + body)

    total = sum(c for _, c in ranked)
    print(f"{len(ranked)} NPCs, {total:,} voice files.", file=sys.stderr)
    return 0


def cmd_lookup(args):
    index = load_index()
    exit_code = 0
    for name in args.names:
        folder, clips = resolve(index, name)
        if folder is None:
            if clips:
                print(f"{name}: ambiguous, candidates: {', '.join(clips[:8])}")
            else:
                print(f"{name}: no voice-over found")
            exit_code = 1
            continue

        verdict = "viable" if len(clips) >= MIN_VIABLE_CLIPS else "below threshold"
        print(f"{name} -> sound/creature/{folder}/  ({len(clips)} clips, {verdict})")
        for file_data_id, filename in clips[: args.limit]:
            print(f"    {file_data_id:>9}  {filename}")
        if len(clips) > args.limit:
            print(f"    ... {len(clips) - args.limit} more")
    return exit_code


def cmd_report(args):
    index = load_index()
    names = select_names(index, args)

    viable, weak, missing = [], [], []
    for name in names:
        folder, clips = resolve(index, name)
        if folder is None:
            missing.append(name)
        elif len(clips) >= MIN_VIABLE_CLIPS:
            viable.append((name, len(clips)))
        else:
            weak.append((name, len(clips)))

    total = len(names)
    print(f"Reference audio for {total} NPCs\n")
    print(f"  viable  ({MIN_VIABLE_CLIPS}+ clips) : {len(viable):>4}"
          f"  {len(viable) / total:>5.0%}")
    print(f"  weak    (1-{MIN_VIABLE_CLIPS - 1} clips) : {len(weak):>4}"
          f"  {len(weak) / total:>5.0%}")
    print(f"  none                : {len(missing):>4}"
          f"  {len(missing) / total:>5.0%}")

    if weak:
        print("\nWeak — clone quality will suffer, consider a fallback voice:")
        for name, count in sorted(weak, key=lambda x: x[1]):
            print(f"  {count:>3}  {name}")
    if missing:
        print("\nNo voice-over — needs a fallback voice keyed by race/gender:")
        for name in missing:
            print(f"       {name}")
    return 0


def cmd_manifest(args):
    index = load_index()
    names = select_names(index, args)

    lines, skipped = [], []
    for name in names:
        folder, clips = resolve(index, name)
        if folder is None:
            skipped.append(name)
            continue
        for file_data_id, _ in clips[: args.per_npc]:
            lines.append(str(file_data_id))

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        out.write("\n".join(lines) + "\n")
    finally:
        if args.output:
            out.close()

    where = args.output or "stdout"
    print(f"Wrote {len(lines):,} FileDataIDs to {where}.", file=sys.stderr)
    if skipped:
        print(f"Skipped {len(skipped)} NPC(s) with no voice-over: "
              f"{', '.join(skipped[:5])}"
              f"{' ...' if len(skipped) > 5 else ''}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="download and cache the voice-over index")

    p_patch = sub.add_parser("patch", help="list NPCs voiced for given patches")
    p_patch.add_argument("patches", nargs="+",
                         help='patch prefixes as they appear in filenames, '
                              'e.g. 120 1205 1207 121 for Midnight')
    p_patch.add_argument("-o", "--output")
    p_patch.add_argument("--min-clips", type=int, default=0,
                         help="drop NPCs with fewer than this many clips")

    p_lookup = sub.add_parser("lookup", help="show the voice files for an NPC")
    p_lookup.add_argument("names", nargs="+")
    p_lookup.add_argument("--limit", type=int, default=10,
                          help="clips to list per NPC (default 10)")

    p_report = sub.add_parser("report", help="reference-audio coverage for a list of NPCs")
    p_report.add_argument("names_file", nargs="?",
                          help="file with one NPC name per line")
    p_report.add_argument("--patch", nargs="+", metavar="PATCH",
                          help="use the NPCs voiced for these patches instead")

    p_manifest = sub.add_parser("manifest", help="emit FileDataIDs for wow.export")
    p_manifest.add_argument("names_file", nargs="?",
                            help="file with one NPC name per line")
    p_manifest.add_argument("--patch", nargs="+", metavar="PATCH",
                            help="use the NPCs voiced for these patches instead")
    p_manifest.add_argument("-o", "--output")
    p_manifest.add_argument("--per-npc", type=int, default=40,
                            help="max clips per NPC (default 40; more than enough "
                                 "for zero-shot cloning)")

    args = parser.parse_args()
    return {
        "index": cmd_index,
        "patch": cmd_patch,
        "lookup": cmd_lookup,
        "report": cmd_report,
        "manifest": cmd_manifest,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
