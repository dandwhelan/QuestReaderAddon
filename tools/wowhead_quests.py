#!/usr/bin/env python3
"""Fetch quest text and speakers from Wowhead.

Quest text is server-delivered and absent from the client database, so it has
to come from somewhere that records it. The harvester addon reads it from a
live client; this reads it from Wowhead, which holds it because players upload
it. Both produce the same records, so the synthesis stage does not care which
was used.

The tradeoff between them is worth understanding:

    harvester   authoritative, uses only public API, needs the content played
    Wowhead     covers everything at once, but lags a patch by days and is a
                third party's data under their terms of use

Wowhead's terms do not permit scraping. This is the route the original project
used, and the addon's own README alludes to it ("the source I utilize for quest
info can sometimes take several days"), but it is a deliberate choice rather
than an obviously safe default. Requests are rate-limited and cached so a rerun
costs nothing, and the default pace is deliberately unhurried.

Usage:
    wowhead_quests.py --ids missing.txt -o passages.json
    wowhead_quests.py --ids missing.txt --delay 2.0 --format csv -o out.csv
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from harvest_export import normalize

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".cache", "wowhead")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

DESCRIPTION = re.compile(r'heading-size-3">Description</h2>(.*?)<h2', re.S)
SECTION = r'<div id="[^"]*-{0}"[^>]*>(.*?)</div>'
# Quick-facts are embedded as escaped JS: Start: [url=\/npc=213688\/urtago]Urtago[\/url]
PARTICIPANT = r'{0}:\s*\[url=\\?/npc=(\d+)\\?/[^\]]*\]([^\[]+)\['


def clean(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(fragment).strip()


class Blocked(Exception):
    """Wowhead has stopped serving us; carrying on would make it worse."""


def fetch(quest_id, delay, refresh=False):
    """Return the Wowhead page for a quest, caching so reruns are free.

    A 403 or 429 is rate limiting, not a missing page. It gets three retries
    with a delay that doubles from a minute, and then raises Blocked so the
    caller can stop cleanly — a 264-page run died mid-flight and lost every
    record it had extracted, because a block partway through killed the whole
    process before anything was written.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cached = os.path.join(CACHE_DIR, f"{quest_id}.html")
    if os.path.exists(cached) and not refresh:
        with open(cached, encoding="utf-8", errors="replace") as handle:
            return handle.read()

    request = urllib.request.Request(
        f"https://www.wowhead.com/quest={quest_id}",
        headers={"User-Agent": USER_AGENT})
    backoff = 60
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                time.sleep(delay)
                return None
            if exc.code in (403, 429) and attempt < 3:
                print(f"  rate limited ({exc.code}); waiting {backoff}s ...",
                      file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
                continue
            if exc.code in (403, 429):
                raise Blocked(f"still {exc.code} after {attempt} retries")
            raise
        finally:
            # Pace every request, including failures, so a run of missing IDs
            # does not turn into a burst.
            time.sleep(delay)

    with open(cached, "w", encoding="utf-8") as handle:
        handle.write(body)
    return body


def extract(quest_id, page):
    """Pull the three spoken passages and the NPCs who speak them."""
    title = ""
    match = re.search(r"<title>([^<]+?) - Quest - ", page)
    if match:
        title = html.unescape(match.group(1)).strip()

    participants = {}
    for role in ("Start", "End"):
        match = re.search(PARTICIPANT.format(role), page)
        if match:
            participants[role] = (int(match.group(1)),
                                  html.unescape(match.group(2)).strip())

    # The quest giver speaks the offer; the turn-in NPC speaks the rest. Where
    # Wowhead lists only one, it serves for both.
    giver = participants.get("Start") or participants.get("End")
    ender = participants.get("End") or participants.get("Start")

    passages = {}
    match = DESCRIPTION.search(page)
    if match:
        passages["description"] = (clean(match.group(1)), giver)
    for name in ("progress", "completion"):
        match = re.search(SECTION.format(name), page, re.S)
        if match:
            passages[name] = (clean(match.group(1)), ender)

    return title, passages


def read_ids(path):
    ids = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    match = re.match(r"(\d+)", line)
                    if match:
                        ids.append(int(match.group(1)))
    except FileNotFoundError:
        sys.exit(f"No such file: {path}\n"
                 f"Expected a list of quest IDs, one per line. /qrmissing in "
                 f"the addon prints the ones a player actually hit.")
    if not ids:
        sys.exit(f"{path} contained no quest IDs.")
    return ids


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", required=True,
                        help="file of quest IDs, one per line")
    parser.add_argument("-o", "--output")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="seconds between requests (default 1.5)")
    parser.add_argument("--refresh", action="store_true",
                        help="refetch pages already cached")
    parser.add_argument("--player-name", default="adventurer")
    parser.add_argument("--gender", choices=("male", "female"), default="male")
    args = parser.parse_args()

    quest_ids = read_ids(args.ids)
    print(f"Fetching {len(quest_ids):,} quest(s) at {args.delay}s intervals. "
          f"Cached pages are reused.", file=sys.stderr)

    records, missing, no_npc, unknown = [], [], 0, set()
    for position, quest_id in enumerate(quest_ids, 1):
        try:
            page = fetch(quest_id, args.delay, args.refresh)
        except Blocked as exc:
            # Write what was gathered rather than losing it; the cache means
            # a later run pays nothing to redo this ground.
            print(f"\nBlocked at quest {quest_id} ({position}/"
                  f"{len(quest_ids)}): {exc}\nWriting what was fetched. "
                  f"Rerun later — cached pages are skipped, so the run "
                  f"resumes where this one stopped.", file=sys.stderr)
            break
        if page is None:
            missing.append(quest_id)
            continue

        title, passages = extract(quest_id, page)
        if not passages:
            missing.append(quest_id)
            continue

        for passage, (raw, speaker) in passages.items():
            spoken, leftovers = normalize(raw, args.player_name, args.gender)
            if not spoken:
                continue
            unknown.update(leftovers)
            if not speaker:
                no_npc += 1
            records.append({
                "questID": quest_id,
                "passage": passage,
                "title": title,
                "npcID": speaker[0] if speaker else "",
                "npcName": speaker[1] if speaker else "",
                "soundFile": f"{quest_id}_{passage}.ogg",
                "text": spoken,
            })

        if position % 25 == 0:
            print(f"  {position}/{len(quest_ids)}", file=sys.stderr)

    if not records:
        sys.exit("No quest text found for any of the given IDs.")

    out = open(args.output, "w", encoding="utf-8", newline="") \
        if args.output else sys.stdout
    try:
        if args.format == "json":
            json.dump({"source": "wowhead", "passages": records}, out,
                      indent=2, ensure_ascii=False)
            out.write("\n")
        else:
            writer = csv.DictWriter(out, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
    finally:
        if args.output:
            out.close()

    print(f"Wrote {len(records):,} passage(s) to {args.output or 'stdout'}.",
          file=sys.stderr)
    if missing:
        print(f"  {len(missing)} quest(s) had no text on Wowhead "
              f"(too new, or not player-facing): "
              f"{', '.join(str(q) for q in missing[:8])}"
              f"{' ...' if len(missing) > 8 else ''}", file=sys.stderr)
    if no_npc:
        print(f"  {no_npc} passage(s) have no NPC listed.", file=sys.stderr)
    if unknown:
        print(f"  Unhandled markup, check before generating: "
              f"{', '.join(sorted(unknown))}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
