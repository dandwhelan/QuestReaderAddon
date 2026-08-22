#!/usr/bin/env python3
"""Choose a stand-in voice for every NPC that has none of its own.

Roughly a fifth of quest givers have no recorded speech to clone from. Rather
than invent a voice for them, this picks a donor: a well-recorded NPC of the
same race and sex, whose clips stand in for everyone in that group. A generic
Blood Elf guard then sounds like a Blood Elf rather than like whatever voice
happened to be assigned.

Donors are ranked by how much reference audio they have, since a donor's voice
is reused across many NPCs and a weak clone propagates everywhere. Groups with
no viable donor are reported rather than filled with something unsuitable —
those need a human decision.

Usage:
    voice_bank.py --names npcs.txt -o voicebank.json
    voice_bank.py --patch 120 1200 1205 1207 121 -o voicebank.json
    voice_bank.py --names npcs.txt --report
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import npc_traits
import voice_sources

# A donor's voice is reused across a whole group, so hold it to a higher bar
# than an NPC voicing only itself.
MIN_DONOR_CLIPS = 15


def generic_pools(index):
    """The game's own generic voice sets, keyed by the words in their name.

    Blizzard already ships ~160 pools built for exactly this purpose —
    generic_amani_civilian, generic_blood_elf_citizen_a — voiced as anonymous
    members of a race or faction. They make better stand-ins than a named
    character, whose voice is recognisable and carries their personality.
    """
    pools = {}
    for folder, clips in index.items():
        if "generic" in folder.split("_") and len(clips) >= MIN_DONOR_CLIPS:
            words = {w for w in folder.split("_")
                     if w not in ("generic", "a", "b")}
            pools[folder] = (words, clips)
    return pools


def match_pool(pools, race, sex):
    """Best generic pool for a race, preferring one matching the sex too."""
    if not race:
        return None
    wanted = {w for w in race.lower().replace("'", "").split() if w}
    if not wanted:
        return None

    scored = []
    for folder, (words, clips) in pools.items():
        # Every word of the race must appear. Matching on any overlap put
        # night elves on a blood elf pool, because both contain "elf".
        if not wanted <= words:
            continue
        # A pool naming the sex is a closer fit than one that does not.
        sexed = 1 if sex and sex in words else 0
        # Fewer extra words means a less specialised, more neutral pool.
        generality = -len(words - wanted)
        scored.append((sexed, generality, len(clips), folder, clips))
    if not scored:
        return None
    scored.sort(reverse=True)
    _, _, count, folder, clips = scored[0]
    return folder, count, clips


def build(names, index, tables, pools):
    """Group NPCs by race and sex, and pick the best donor in each group."""
    groups = {}
    unresolved = []

    for name in names:
        traits = npc_traits.resolve(name, tables)
        if not traits:
            unresolved.append(name)
            continue

        folder, clips = voice_sources.resolve(index, name)
        clip_count = len(clips) if folder else 0

        group = groups.setdefault(traits["voiceGroup"], {
            "race": traits["race"], "sex": traits["sex"],
            "members": [], "candidates": [],
        })
        group["members"].append({
            "npcName": traits["npcName"],
            "npcID": traits["npcID"],
            "clips": clip_count,
            "needsFallback": clip_count < voice_sources.MIN_VIABLE_CLIPS,
        })
        if folder and clip_count >= MIN_DONOR_CLIPS:
            group["candidates"].append((clip_count, folder, traits["npcName"],
                                        clips))

    for group in groups.values():
        # Prefer one of the game's anonymous pools; fall back to the best
        # recorded NPC in the group only when no pool matches the race.
        pool = match_pool(pools, group["race"], group["sex"])
        if pool:
            folder, count, clips = pool
            source, donor_name = "generic pool", folder.replace("_", " ")
        else:
            group["candidates"].sort(key=lambda c: -c[0])
            if group["candidates"]:
                count, folder, donor_name, clips = group["candidates"][0]
                source = "named NPC"
            else:
                folder = None

        group["donor"] = None if not folder else {
            "npcName": donor_name,
            "folder": folder,
            "source": source,
            "clips": count,
            "referencePaths": [f"sound/creature/{folder}/{f}"
                               for _, f in clips[:8]],
        }
        del group["candidates"]

    return groups, unresolved


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--names", dest="names_file",
                        help="file with one NPC name per line")
    parser.add_argument("--patch", nargs="+",
                        help="use the NPCs voiced for these patches")
    parser.add_argument("-o", "--output")
    parser.add_argument("--report", action="store_true",
                        help="print a summary instead of writing the bank")
    args = parser.parse_args()

    index = voice_sources.load_index()

    if args.patch:
        names = [folder.replace("_", " ") for folder, _ in
                 voice_sources.npcs_for_patches(index, args.patch)]
    elif args.names_file:
        names = voice_sources.read_names(args.names_file)
    else:
        sys.exit("Give --names <file> or --patch <patch...>.")

    tables = npc_traits.load_tables()
    pools = generic_pools(index)
    print(f"{len(pools)} generic voice pool(s) available as stand-ins.",
          file=sys.stderr)
    groups, unresolved = build(names, index, tables, pools)

    covered = sum(1 for g in groups.values() if g["donor"])
    needing = sum(1 for g in groups.values() for m in g["members"]
                  if m["needsFallback"])
    orphaned = sum(1 for g in groups.values() if not g["donor"]
                   for m in g["members"] if m["needsFallback"])

    print(f"{len(names):,} NPC(s) -> {len(groups)} voice group(s)",
          file=sys.stderr)
    print(f"  groups with a donor : {covered}/{len(groups)}", file=sys.stderr)
    print(f"  NPCs needing one    : {needing}", file=sys.stderr)
    print(f"  still without       : {orphaned}", file=sys.stderr)

    if args.report or not args.output:
        print(f"\n{'group':<24}{'donor':<30}{'source':<14}{'clips':>6}",
              file=sys.stderr)
        for name, group in sorted(groups.items(),
                                  key=lambda kv: -len(kv[1]["members"])):
            needs = sum(1 for m in group["members"] if m["needsFallback"])
            donor = group["donor"]
            print(f"{name:<24}"
                  f"{(donor['npcName'] if donor else '- none -')[:29]:<30}"
                  f"{(donor['source'] if donor else '-'):<14}"
                  f"{(donor['clips'] if donor else 0):>6}", file=sys.stderr)

    if unresolved:
        print(f"\n{len(unresolved)} NPC(s) not in the client tables: "
              f"{', '.join(unresolved[:6])}"
              f"{' ...' if len(unresolved) > 6 else ''}", file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(groups, handle, indent=2, ensure_ascii=False)
        print(f"\nWrote {args.output}.", file=sys.stderr)
        print(f"Extract the donor clips it names, then pass it to synthesis:\n"
              f"    python3 synthesize.py passages.json --reference ./ref "
              f"--voice-bank {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
