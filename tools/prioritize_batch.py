#!/usr/bin/env python3
"""Order harvested passages into a synthesis batch: story quests first.

THE SIGNAL, AND HOW SOLID IT IS
------------------------------------------------------------------------
Wowhead's own quest pages carry a real, Blizzard-sourced marker for "this is
a Campaign quest": the infobox's Start/End icons are
    quest-campaign-available.png / quest-campaign-turnin.png
instead of the ordinary
    questnormal.png / questturnin.png
(there is also a distinct quest-important-*.png icon for quests Wowhead
flags as otherwise notable/chain-critical, used here as a weaker tier 2).
This is genuine story-detection, not a proxy -- it reflects Blizzard's own
Campaign quest type (introduced with the War Campaign in Battle for
Azeroth and used since for Shadowlands chapters, Dragonflight, The War
Within, and Midnight).

BUT coverage is thin and lopsided. As of writing, tools/.cache/wowhead/ has
~11,000 cached quest pages against a ~110,000-quest corpus (~10%), and of
those cached pages only ~205 carry the campaign icon and ~45 the important
icon. The campaign-tagged quests found are almost all The War Within,
Midnight (both post-BfA, where the Campaign UI exists) and, oddly, a
handful of quests classified as "Classic" -- these are very likely 1-60
retail quests misfiled by quest_expansions.json's own Classic/retail
ambiguity, not Vanilla content (Vanilla predates the Campaign system
entirely and cannot have this icon). Wrath shows 5 hits for the same
reason. Expansions between Classic and BfA (TBC, Wrath proper, Cata, MoP,
Legion) show ~0 campaign hits in this cache -- not because their story
quests don't matter, but because Blizzard's Campaign quest *type* did not
exist yet when that content shipped, so nothing in those expansions can
ever carry this marker, cache coverage or not.

So: tier 1/2 here is real signal where it fires, but it only ever *can*
fire for BfA-onward content, and even there is limited by how much of the
cache has been fetched. It should not be read as "these are the only
story quests in the game" -- it is "these are the quests we can currently
*prove* are story quests." Everything else falls back to the plainly
stated substitute the task allows: expansion-recency order alone, which is
NOT a story-detection heuristic, it just guarantees newest-expansion
filler comes before older-expansion filler.

TIERS EMITTED (best to worst signal)
    1. Confirmed campaign quest (wowhead campaign icon), newest expansion first
    2. Wowhead-flagged "important" quest, newest expansion first
    3. Everything else -- expansion recency only, no story signal at all

Within a tier, quests are ordered by expansion recency, then by questID for
stability. All passages for a quest (description/progress/completion) stay
adjacent and keep that relative order.

Usage:
    python tools/prioritize_batch.py tools/harvested_bfa.json tools/harvested_wotlk.json -o tools/priority_batch.json
    python tools/prioritize_batch.py tools/harvested_*.json --limit 500 --dry-run
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import OrderedDict, Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOUNDS_DIR = os.path.join(REPO_ROOT, "Sounds")
WOWHEAD_CACHE = os.path.join(REPO_ROOT, "tools", ".cache", "wowhead")
QUEST_EXPANSIONS_JSON = os.path.join(REPO_ROOT, "tools", "quest_expansions.json")

# Newest first -- matches the task's stated backfill order.
EXPANSION_ORDER = [
    "Midnight",
    "The War Within",
    "Dragonflight",
    "Shadowlands",
    "Battle for Azeroth",
    "Legion",
    "Warlords of Draenor",
    "Mists of Pandaria",
    "Cataclysm",
    "Wrath of the Lich King",
    "The Burning Crusade",
    "Classic",
]
EXPANSION_RANK = {name: i for i, name in enumerate(EXPANSION_ORDER)}
UNKNOWN_EXPANSION_RANK = len(EXPANSION_ORDER)  # sorts after every known one

# Same regex build_sound_packs.py uses to recover an expansion from a
# per-quest page's own "Added in World of Warcraft: X" line, for quests
# quest_expansions.json does not cover.
ADDED_IN = re.compile(r"Added in World of Warcraft:\s*([^<\">.\n]+)")

CAMPAIGN_MARKERS = ("quest-campaign-available", "quest-campaign-turnin")
IMPORTANT_MARKERS = ("quest-important-available", "quest-important-turnin")

PASSAGE_ORDER = {"description": 0, "progress": 1, "completion": 2}


def log(message):
    print(message, file=sys.stderr, flush=True)


def load_quest_expansions_json(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"warning: could not read {path} ({exc}); ignoring it")
        return {}
    if not isinstance(raw, dict):
        log(f"warning: {path} is not a JSON object; ignoring it")
        return {}
    return {str(k): v.strip() for k, v in raw.items()
            if isinstance(v, str) and v.strip()}


def scan_wowhead_cache(cache_dir):
    """One pass over the cache: {questID: expansion} plus campaign/important
    quest-ID sets, all from data already sitting on disk."""
    expansion_fallback = {}
    campaign_ids = set()
    important_ids = set()

    if not os.path.isdir(cache_dir):
        log(f"warning: no wowhead cache at {cache_dir}; campaign/important "
            f"detection and expansion fallback are both unavailable")
        return expansion_fallback, campaign_ids, important_ids

    unreadable = 0
    scanned = 0
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
        scanned += 1

        match = ADDED_IN.search(text)
        if match:
            expansion_fallback[quest_id] = match.group(1).strip()

        if any(marker in text for marker in CAMPAIGN_MARKERS):
            campaign_ids.add(quest_id)
        elif any(marker in text for marker in IMPORTANT_MARKERS):
            important_ids.add(quest_id)

    if unreadable:
        log(f"warning: {unreadable} cached wowhead page(s) could not be read")
    log(f"Scanned {scanned:,} cached wowhead page(s): "
        f"{len(campaign_ids)} campaign-marked, {len(important_ids)} "
        f"important-marked, {len(expansion_fallback)} carry an "
        f"'Added in World of Warcraft' line.")
    return expansion_fallback, campaign_ids, important_ids


def load_client_campaign_quests():
    """Campaign quest IDs straight from the client's own tables.

    Chain: CampaignXQuestLine (CampaignID -> QuestLineID) joined to
    QuestLineXQuest (QuestLineID -> QuestID). A quest sitting in a questline
    that belongs to a campaign IS a campaign quest, by Blizzard's own
    definition -- no inference involved.

    This replaces scraping Wowhead pages for a campaign icon, which was the
    only signal available before these tables were exported. That approach
    found 59 campaign quests, because it could only see the few thousand
    pages already cached and the icon only exists on Battle for Azeroth and
    later content. The client tables give 6,113 covering every expansion.

    Lives in tools/.cache/ directly (exported from wow.export), NOT
    tools/.cache/wowhead/ -- that subfolder is per-quest HTML pages, a
    different cache with a different purpose. The first version of this
    function took cache_dir from the caller and looked in the wowhead
    subfolder, found nothing, and silently fell back to the scrape without
    ever logging that it had. Hardcoded here instead of threaded through
    another parameter, so that mistake can't happen again the same way.

    Returns an empty set if the tables are absent -- the caller falls back to
    the scraped markers, which is worse but still works.
    """
    client_cache = os.path.join(REPO_ROOT, "tools", ".cache")
    cxq = os.path.join(client_cache, "CampaignXQuestLine.csv")
    lxq = os.path.join(client_cache, "QuestLineXQuest.csv")
    if not (os.path.exists(cxq) and os.path.exists(lxq)):
        return set()

    def rows(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            return list(csv.DictReader(handle, delimiter=";"))

    try:
        campaign_lines = {r["QuestLineID"] for r in rows(cxq)}
        return {r["QuestID"] for r in rows(lxq)
                if r["QuestLineID"] in campaign_lines}
    except (KeyError, OSError) as exc:
        log(f"warning: could not read client campaign tables ({exc}); "
            f"falling back to scraped markers")
        return set()


def build_expansion_map(cache_dir, quest_expansions_json):
    primary = load_quest_expansions_json(quest_expansions_json)
    fallback, campaign_ids, important_ids = scan_wowhead_cache(cache_dir)

    # The client's own campaign tables are authoritative where they exist; the
    # scraped set is only a fallback for anything they somehow miss.
    client_campaign = load_client_campaign_quests()
    if client_campaign:
        added = len(client_campaign - campaign_ids)
        log(f"Client campaign tables: {len(client_campaign):,} campaign "
            f"quest(s) ({added:,} the wowhead scrape had missed).")
        campaign_ids = campaign_ids | client_campaign

    mapping = dict(fallback)
    mapping.update(primary)  # primary (quest_expansions.json) wins
    filled_by_fallback_only = len(fallback.keys() - primary.keys())
    log(f"Combined expansion map: {len(primary):,} from "
        f"quest_expansions.json, {filled_by_fallback_only:,} more from the "
        f"wowhead page cache, {len(mapping):,} total quests with a known "
        f"expansion.")
    return mapping, campaign_ids, important_ids


def load_input_passages(paths):
    """Merge one or more passages.json-shaped files, de-duplicating on
    (questID, passage) -- last file wins on a collision, same as a human
    manually concatenating batches would expect."""
    merged = OrderedDict()
    sources = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or "passages" not in data:
            sys.exit(f"{path}: expected an object with a 'passages' list")
        sources.append(data.get("source", os.path.basename(path)))
        count = 0
        for passage in data["passages"]:
            key = (str(passage.get("questID")), passage.get("passage"))
            merged[key] = passage
            count += 1
        log(f"Loaded {count:,} passage(s) from {path}.")
    log(f"{len(merged):,} unique (questID, passage) entries after merging "
        f"{len(paths)} file(s).")
    return list(merged.values()), sources


def already_voiced(sound_dir):
    """Stems (without extension) of every file already sitting in Sounds/."""
    stems = set()
    if not os.path.isdir(sound_dir):
        log(f"warning: no Sounds/ directory at {sound_dir}; nothing will "
            f"be treated as already voiced")
        return stems
    for name in os.listdir(sound_dir):
        stem, ext = os.path.splitext(name)
        if ext.lower() in (".ogg", ".wav"):
            stems.add(stem)
    return stems


def tier_for(quest_id, campaign_ids, important_ids):
    if quest_id in campaign_ids:
        return 1
    if quest_id in important_ids:
        return 2
    return 3


def expansion_rank_for(quest_id, expansion_map):
    expansion = expansion_map.get(quest_id)
    if expansion is None:
        return UNKNOWN_EXPANSION_RANK, None
    return EXPANSION_RANK.get(expansion, UNKNOWN_EXPANSION_RANK), expansion


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+",
                         help="one or more passages.json-shaped harvest files")
    parser.add_argument("-o", "--output",
                         default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "priority_batch.json"),
                         help="where to write the ordered batch "
                              "(default: tools/priority_batch.json)")
    parser.add_argument("--limit", type=int, default=None,
                         help="emit only the top N passages")
    parser.add_argument("--sounds", default=SOUNDS_DIR,
                         help="Sounds/ directory to check for existing audio "
                              "(default: %(default)s)")
    parser.add_argument("--wowhead-cache", default=WOWHEAD_CACHE,
                         help="cached wowhead quest pages (default: %(default)s)")
    parser.add_argument("--quest-expansions", default=QUEST_EXPANSIONS_JSON,
                         help="questID->expansion map (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                         help="report what would happen; do not write the "
                              "output file")
    parser.add_argument("--exclude-file",
                         default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "excluded_quest_ids.json"),
                         help="questID->reason map of quests to never voice: "
                              "removed from the game, PTR/Beta-only, or "
                              "internal [DNT] dev markers (default: %(default)s). "
                              "Pass an empty/nonexistent path to disable.")
    args = parser.parse_args()

    expansion_map, campaign_ids, important_ids = build_expansion_map(
        args.wowhead_cache, args.quest_expansions)

    passages, sources = load_input_passages(args.inputs)

    # Wowhead marks three kinds of quest that were harvested along with
    # everything else but were never real, live content: removed/unobtainable
    # quests (the page says so outright), PTR/Beta-only quests, and internal
    # "[DNT]" (Do Not Translate) dev markers that leaked into the quest table.
    # Found by scanning the cache on 24 Aug: 1,199 quest IDs, 21 of which had
    # already been voiced before anyone noticed.
    excluded = {}
    if args.exclude_file and os.path.exists(args.exclude_file):
        excluded = json.load(open(args.exclude_file, encoding="utf-8"))
        log(f"Loaded {len(excluded):,} excluded quest ID(s) from "
            f"{args.exclude_file}.")

    voiced_stems = already_voiced(args.sounds)
    remaining = []
    skipped_voiced = skipped_excluded = 0
    for passage in passages:
        quest_id = str(passage.get("questID"))
        if quest_id in excluded:
            skipped_excluded += 1
            continue
        passage_kind = passage.get("passage", "")
        stem = f"{quest_id}_{passage_kind}"
        if stem in voiced_stems:
            skipped_voiced += 1
            continue
        remaining.append(passage)
    log(f"Skipped {skipped_voiced:,} passage(s) that already have audio in "
        f"{args.sounds}.")
    log(f"Skipped {skipped_excluded:,} passage(s) from removed/PTR-only/DNT "
        f"quests.")

    def sort_key(passage):
        quest_id = str(passage.get("questID"))
        tier = tier_for(quest_id, campaign_ids, important_ids)
        exp_rank, _ = expansion_rank_for(quest_id, expansion_map)
        passage_rank = PASSAGE_ORDER.get(passage.get("passage", ""), 99)
        # questID as int when possible, so ordering is stable and readable
        # rather than a lexicographic string sort.
        try:
            qid_key = int(quest_id)
        except ValueError:
            qid_key = quest_id
        return (tier, exp_rank, qid_key, passage_rank)

    ordered = sorted(remaining, key=sort_key)

    tier_counts = Counter(
        tier_for(str(p.get("questID")), campaign_ids, important_ids)
        for p in ordered)
    expansion_counts = Counter(
        expansion_rank_for(str(p.get("questID")), expansion_map)[1] or "(unknown)"
        for p in ordered)

    if args.limit is not None:
        ordered = ordered[:args.limit]

    log("")
    log("Ordering applied: tier 1 = confirmed Wowhead campaign-icon quest, "
        "tier 2 = Wowhead important-icon quest, tier 3 = everything else "
        "(expansion recency only, no story signal). Within a tier: "
        "expansion recency (newest first), then questID.")
    log(f"Tier counts (of {len(remaining):,} eligible passages, before "
        f"--limit): tier 1 (campaign) = {tier_counts.get(1, 0):,}, "
        f"tier 2 (important) = {tier_counts.get(2, 0):,}, "
        f"tier 3 (recency-only) = {tier_counts.get(3, 0):,}.")
    log("Passages by expansion (before --limit):")
    for name in EXPANSION_ORDER + ["(unknown)"]:
        if expansion_counts.get(name):
            log(f"  {name}: {expansion_counts[name]:,}")

    if args.limit is not None:
        log(f"--limit {args.limit}: emitting {len(ordered):,} passage(s).")
    else:
        log(f"Emitting all {len(ordered):,} eligible passage(s).")

    output = {
        "source": f"prioritize_batch.py <- {', '.join(str(s) for s in sources)}",
        "passages": ordered,
    }

    if args.dry_run:
        log(f"--dry-run: not writing {args.output}")
        return

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
    log(f"Wrote {len(ordered):,} passage(s) to {args.output}.")


if __name__ == "__main__":
    main()
