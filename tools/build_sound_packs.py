#!/usr/bin/env python3
"""Split the monolithic Sounds/ + SoundLengths.lua into per-expansion packs.

Each output pack is a self-contained addon directory: its own .toc, its own
Sounds/, and its own SoundLengths.lua shard under a pack-specific global name.
See packs/QuestReaderAddon_Pack_Cataclysm/ for what one looks like once built,
and QuestReaderAddon.lua's QuestReaderAddon_RegisterSoundPack for how a pack's
index gets merged into the base addon at runtime.

This script only reads Sounds/ and SoundLengths.lua. It never writes into
either -- packs are written to a new output directory (default: packs/), and
an existing pack directory is refused unless --force, in which case it is
backed up first, not overwritten in place.

WHERE THE EXPANSION FOR A QUEST COMES FROM, AND ITS LIMITS
------------------------------------------------------------------------
There is no expansion field on quests anywhere in this repo. QuestV2.csv
(tools/.cache/QuestV2.csv, a dump of the client's QuestV2 table) carries only
ID, UniqueBitFlag and UiQuestDetailsThemeID -- no zone, no expansion. Nothing
else cached here (Creature.csv, the listfile, vo-index.json) carries it
either, and voice_sources.py's --patch filtering is about which patch a
piece of *Blizzard reference audio* was recorded for, which is unrelated to
which expansion a *quest* belongs to.

The one thing that does carry it: tools/.cache/wowhead/<questID>.html, the
per-quest pages already fetched by wowhead_quests.py for quest text, each
carry a line like:
    Added in World of Warcraft: The War Within
This script parses that line for whatever quests happen to be in the cache
(2,043 pages as of writing, of ~66,000 quest IDs in QuestV2 -- a few percent).
Coverage will grow for free as wowhead_quests.py fetches more quests for text,
but it is NOT complete today, and a quest whose page was never fetched, or
whose page lacked that line (dead/renamed/blocked pages do happen -- see the
"unmapped" count this script prints), has no known expansion.

Quests with no known expansion are NOT guessed at and NOT dropped. They go
into a pack named "Unsorted" so their audio still ships and still plays; it
is just not expansion-sorted yet. If real coverage matters, the fix is a
dedicated scrape: either keep growing the wowhead cache (it costs nothing,
this script just needs to be re-run), or scrape wowhead's per-expansion quest
listing pages directly, which return the full ID set for an expansion in one
pass instead of one page per quest.

Gossip and item/book audio (npc<id>_gossip<n>, item<id>_page<n>, and friends)
has no quest ID to look anything up by, and an NPC or item can span many
expansions anyway. That audio always goes into a pack named "Shared", which
is the one pack an install should always have alongside any expansion packs.

Usage:
    python tools/build_sound_packs.py --out packs
    python tools/build_sound_packs.py --out packs --dry-run
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_TOC = os.path.join(REPO_ROOT, "QuestReaderAddon.toc")


def base_interface_lines():
    """Read ## Interface / ## X-Interface straight from the base addon's own
    .toc, so a pack never drifts from it after a game-version bump. This used
    to be a literal string here -- fine until the base .toc changed and every
    pack silently kept the old value."""
    lines = []
    with open(BASE_TOC, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("## Interface:") or line.startswith("## X-Interface:"):
                lines.append(line.rstrip("\n"))
    if not lines:
        raise SystemExit(f"No ## Interface line found in {BASE_TOC}")
    return "\n".join(lines) + "\n"
SOUNDS_DIR = os.path.join(REPO_ROOT, "Sounds")
SOUND_LENGTHS = os.path.join(REPO_ROOT, "SoundLengths.lua")
WOWHEAD_CACHE = os.path.join(REPO_ROOT, "tools", ".cache", "wowhead")
# Another process may be populating this file (a {questID: expansion} map
# fetched directly from Wowhead's per-expansion listing pages, not the
# per-quest page cache below) while this script runs. It is read-only here,
# checked for on every invocation, and simply absent until that job finishes
# writing it -- this script does not wait for it and works fine without it.
QUEST_EXPANSIONS_JSON = os.path.join(REPO_ROOT, "tools", "quest_expansions.json")

# Records, per oversized pack, the quest/asset-ID boundaries this script has
# already committed to for splitting that pack into sequential volumes
# (Part1, Part2, ...). Once a boundary is written here it is never moved --
# see split_oversized() below for why: reshuffling a boundary after a pack
# has been published would silently break every existing install of that
# volume (its .toc/repo name stays the same, but the clips inside would no
# longer match what players think they installed). This file is new output
# owned by this script, not one of the protected data files -- it is safe to
# create/update it.
DEFAULT_SPLIT_STATE = os.path.join(REPO_ROOT, "tools", "pack_split_state.json")

# CurseForge's hard per-file cap. Leave real headroom under it so ordinary
# growth of the *last, still-open* volume of a pack doesn't immediately
# require yet another split next run -- see split_oversized().
DEFAULT_MAX_PACK_MB = 500
SPLIT_TARGET_FRACTION = 0.90  # fill a sealed volume to ~90% of the cap

# Mirrors build_soundlengths.py's AUDIO_NAME: group(1) is the ID/slug part,
# group(2) is the passage. Only "description"/"progress"/"completion" carry
# a leading questID that can be looked up against wowhead; gossip/page
# entries do not name a quest at all.
AUDIO_NAME = re.compile(
    r"^([a-z0-9_]+)_(description|progress|completion|gossip\d+|page\d+)\.(ogg|wav)$",
    re.IGNORECASE)
QUEST_PASSAGES = {"description", "progress", "completion"}

# Taken from the page's <meta name="description">, e.g. "... Added in World
# of Warcraft: The War Within. Always up to date with the latest patch." --
# stop at the first period so the trailing boilerplate sentence isn't
# swallowed into the expansion name.
ADDED_IN = re.compile(r"Added in World of Warcraft:\s*([^<\">.\n]+)")

SHARED_PACK = "Shared"
UNSORTED_PACK = "Unsorted"


def slugify(expansion_name):
    """"The War Within" -> "TheWarWithin", used in pack/addon names."""
    return re.sub(r"[^A-Za-z0-9]", "", expansion_name)


def load_sound_lengths(path):
    """Parse SoundLengths.lua's {["file"] = seconds,} body without a Lua VM.

    The file is machine-generated by build_soundlengths.py in exactly this
    shape, so a regex over the entry lines is reliable and avoids adding a
    Lua-parsing dependency for one flat table.
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    entries = {}
    for match in re.finditer(r'\["([^"]+)"\]\s*=\s*([0-9.]+)', text):
        entries[match.group(1)] = float(match.group(2))
    return entries


def load_quest_expansions_json(path):
    """{questID (str): expansion name} from tools/quest_expansions.json.

    This is a direct {questID: expansion} map, produced by a separate
    Wowhead-scraping job that is not part of this script and may be running
    concurrently with it. Returns an empty map, silently, if the file does
    not exist yet -- this script never waits for it and works fine without
    it, it just means every quest falls back to the per-page cache below (or
    to Unsorted if that has nothing either).

    A malformed file (bad JSON, wrong shape) is reported and skipped rather
    than crashing the whole build -- a broken supplementary map should not
    stop packs from being built at all.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: could not read {path} ({exc}); ignoring it",
              file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print(f"warning: {path} is not a JSON object; ignoring it",
              file=sys.stderr)
        return {}

    mapping = {}
    skipped = 0
    for quest_id, expansion in raw.items():
        if not isinstance(expansion, str) or not expansion.strip():
            skipped += 1
            continue
        mapping[str(quest_id)] = expansion.strip()
    if skipped:
        print(f"warning: {skipped} entr(y/ies) in {path} had a non-string "
              f"or empty expansion value and were skipped", file=sys.stderr)
    print(f"Loaded {len(mapping):,} quest->expansion mapping(s) from "
          f"{path}.", file=sys.stderr)
    return mapping


def build_quest_expansion_map_from_wowhead_cache(cache_dir):
    """{questID (str): expansion name} from cached wowhead quest pages.

    Returns an empty map, with a warning, if the cache directory is not
    present -- this script still runs and everything just lands in Unsorted.
    """
    mapping = {}
    if not os.path.isdir(cache_dir):
        print(f"warning: no wowhead cache at {cache_dir}; every quest will "
              f"land in '{UNSORTED_PACK}' unless quest_expansions.json "
              f"covers it", file=sys.stderr)
        return mapping

    unreadable = 0
    for name in os.listdir(cache_dir):
        if not name.lower().endswith(".html"):
            continue
        quest_id = os.path.splitext(name)[0]
        if not quest_id.isdigit():
            continue
        try:
            with open(os.path.join(cache_dir, name), "r",
                      encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            unreadable += 1
            continue
        match = ADDED_IN.search(text)
        if match:
            mapping[quest_id] = match.group(1).strip()
    if unreadable:
        print(f"warning: {unreadable} cached wowhead page(s) could not be "
              f"read", file=sys.stderr)
    return mapping


def build_quest_expansion_map(cache_dir, quest_expansions_json):
    """Combine both expansion sources, primary map wins on overlap.

    tools/quest_expansions.json (if present) is the primary source -- it is
    a direct, deliberately-fetched {questID: expansion} map, more complete
    and more precise than scraping the "Added in ..." line back out of
    per-quest pages cached for an unrelated purpose (quest text). The
    wowhead per-page cache only fills in quest IDs the primary map does not
    already cover.
    """
    primary = load_quest_expansions_json(quest_expansions_json)
    fallback = build_quest_expansion_map_from_wowhead_cache(cache_dir)

    mapping = dict(fallback)
    mapping.update(primary)  # primary wins on any overlap

    filled_by_fallback_only = len(fallback.keys() - primary.keys())
    print(f"Combined expansion map: {len(primary):,} from "
          f"quest_expansions.json, {filled_by_fallback_only:,} more from "
          f"the wowhead page cache, {len(mapping):,} total.",
          file=sys.stderr)
    return mapping


def classify(filename, quest_expansion):
    """Which pack a single sound file belongs in."""
    match = AUDIO_NAME.match(filename)
    if not match or match.group(2) not in QUEST_PASSAGES:
        return SHARED_PACK
    quest_id = match.group(1)
    return quest_expansion.get(quest_id, UNSORTED_PACK)


def plan_packs(sound_lengths, quest_expansion):
    """{pack_name: {filename: seconds}}"""
    packs = {}
    for filename, seconds in sound_lengths.items():
        pack = classify(filename, quest_expansion)
        packs.setdefault(pack, {})[filename] = seconds
    return packs


PART_SUFFIX = re.compile(r"^(.*)(_Part\d+)$")


def pack_addon_name(pack_key):
    part_match = PART_SUFFIX.match(pack_key)
    base_key = part_match.group(1) if part_match else pack_key
    part_tag = part_match.group(2) if part_match else ""
    if base_key in (SHARED_PACK, UNSORTED_PACK):
        suffix = base_key + part_tag
    else:
        suffix = slugify(base_key) + part_tag
    return f"QuestReaderAddon_Pack_{suffix}", suffix


def numeric_sort_key(filename):
    """Order clips by the numeric ID embedded in the filename.

    AUDIO_NAME's group(1) is "12345" for quest passages, or something like
    "npc12345"/"item6789" for gossip/page audio. Pulling the trailing digits
    out of that gives a stable, deterministic ordering to bin-pack by --
    it is NOT a proxy for quest order in the game, release date, or zone. It
    is just a fixed, reproducible sequence so that splitting is mechanical
    and re-running the script on unchanged input always yields the same
    boundaries.
    """
    match = AUDIO_NAME.match(filename)
    base = match.group(1) if match else filename
    digits = re.search(r"(\d+)$", base)
    return (int(digits.group(1)) if digits else -1, filename)


def load_split_state(path):
    """{pack_key: [frozen boundary, frozen boundary, ...]} (ascending ints).

    A missing/unreadable/malformed file is treated as "no packs have been
    split yet" -- this is new bookkeeping this script owns, so it degrades
    the same way quest_expansions.json does: absence just means "nothing
    known yet," never a crash.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: could not read split state {path} ({exc}); "
              f"treating as empty (a fresh split will be computed and "
              f"will overwrite this file)", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: sorted(int(b) for b in v) for k, v in raw.items()
             if isinstance(v, list)}


def save_split_state(path, state):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def size_bytes_for(entries, sounds_source):
    """{filename: size in bytes}, 0 for any file missing on disk."""
    sizes = {}
    for filename in entries:
        src = os.path.join(sounds_source, filename)
        sizes[filename] = os.path.getsize(src) if os.path.isfile(src) else 0
    return sizes


def split_oversized(pack_key, entries, sizes, max_bytes, state):
    """Split one oversized pack into sequential, ID-ordered volumes.

    Design, and why this beats the alternatives considered:

    - Zone/continent split was ruled out: nothing cached in this repo (see
      the module docstring and docs/sound_packs.md section 3) carries a
      quest's zone or continent, only its expansion (and that itself is
      only ~10% directly mapped, filled out by quest_expansions.json).
      Designing around zone data that doesn't exist would just move the
      "Unsorted" problem down a level.
    - Level bracket has the same problem: no level field exists anywhere in
      QuestV2.csv or the harvested JSON. Ruled out for the same reason.
    - A naive "just split what's over budget into two even halves each
      run" was rejected because re-running it after the library grows would
      recompute different boundaries and silently reshuffle which clips are
      in "Part1" vs "Part2" -- an existing install of Part1 would suddenly
      be missing clips it used to have (they moved to a renamed/renumbered
      Part2) with no error, just silence. That is worse than the 500MB
      problem it's meant to solve.

    What this does instead: sort this pack's entries by numeric_sort_key
    (a fixed, reproducible order -- not zone or level, just deterministic),
    then greedily fill "PartN" volumes to ~90% of max_bytes. The boundary
    (highest numeric key) at which a volume was sealed is written to
    pack_split_state.json and never revisited: on every future run, any
    entry whose key falls at or under a frozen boundary goes to that same
    already-published Part, full stop, even if that makes the frozen part
    quietly exceed the cap by a few clips (a warning is printed; it is not
    silently ignored). Only the newest, still-open Part absorbs new growth,
    and if IT crosses the cap, a new boundary is sealed and a new Part is
    opened. Existing installs of Part1..Part(n-1) are therefore never
    invalidated by later growth -- only a new PartN ever appears.

    Trade-off, stated plainly: the very first time a given pack crosses the
    cap, its previously-unsplit name changes (e.g.
    QuestReaderAddon_Pack_Classic becomes ..._Part1,
    ..._Part2, ...). That is a one-time, unavoidable break of that pack's
    existing repo name/identity -- there is no way to keep serving the same
    growing pack from the same single file once it must physically split.
    Once split, though, later growth is additive, not disruptive.

    The ordering is quest-ID order, not any meaningful player-facing axis
    (not story order, not zone, not level) -- a player picking "Part 2"
    has no idea what's actually inside beyond "some more Classic quests".
    This is the honest cost of not having zone/level data; see the
    accompanying design writeup for what to switch to (zone-based) once/if
    that data becomes available.
    """
    total = sum(sizes.get(f, 0) for f in entries)
    frozen = list(state.get(pack_key, []))
    if total <= max_bytes and not frozen:
        return {pack_key: entries}, frozen

    ordered = sorted(entries, key=numeric_sort_key)
    target = int(max_bytes * SPLIT_TARGET_FRACTION)

    parts = []          # list of {filename: seconds}
    current = {}
    current_bytes = 0

    # Walk the sorted list once. Anything at/under a frozen boundary goes
    # into that same fixed part, regardless of size. Once past all frozen
    # boundaries, greedily fill new parts up to `target`, sealing (freezing)
    # a new boundary each time a part fills up -- except the very last part,
    # which stays open (unfrozen) so ordinary future growth just adds to it
    # in place rather than immediately forcing yet another split.
    boundary_ptr = 0
    for filename in ordered:
        key = numeric_sort_key(filename)[0]
        if boundary_ptr < len(frozen) and key <= frozen[boundary_ptr]:
            # Belongs to an already-sealed part. Walk forward through
            # `parts` lazily -- parts list mirrors frozen boundaries 1:1.
            while len(parts) <= boundary_ptr:
                parts.append({})
            parts[boundary_ptr][filename] = entries[filename]
            continue
        # Past the current frozen boundary (or no boundaries left).
        while boundary_ptr < len(frozen) and key > frozen[boundary_ptr]:
            boundary_ptr += 1
        if boundary_ptr < len(frozen) and key <= frozen[boundary_ptr]:
            while len(parts) <= boundary_ptr:
                parts.append({})
            parts[boundary_ptr][filename] = entries[filename]
            continue

        # Open territory: greedily accumulate into the current open part.
        size = sizes.get(filename, 0)
        if current and current_bytes + size > target:
            # Seal this part: freeze its boundary and start a new open one.
            frozen.append(key_before)
            parts.append(current)
            current = {}
            current_bytes = 0
        current[filename] = entries[filename]
        current_bytes += size
        key_before = key

    if current:
        parts.append(current)
    elif not parts:
        parts.append({})

    result = {}
    for i, part_entries in enumerate(parts, start=1):
        result[f"{pack_key}_Part{i}"] = part_entries

    # Sanity: every frozen boundary must correspond to a real, non-final
    # part. Warn (don't crash) if a frozen part is over cap after all --
    # that can happen legitimately if a single clip near a boundary is
    # unusually large, or if max-pack-mb was lowered after boundaries were
    # already committed.
    for i, part_entries in enumerate(parts[:-1], start=1):
        part_bytes = sum(sizes.get(f, 0) for f in part_entries)
        if part_bytes > max_bytes:
            print(f"warning: {pack_key}_Part{i} is a frozen/sealed volume "
                  f"but now measures {part_bytes / 1024 / 1024:.1f} MB, "
                  f"over the {max_bytes / 1024 / 1024:.0f} MB cap. Its "
                  f"boundary is intentionally not being moved (that would "
                  f"break existing installs) -- this needs a human look.",
                  file=sys.stderr)

    return result, frozen


def write_pack(out_dir, pack_key, entries, sounds_source, dry_run):
    addon_name, suffix = pack_addon_name(pack_key)
    part_match = PART_SUFFIX.match(pack_key)
    base_key = part_match.group(1) if part_match else pack_key
    part_label = part_match.group(2).replace("_Part", " Part ") if part_match else ""
    pack_dir = os.path.join(out_dir, addon_name)
    total_bytes = 0
    total_seconds = sum(entries.values())

    if dry_run:
        for filename in entries:
            src = os.path.join(sounds_source, filename)
            if os.path.isfile(src):
                total_bytes += os.path.getsize(src)
        print(f"  {addon_name}: {len(entries):,} clip(s), "
              f"{total_seconds / 3600:.1f}h, {total_bytes / 1024 / 1024:.1f} MB "
              f"(dry run, nothing written)")
        return

    if os.path.exists(pack_dir):
        sys.exit(f"{pack_dir} already exists; remove it or pick a different "
                 f"--out directory. This tool never overwrites a previous "
                 f"pack build in place.")

    sounds_out = os.path.join(pack_dir, "Sounds")
    os.makedirs(sounds_out)

    missing = []
    for filename in sorted(entries):
        src = os.path.join(sounds_source, filename)
        if not os.path.isfile(src):
            missing.append(filename)
            continue
        shutil.copy2(src, os.path.join(sounds_out, filename))
        total_bytes += os.path.getsize(src)
    if missing:
        print(f"  warning: {addon_name} index names {len(missing)} file(s) "
              f"not found in {sounds_source}: {', '.join(missing[:5])}"
              f"{' ...' if len(missing) > 5 else ''}", file=sys.stderr)

    var_name = f"QuestReaderSoundLengths_Pack_{suffix}"
    body = "".join(f'    ["{name}"] = {seconds:.2f},\n'
                   for name, seconds in sorted(entries.items()))
    with open(os.path.join(pack_dir, "SoundLengths.lua"), "w",
              encoding="utf-8") as handle:
        handle.write(f"{var_name} = {{\n{body}}}\n")

    with open(os.path.join(pack_dir, "Register.lua"), "w",
              encoding="utf-8") as handle:
        handle.write(
            "local addonName = ...\n\n"
            "-- QuestReaderAddon_RegisterSoundPack is defined at the top of\n"
            "-- the base addon's QuestReaderAddon.lua. If this pack loads\n"
            "-- first, that global does not exist yet -- queue instead, and\n"
            "-- the base addon drains the queue itself once it loads.\n"
            "if QuestReaderAddon_RegisterSoundPack then\n"
            f"    QuestReaderAddon_RegisterSoundPack(addonName, {var_name})\n"
            "else\n"
            "    QuestReaderPendingSoundPacks = QuestReaderPendingSoundPacks or {}\n"
            "    table.insert(QuestReaderPendingSoundPacks,\n"
            f"        {{ name = addonName, index = {var_name} }})\n"
            "end\n")

    if base_key == UNSORTED_PACK:
        with open(os.path.join(pack_dir, "README_UNSORTED.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(
                "This pack holds quest audio whose expansion could not be\n"
                "determined at build time -- it is not a catch-all bug, it is\n"
                f"the deliberate, documented fallback (see docs/sound_packs.md,\n"
                "section 3, and tools/build_sound_packs.py's module docstring).\n"
                f"It currently contains {len(entries):,} clip(s), {total_bytes / 1024 / 1024:.1f} MB.\n\n"
                "Coverage improves automatically, with no code change, once:\n"
                "  - tools/quest_expansions.json exists and covers more quest\n"
                "    IDs (primary source), or\n"
                "  - tools/.cache/wowhead/<questID>.html grows to cover more\n"
                "    quest pages (fallback source).\n"
                "Re-run tools/build_sound_packs.py after either improves.\n")

    title = base_key if base_key in (SHARED_PACK, UNSORTED_PACK) \
        else f"{base_key} Voices"
    title = f"{title}{part_label}"
    with open(os.path.join(pack_dir, f"{addon_name}.toc"), "w",
              encoding="utf-8") as handle:
        handle.write(
            base_interface_lines() +
            f"## Title: SpeakStone Narration - {title}\n"
            f"## Notes: {base_key}{part_label} quest audio for SpeakStone "
            f"Narration. Requires QuestReaderAddon.\n"
            "## Author: clhammer weirdautomation@gmail.com, Paratus\n\n"
            # Required, not Optional: a pack is audio plus a two-line
            # Register.lua that calls a global the base addon defines. On its
            # own it does nothing at all, so the game should refuse to load it
            # without QuestReaderAddon rather than sit there inert. HandyNotes,
            # which this whole base-plus-modules layout follows, does the same
            # (RequiredDeps: HandyNotes) and reserves OptionalDeps for genuinely
            # optional libraries like Ace3.
            "## RequiredDeps: QuestReaderAddon\n\n"
            "SoundLengths.lua\n"
            "Register.lua\n")

    print(f"  {addon_name}: {len(entries):,} clip(s), "
          f"{total_seconds / 3600:.1f}h, {total_bytes / 1024 / 1024:.1f} MB "
          f"-> {pack_dir}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sounds", default=SOUNDS_DIR,
                        help="source Sounds/ directory (read-only, default: "
                             "the repo's Sounds/)")
    parser.add_argument("--index", default=SOUND_LENGTHS,
                        help="source SoundLengths.lua (read-only)")
    parser.add_argument("--wowhead-cache", default=WOWHEAD_CACHE,
                        help="tools/.cache/wowhead directory used for "
                             "quest->expansion lookup (fallback source)")
    parser.add_argument("--quest-expansions", default=QUEST_EXPANSIONS_JSON,
                        help="tools/quest_expansions.json, a direct "
                             "{questID: expansion} map (primary source, "
                             "used if present)")
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "packs"),
                        help="output directory for built packs (default: "
                             "packs/, alongside this repo)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the pack plan without writing anything")
    parser.add_argument("--max-pack-mb", type=float, default=DEFAULT_MAX_PACK_MB,
                        help=f"split a pack into sequential Part1/Part2/... "
                             f"volumes if it would exceed this size in MB "
                             f"(default: {DEFAULT_MAX_PACK_MB}, CurseForge's "
                             f"per-file cap)")
    parser.add_argument("--split-state", default=DEFAULT_SPLIT_STATE,
                        help="JSON file recording frozen split boundaries "
                             "for packs that have already been split, so "
                             "re-running never reshuffles a previously "
                             "published volume (default: "
                             "tools/pack_split_state.json)")
    args = parser.parse_args()

    if not os.path.isfile(args.index):
        sys.exit(f"Not found: {args.index}")
    if not os.path.isdir(args.sounds):
        sys.exit(f"Not found: {args.sounds}")

    sound_lengths = load_sound_lengths(args.index)
    if not sound_lengths:
        sys.exit(f"No entries parsed from {args.index}; is it the real "
                 f"SoundLengths.lua?")

    quest_expansion = build_quest_expansion_map(args.wowhead_cache,
                                                 args.quest_expansions)
    quests_seen = {AUDIO_NAME.match(f).group(1) for f in sound_lengths
                   if AUDIO_NAME.match(f) and AUDIO_NAME.match(f).group(2) in QUEST_PASSAGES}
    quests_mapped = quests_seen & quest_expansion.keys()
    print(f"Quest audio covers {len(quests_seen):,} quest ID(s); "
          f"{len(quests_mapped):,} ({len(quests_mapped) / max(1, len(quests_seen)):.0%}) "
          f"have a known expansion from the wowhead cache.", file=sys.stderr)

    packs = plan_packs(sound_lengths, quest_expansion)

    max_bytes = int(args.max_pack_mb * 1024 * 1024)
    split_state = load_split_state(args.split_state)
    state_changed = False
    final_packs = {}
    for pack_key, entries in packs.items():
        sizes = size_bytes_for(entries, args.sounds)
        sub_packs, frozen = split_oversized(pack_key, entries, sizes,
                                            max_bytes, split_state)
        if len(sub_packs) > 1 or pack_key in split_state:
            if split_state.get(pack_key) != frozen:
                state_changed = True
            split_state[pack_key] = frozen
        final_packs.update(sub_packs)
        if len(sub_packs) > 1:
            print(f"  {pack_key} exceeds {args.max_pack_mb:.0f} MB -> split "
                  f"into {len(sub_packs)} volume(s): "
                  f"{', '.join(sub_packs)}", file=sys.stderr)

    if not args.dry_run:
        os.makedirs(args.out, exist_ok=True)
        if state_changed:
            save_split_state(args.split_state, split_state)
            print(f"Updated split boundaries in {args.split_state}.",
                  file=sys.stderr)

    print(f"\nBuilding {len(final_packs)} pack(s) into {args.out}"
          f"{' (dry run)' if args.dry_run else ''}:")
    for pack_key in sorted(final_packs, key=lambda k: (k in (SHARED_PACK, UNSORTED_PACK), k)):
        write_pack(args.out, pack_key, final_packs[pack_key], args.sounds, args.dry_run)

    # Loud, explicit call-out of how much is unsorted -- this must never be
    # a silently-shipped mystery pack. Reported by clip count (what a player
    # feels) and by size (what shipping actually costs), not just the
    # quest-ID mapping percentage printed above.
    unsorted_clips = len(packs.get(UNSORTED_PACK, {}))
    total_clips = sum(len(v) for v in packs.values())
    if total_clips:
        unsorted_share = unsorted_clips / total_clips
        if unsorted_clips:
            print(f"\n*** {unsorted_clips:,} of {total_clips:,} clips "
                  f"({unsorted_share:.0%}) have no known expansion and are "
                  f"in '{UNSORTED_PACK}'. This is expected until "
                  f"tools/quest_expansions.json exists or the wowhead cache "
                  f"grows -- see docs/sound_packs.md section 3. It is NOT a "
                  f"bug in this script. ***", file=sys.stderr)

    manifest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source_index": os.path.abspath(args.index),
        "quest_expansions_json_used": os.path.isfile(args.quest_expansions),
        "packs": {pack_addon_name(k)[0]: len(v) for k, v in final_packs.items()},
        "unsorted_clip_count": unsorted_clips,
        "unsorted_share_of_total": round(unsorted_share, 4) if total_clips else 0,
        "max_pack_mb": args.max_pack_mb,
        "split_state_file": os.path.abspath(args.split_state),
    }
    if not args.dry_run:
        with open(os.path.join(args.out, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        print(f"\nWrote {os.path.join(args.out, 'manifest.json')}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
