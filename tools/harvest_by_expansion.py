#!/usr/bin/env python3
"""Harvest quest text from Wowhead, oldest-ID-first, splitting output into
one tools/harvested_<expansion>.json per expansion (matching passages.json's
schema), and updating tools/quest_expansions.json as it goes.

Resumable: skips IDs already present in quest_expansions.json (checked) and
skips passages already present in passages.json / harvested_new.json / any
tools/harvested_*.json. Cached HTML pages are reused automatically.

Usage:
    python tools/harvest_by_expansion.py --ids tools/todo_all.txt --delay 1.5
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wowhead_quests import fetch, extract, read_ids, Blocked
from harvest_export import normalize

ADDED_IN = re.compile(r"Added in World of Warcraft:\s*([^<\">.\n]+)")

# Wowhead's own wording when a quest is not real, live content: removed from
# the game, or only ever existed on a test realm. Found 24 Aug by scanning the
# whole cache after the owner asked "is there QA we can do on this" -- 1,199
# quest IDs were already sitting in harvested files, 30 clips of it already
# voiced, one literally titled "[DNT] QUEST NOT USED" (a Blizzard-internal
# Do Not Translate marker that leaked into the quest table). Checked inline
# here so every future fetch catches it too, not only a one-off retroactive
# scan.
REMOVED_MARKER = re.compile(
    r"no longer available within the game|currently unobtainable", re.I)
PTR_BETA_MARKER = re.compile(
    r"(currently on|only available on) the (PTR|Beta|Public Test Realm)", re.I)
DNT_TITLE = re.compile(r"^\s*\[DNT\]", re.I)
EXCLUDED_IDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "excluded_quest_ids.json")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPANSIONS_PATH = os.path.join(REPO_ROOT, "tools", "quest_expansions.json")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
INFERRED_EXPANSIONS_PATH = os.path.join(TOOLS_DIR, "quest_expansions_inferred.json")

SLUG = {
    "Classic": "classic",
    "The Burning Crusade": "tbc",
    "Wrath of the Lich King": "wotlk",
    "Cataclysm": "cataclysm",
    "Mists of Pandaria": "mop",
    "Warlords of Draenor": "wod",
    "Legion": "legion",
    "Battle for Azeroth": "bfa",
    "Shadowlands": "shadowlands",
    "Dragonflight": "dragonflight",
    "The War Within": "war_within",
    "Midnight": "midnight",
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as h:
            return json.load(h)
    return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as h:
        json.dump(data, h, indent=2, ensure_ascii=False)
        h.write("\n")
    os.replace(tmp, path)


def harvested_path(expansion):
    slug = SLUG.get(expansion, re.sub(r"[^a-z0-9]+", "_", expansion.lower()))
    return os.path.join(TOOLS_DIR, f"harvested_{slug}.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=None,
                     help="stop after this many fetches this run")
    args = ap.parse_args()

    ids = read_ids(args.ids)
    expansions = load_json(EXPANSIONS_PATH, {})
    # Zone-based expansion inference (tools/infer_expansions.py), used as a
    # fallback below in place of the old "no Added-in line -> Classic"
    # guess. That guess was wrong far more often than right: the Added-in
    # line is sparse on nearly every expansion's pages (not just Classic's),
    # so it silently dumped most Warlords of Draenor/Shadowlands/etc. quests
    # into Classic. The inferred map instead reads each quest's zone off its
    # cached page and only falls back to Classic itself when even that has
    # no answer. See tools/infer_expansions.py for the full method.
    inferred_expansions = load_json(INFERRED_EXPANSIONS_PATH, {})
    excluded_ids = load_json(EXCLUDED_IDS_PATH, {})

    # Pre-load existing text so we don't re-add duplicates.
    existing_keys = set()
    for name in ("passages.json",
                 os.path.join("tools", "harvested_new.json")):
        p = os.path.join(REPO_ROOT, name)
        if os.path.exists(p):
            d = load_json(p, {"passages": []})
            for rec in d.get("passages", []):
                existing_keys.add((rec["questID"], rec["passage"]))
    exp_files = {}  # expansion -> data dict, loaded lazily

    def get_exp_data(expansion):
        if expansion not in exp_files:
            path = harvested_path(expansion)
            data = load_json(path, {"source": "wowhead", "passages": []})
            for rec in data["passages"]:
                existing_keys.add((rec["questID"], rec["passage"]))
            exp_files[expansion] = (path, data)
        return exp_files[expansion]

    fetched = 0
    new_expansions = 0
    new_passages = 0
    no_page = 0
    counts_by_exp = {}

    def checkpoint():
        save_json(EXPANSIONS_PATH, expansions)
        for path, data in exp_files.values():
            save_json(path, data)

    try:
        for i, qid in enumerate(ids, 1):
            key = str(qid)
            if key in expansions:
                continue  # already checked in a previous run

            try:
                page = fetch(qid, args.delay)
            except Blocked as exc:
                print(f"\nBLOCKED at quest {qid} (index {i}/{len(ids)}): {exc}",
                      file=sys.stderr)
                print("Stopping immediately per policy. Progress saved.",
                      file=sys.stderr)
                break

            fetched += 1

            if page:
                m = ADDED_IN.search(page)
                # The Added-in tag is authoritative when present, but it is
                # sparse across every expansion (not just Classic's quests
                # lack it -- so do most Warlords of Draenor/Shadowlands/etc.
                # pages in this cache). Fall back to the zone-based inferred
                # map (tools/infer_expansions.py) before ever guessing
                # Classic, and only guess Classic if that map has nothing
                # either -- matching the fact that Classic content is what's
                # left over once nothing else identifies the quest.
                if m:
                    expansion = m.group(1).strip()
                else:
                    expansion = inferred_expansions.get(key) or "Classic"
                expansions[key] = expansion
                removed_reason = None
                if REMOVED_MARKER.search(page):
                    removed_reason = "removed"
                elif PTR_BETA_MARKER.search(page):
                    removed_reason = "ptr_beta"
                if removed_reason and key not in excluded_ids:
                    excluded_ids[key] = removed_reason
                    save_json(EXCLUDED_IDS_PATH, excluded_ids)
                if expansion and key not in excluded_ids:
                    new_expansions += 1
                    title, passages = extract(qid, page)
                    if DNT_TITLE.search(title or ""):
                        excluded_ids[key] = "dnt_title"
                        save_json(EXCLUDED_IDS_PATH, excluded_ids)
                        continue
                    path, data = get_exp_data(expansion)
                    for passage, (raw, speaker) in passages.items():
                        if (qid, passage) in existing_keys:
                            continue
                        spoken, _leftovers = normalize(raw, "adventurer", "male")
                        if not spoken:
                            continue
                        data["passages"].append({
                            "questID": qid,
                            "passage": passage,
                            "title": title,
                            "npcID": speaker[0] if speaker else "",
                            "npcName": speaker[1] if speaker else "",
                            "soundFile": f"{qid}_{passage}.ogg",
                            "text": spoken,
                        })
                        existing_keys.add((qid, passage))
                        new_passages += 1
                        counts_by_exp[expansion] = counts_by_exp.get(expansion, 0) + 1
            else:
                expansions[key] = None
                no_page += 1

            if fetched % 20 == 0:
                checkpoint()
                print(f"  [{i}/{len(ids)}] fetched={fetched} "
                      f"expansions_found={new_expansions} "
                      f"new_passages={new_passages} no_page={no_page} "
                      f"by_exp={counts_by_exp}", file=sys.stderr)

            if args.limit and fetched >= args.limit:
                print(f"Hit --limit {args.limit}, stopping.", file=sys.stderr)
                break
    finally:
        checkpoint()

    print(f"\nDone this run: fetched {fetched} pages, "
          f"{new_expansions} with an expansion line, {no_page} with no page, "
          f"{new_passages} new passages extracted.", file=sys.stderr)
    print(f"By expansion: {counts_by_exp}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
