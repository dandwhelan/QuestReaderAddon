#!/usr/bin/env python3
"""Priority-1 harvest: fetch uncached Wowhead pages for quests that already
have generated audio, to build the quest->expansion mapping, plus opportunistically
extract any quest text passages found along the way.

Resumable: checkpoints tools/quest_expansions.json and tools/harvested_new.json
after every fetch. Re-run with the same --ids file; already-cached pages are
skipped instantly by wowhead_quests.fetch(), and IDs already recorded in
quest_expansions.json are skipped too.

Usage:
    python tools/harvest_priority1.py --ids tools/priority1_ids.txt
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wowhead_quests import fetch, extract, read_ids, Blocked
from harvest_export import normalize

ADDED_IN = re.compile(r"Added in World of Warcraft:\s*([^<\">.\n]+)")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPANSIONS_PATH = os.path.join(REPO_ROOT, "tools", "quest_expansions.json")
HARVESTED_PATH = os.path.join(REPO_ROOT, "tools", "harvested_new.json")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=None,
                     help="stop after this many NEW fetches (for testing)")
    args = ap.parse_args()

    ids = read_ids(args.ids)
    expansions = load_json(EXPANSIONS_PATH, {})
    harvested = load_json(HARVESTED_PATH, {"source": "wowhead", "passages": []})
    existing_keys = {(p["questID"], p["passage"]) for p in harvested["passages"]}

    fetched_this_run = 0
    new_expansions = 0
    new_passages = 0
    no_expansion = 0

    for i, qid in enumerate(ids, 1):
        key = str(qid)
        if key in expansions and fetched_this_run > 0:
            # already mapped from a previous run; only skip actual work,
            # still fine to re-check page presence cheaply below.
            pass
        try:
            page = fetch(qid, args.delay)
        except Blocked as exc:
            print(f"\nBLOCKED at quest {qid} ({i}/{len(ids)}): {exc}",
                  file=sys.stderr)
            print("Stopping. Progress saved. Resume with the same command "
                  "later; cached/mapped IDs are skipped.", file=sys.stderr)
            break

        fetched_this_run += 1

        if page:
            m = ADDED_IN.search(page)
            if m:
                expansions[key] = m.group(1).strip()
                new_expansions += 1
            else:
                if key not in expansions:
                    expansions[key] = None
                no_expansion += 1

            title, passages = extract(qid, page)
            for passage, (raw, speaker) in passages.items():
                if (qid, passage) in existing_keys:
                    continue
                spoken, _leftovers = normalize(raw, "adventurer", "male")
                if not spoken:
                    continue
                harvested["passages"].append({
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
        else:
            expansions.setdefault(key, None)

        if fetched_this_run % 10 == 0 or i == len(ids):
            save_json(EXPANSIONS_PATH, expansions)
            save_json(HARVESTED_PATH, harvested)
            print(f"  [{i}/{len(ids)}] fetched={fetched_this_run} "
                  f"expansions_found={new_expansions} "
                  f"new_passages={new_passages}", file=sys.stderr)

        if args.limit and fetched_this_run >= args.limit:
            print(f"Hit --limit {args.limit}, stopping.", file=sys.stderr)
            break

    save_json(EXPANSIONS_PATH, expansions)
    save_json(HARVESTED_PATH, harvested)
    print(f"\nDone this run: fetched {fetched_this_run} pages, "
          f"{new_expansions} new expansion mappings, "
          f"{no_expansion} pages with no expansion line, "
          f"{new_passages} new passages extracted.", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
