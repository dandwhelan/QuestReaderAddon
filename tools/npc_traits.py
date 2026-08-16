#!/usr/bin/env python3
"""Look up the race, sex and creature type of NPCs, for fallback voices.

Around 80% of voiced NPCs have enough reference audio to clone. The rest — the
generic, unnamed quest givers — have none, and need a stand-in voice. Assigning
those at random makes a dwarf sound like a dryad, so this resolves what each
NPC actually is and groups them, letting one chosen voice serve every NPC that
shares a race and sex.

The client tables carry it: Creature holds the name and creature type, and
Creature -> CreatureDisplayInfo -> CreatureDisplayInfoExtra reaches the race and
sex of the model. Coverage is not complete — creatures whose model has no
extended display record, and brand-new NPCs absent from the published tables,
resolve only as far as their creature type.

Usage:
    npc_traits.py "Elder Hagar" "Zul'jarra"
    npc_traits.py --names npcs.txt --format csv -o traits.csv
    npc_traits.py --names npcs.txt --groups
"""

import argparse
import csv
import os
import re
import sys
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
TABLES = ("Creature", "CreatureDisplayInfo", "CreatureDisplayInfoExtra")

SEX = {"0": "male", "1": "female", "2": "neutral", "3": "both"}
CREATURE_TYPE = {
    "1": "Beast", "2": "Dragonkin", "3": "Demon", "4": "Elemental", "5": "Giant",
    "6": "Undead", "7": "Humanoid", "8": "Critter", "9": "Mechanical",
    "10": "Unspecified", "11": "Totem", "12": "Non-combat Pet",
    "13": "Gas Cloud", "14": "Wild Pet", "15": "Aberration",
}
RACE = {
    "1": "Human", "2": "Orc", "3": "Dwarf", "4": "Night Elf", "5": "Undead",
    "6": "Tauren", "7": "Gnome", "8": "Troll", "9": "Goblin", "10": "Blood Elf",
    "11": "Draenei", "22": "Worgen", "24": "Pandaren", "27": "Nightborne",
    "28": "Highmountain Tauren", "29": "Void Elf", "30": "Lightforged Draenei",
    "31": "Zandalari Troll", "32": "Kul Tiran", "34": "Dark Iron Dwarf",
    "35": "Vulpera", "36": "Mag'har Orc", "37": "Mechagnome",
    "52": "Dracthyr", "70": "Dracthyr", "84": "Earthen", "85": "Haranir",
}


def fold(name):
    """Reduce a display name to letters and digits, matching sound folders."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def fetch_table(name, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{name}.csv")
    if refresh or not os.path.exists(path):
        print(f"Downloading {name} ...", file=sys.stderr)
        urllib.request.urlretrieve(f"https://wago.tools/db2/{name}/csv", path)
    return path


def load_tables(refresh=False):
    creatures, by_name = {}, {}
    with open(fetch_table("Creature", refresh), encoding="utf-8",
              errors="replace") as handle:
        for row in csv.DictReader(handle):
            # Any of the four display slots may hold the model; the first
            # populated one is the NPC's usual appearance.
            displays = [row.get(f"DisplayID_{i}") for i in range(4)]
            record = {
                "name": row.get("Name_lang", ""),
                "type": CREATURE_TYPE.get(row.get("CreatureType"), "?"),
                "displays": [d for d in displays if d and d != "0"],
            }
            creatures[row["ID"]] = record
            # Fold to the same shape the sound folders use, so a name arriving
            # as "zuljarra" still finds the creature named "Zul'jarra".
            by_name.setdefault(fold(record["name"]), row["ID"])

    display_to_extra = {}
    with open(fetch_table("CreatureDisplayInfo", refresh), encoding="utf-8",
              errors="replace") as handle:
        for row in csv.DictReader(handle):
            extra = row.get("ExtendedDisplayInfoID")
            if extra and extra != "0":
                display_to_extra[row["ID"]] = extra

    extras = {}
    with open(fetch_table("CreatureDisplayInfoExtra", refresh),
              encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            extras[row["ID"]] = (row.get("DisplayRaceID"),
                                 row.get("DisplaySexID"))

    return creatures, by_name, display_to_extra, extras


def resolve(name_or_id, tables):
    creatures, by_name, display_to_extra, extras = tables
    key = str(name_or_id).strip()
    creature_id = key if key.isdigit() and key in creatures \
        else by_name.get(fold(key))
    if not creature_id:
        return None

    record = creatures[creature_id]
    race = sex = None
    for display in record["displays"]:
        extra_id = display_to_extra.get(display)
        if extra_id and extra_id in extras:
            raw_race, raw_sex = extras[extra_id]
            race = RACE.get(raw_race, f"race {raw_race}" if raw_race else None)
            sex = SEX.get(raw_sex)
            break

    return {
        "npcID": creature_id,
        "npcName": record["name"],
        "type": record["type"],
        "race": race or "",
        "sex": sex or "",
        # The key a fallback voice is chosen for. NPCs sharing it can share a
        # voice without sounding wrong.
        "voiceGroup": "_".join(p.lower().replace(" ", "-") for p in
                               (race or record["type"], sex or "neutral")),
    }


def read_names(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return [l.strip() for l in handle
                    if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        sys.exit(f"No such file: {path}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="NPC names or creature IDs")
    parser.add_argument("--names-file", "--names", dest="names_file",
                        help="file with one NPC name per line")
    parser.add_argument("-o", "--output")
    parser.add_argument("--format", choices=("table", "csv"), default="table")
    parser.add_argument("--groups", action="store_true",
                        help="summarise by voice group instead of per NPC")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    wanted = list(args.names)
    if args.names_file:
        wanted.extend(read_names(args.names_file))
    if not wanted:
        sys.exit("Give NPC names as arguments or with --names-file.")

    tables = load_tables(args.refresh)
    rows, unresolved = [], []
    for name in wanted:
        found = resolve(name, tables)
        if found:
            rows.append(found)
        else:
            unresolved.append(name)

    if args.groups:
        groups = {}
        for row in rows:
            groups.setdefault(row["voiceGroup"], []).append(row["npcName"])
        print(f"{len(groups)} voice group(s) for {len(rows)} NPC(s):\n")
        for group, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(members):>4}  {group}")
            print(f"        {', '.join(sorted(members)[:6])}"
                  f"{' ...' if len(members) > 6 else ''}")
    elif rows:
        out = open(args.output, "w", encoding="utf-8", newline="") \
            if args.output else sys.stdout
        try:
            if args.format == "csv":
                writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            else:
                out.write(f"{'NPC':<26}{'type':<12}{'race':<20}"
                          f"{'sex':<9}voice group\n")
                for row in rows:
                    out.write(f"{row['npcName'][:25]:<26}{row['type']:<12}"
                              f"{row['race'] or '-':<20}{row['sex'] or '-':<9}"
                              f"{row['voiceGroup']}\n")
        finally:
            if args.output:
                out.close()

    if unresolved:
        print(f"\n{len(unresolved)} NPC(s) not in the client tables "
              f"(too new, or a differing name): "
              f"{', '.join(unresolved[:8])}"
              f"{' ...' if len(unresolved) > 8 else ''}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
