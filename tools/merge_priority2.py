#!/usr/bin/env python3
"""Merge harvested_priority2.json into harvested_new.json (dedup by questID+passage),
and opportunistically backfill quest_expansions.json from the same cached pages
(cheap: pages are already on disk, no network)."""
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, "tools", ".cache", "wowhead")
ADDED_IN = re.compile(r"Added in World of Warcraft:\s*([^<\">.\n]+)")

new_path = os.path.join(REPO_ROOT, "tools", "harvested_priority2.json")
main_path = os.path.join(REPO_ROOT, "tools", "harvested_new.json")
exp_path = os.path.join(REPO_ROOT, "tools", "quest_expansions.json")

new_data = json.load(open(new_path, encoding="utf-8"))
main_data = json.load(open(main_path, encoding="utf-8"))
expansions = json.load(open(exp_path, encoding="utf-8"))

existing_keys = {(p["questID"], p["passage"]) for p in main_data["passages"]}
added = 0
for p in new_data["passages"]:
    key = (p["questID"], p["passage"])
    if key not in existing_keys:
        main_data["passages"].append(p)
        existing_keys.add(key)
        added += 1

with open(main_path, "w", encoding="utf-8") as h:
    json.dump(main_data, h, indent=2, ensure_ascii=False)
    h.write("\n")

print(f"Merged {added} new passages into {main_path} "
      f"(total now {len(main_data['passages'])}).")

# Backfill expansion mapping for the quest ids we just fetched.
qids = sorted({p["questID"] for p in new_data["passages"]})
new_exp = 0
checked = 0
for qid in qids:
    key = str(qid)
    if key in expansions:
        continue
    cached = os.path.join(CACHE_DIR, f"{qid}.html")
    if not os.path.exists(cached):
        continue
    checked += 1
    with open(cached, encoding="utf-8", errors="replace") as fh:
        page = fh.read()
    m = ADDED_IN.search(page)
    expansions[key] = m.group(1).strip() if m else None
    if m:
        new_exp += 1

with open(exp_path, "w", encoding="utf-8") as h:
    json.dump(expansions, h, indent=2, ensure_ascii=False)
    h.write("\n")

print(f"Backfilled expansion mapping for {checked} additional quest id(s), "
      f"{new_exp} with an expansion line. quest_expansions.json now has "
      f"{len(expansions)} entries.")
