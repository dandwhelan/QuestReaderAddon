#!/usr/bin/env python3
"""Turn harvested quest text into synthesis-ready input.

Reads the SavedVariables file written by QuestReaderHarvester and emits one
record per spoken passage: the quest, which NPC says it, and the text with
Blizzard's substitution tokens expanded into something speakable.

That expansion is the point of this script. Quest text is written for display,
not speech, and carries markup a voice model will otherwise read aloud:

    $n / $p   the player's name          $b   line break
    $r        the player's race          $c   the player's class
    $g a:b;   gender-conditional text    |cFF...|r  colour codes

A model handed "$b$bThe blood of Ula'tek stirs, $n." will pronounce the dollar
signs. Normalizing has to happen before generation, because fixing it later
means regenerating every affected file.

Usage:
    harvest_export.py QuestReaderHarvester.lua -o passages.json
    harvest_export.py QuestReaderHarvester.lua --format csv -o passages.csv
    harvest_export.py QuestReaderHarvester.lua --player-name "champion"
"""

import argparse
import csv
import json
import re
import sys

PASSAGES = ("description", "progress", "completion")

# Colour escapes and embedded textures carry no speech content.
COLOR_OPEN = re.compile(r"\|c[0-9A-Fa-f]{8}")
COLOR_CLOSE = re.compile(r"\|r")
TEXTURE = re.compile(r"\|T.-\|t")
# $g male:female;  — also written $G, and the separator may be absent.
GENDER = re.compile(r"\$[gG]\s*([^:;]*):([^;]*);")
# |4singular:plural; picks a form by an adjacent number.
PLURAL = re.compile(r"\|4([^:;]*):([^;]*);")


class LuaParseError(Exception):
    pass


def parse_lua_table(text):
    """Parse the subset of Lua that SavedVariables files are written in.

    Only what the game emits is supported: nested tables, string and numeric
    keys, strings, numbers, booleans and nil. That is enough, and it avoids
    executing a file that arrives from a game client.
    """
    pos = 0
    n = len(text)

    def error(msg):
        line = text.count("\n", 0, pos) + 1
        raise LuaParseError(f"line {line}: {msg}")

    def skip():
        nonlocal pos
        while pos < n:
            ch = text[pos]
            if ch in " \t\r\n":
                pos += 1
            elif text.startswith("--", pos):
                end = text.find("\n", pos)
                pos = n if end == -1 else end + 1
            else:
                break

    def parse_string():
        nonlocal pos
        quote = text[pos]
        pos += 1
        out = []
        while pos < n:
            ch = text[pos]
            if ch == "\\":
                nxt = text[pos + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                pos += 2
            elif ch == quote:
                pos += 1
                return "".join(out)
            else:
                out.append(ch)
                pos += 1
        error("unterminated string")

    def parse_value():
        nonlocal pos
        skip()
        if pos >= n:
            error("unexpected end of input")
        ch = text[pos]
        if ch in "\"'":
            return parse_string()
        if ch == "{":
            return parse_table()
        match = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?").match(text, pos)
        if match:
            pos = match.end()
            raw = match.group()
            return float(raw) if any(c in raw for c in ".eE") else int(raw)
        for literal, value in (("true", True), ("false", False), ("nil", None)):
            if text.startswith(literal, pos):
                pos += len(literal)
                return value
        error(f"unexpected character {ch!r}")

    def parse_key():
        nonlocal pos
        skip()
        if text[pos] == "[":
            pos += 1
            key = parse_value()
            skip()
            if pos >= n or text[pos] != "]":
                error("expected ] after key")
            pos += 1
        else:
            match = re.compile(r"[A-Za-z_]\w*").match(text, pos)
            if not match:
                error("expected key")
            key = match.group()
            pos = match.end()
        skip()
        if pos >= n or text[pos] != "=":
            error("expected = after key")
        pos += 1
        return key

    def parse_table():
        nonlocal pos
        pos += 1  # consume {
        table, index = {}, 1
        while True:
            skip()
            if pos >= n:
                error("unterminated table")
            if text[pos] == "}":
                pos += 1
                return table
            save = pos
            try:
                key = parse_key()
            except LuaParseError:
                pos = save
                key = index
                index += 1
            table[key] = parse_value()
            skip()
            if pos < n and text[pos] in ",;":
                pos += 1

    skip()
    # Files open with "VariableName = {" — step over the assignment.
    match = re.compile(r"[A-Za-z_]\w*\s*=\s*").match(text, pos)
    if match:
        pos = match.end()
    return parse_value()


def normalize(text, player_name="adventurer", gender="male"):
    """Rewrite display markup into speakable prose."""
    if not text:
        return ""

    text = TEXTURE.sub("", text)
    text = COLOR_OPEN.sub("", text)
    text = COLOR_CLOSE.sub("", text)

    # Keep the first form for gendered and plural alternatives; a single
    # recorded line cannot voice both, and the choice must be consistent.
    text = GENDER.sub(lambda m: m.group(1 if gender == "male" else 2).strip(), text)
    text = PLURAL.sub(lambda m: m.group(1).strip(), text)

    # $b is a paragraph break. A sentence break reads better than a dropped
    # space, which would run two sentences together.
    text = re.sub(r"\$[bB]", "\n", text)
    text = re.sub(r"\|n", "\n", text)

    text = re.sub(r"\$[nNpP]", player_name, text)
    # Race and class have no value at generation time; the neutral noun keeps
    # the sentence grammatical.
    text = re.sub(r"\$[rR]", "traveler", text)
    text = re.sub(r"\$[cC]", "hero", text)

    # Anything left is markup this function does not know about. Report it
    # rather than pass it to the model.
    leftovers = re.findall(r"\$\w|\|\w", text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), leftovers


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("savedvariables",
                        help="path to QuestReaderHarvester.lua")
    parser.add_argument("-o", "--output")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--player-name", default="adventurer",
                        help="stands in for $n (default: adventurer)")
    parser.add_argument("--gender", choices=("male", "female"), default="male",
                        help="which side of $g to keep (default: male)")
    args = parser.parse_args()

    try:
        with open(args.savedvariables, encoding="utf-8") as handle:
            data = parse_lua_table(handle.read())
    except FileNotFoundError:
        sys.exit(f"No such file: {args.savedvariables}\n"
                 f"Look in your WoW install under "
                 f"WTF/Account/<ACCOUNT>/SavedVariables/.")
    except LuaParseError as exc:
        sys.exit(f"Could not parse {args.savedvariables}: {exc}")

    quests = data.get("quests") or {}
    gossip = data.get("gossip") or {}
    if not quests and not gossip:
        sys.exit("Nothing recorded. Play through some quest dialogue or talk "
                 "to NPCs with the harvester enabled, then /reload to flush "
                 "SavedVariables.")

    records, unknown_markup, no_npc = [], set(), 0
    for quest_id, entry in sorted(quests.items(), key=lambda kv: str(kv[0])):
        if not isinstance(entry, dict):
            continue
        for passage in PASSAGES:
            captured = entry.get(passage)
            if not isinstance(captured, dict) or not captured.get("text"):
                continue
            spoken, leftovers = normalize(
                captured["text"], args.player_name, args.gender)
            unknown_markup.update(leftovers)
            if not captured.get("npcID"):
                no_npc += 1
            records.append({
                "questID": quest_id,
                "passage": passage,
                "title": entry.get("title", ""),
                "npcID": captured.get("npcID") or "",
                "npcName": captured.get("npcName") or "",
                "soundFile": f"{quest_id}_{passage}.ogg",
                "text": spoken,
            })

    # Gossip has no quest to key on — an NPC that gives no quest still has a
    # greeting. "questID" holds "npc<id>" instead of a number here, which
    # keeps every downstream tool (synthesize.py, build_soundlengths.py)
    # working unmodified: they only ever treat the field as an opaque prefix
    # for the sound filename, never as an actual quest lookup.
    for npc_id, entry in sorted(gossip.items(), key=lambda kv: str(kv[0])):
        if not isinstance(entry, dict):
            continue
        raw_texts = entry.get("texts")
        if not isinstance(raw_texts, dict):
            continue
        # Lua's array-style tables parse as a dict keyed "1", "2", ... rather
        # than a Python list, so order is recovered by sorting the keys
        # numerically -- capture order, which is what the addon's own
        # gossip1/gossip2 filenames are numbered against.
        texts = [raw_texts[k] for k in sorted(raw_texts, key=lambda k: int(k))]
        for index, text in enumerate(texts, 1):
            if not text:
                continue
            spoken, leftovers = normalize(
                text, args.player_name, args.gender)
            unknown_markup.update(leftovers)
            key = f"npc{npc_id}"
            passage = f"gossip{index}"
            records.append({
                "questID": key,
                "passage": passage,
                "title": entry.get("npcName", ""),
                "npcID": npc_id,
                "npcName": entry.get("npcName") or "",
                "soundFile": f"{key}_{passage}.ogg",
                "text": spoken,
            })

    out = open(args.output, "w", encoding="utf-8", newline="") \
        if args.output else sys.stdout
    try:
        if args.format == "json":
            json.dump({"locale": data.get("locale"),
                       "build": data.get("build"),
                       "passages": records}, out, indent=2, ensure_ascii=False)
            out.write("\n")
        else:
            writer = csv.DictWriter(out, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
    finally:
        if args.output:
            out.close()

    where = args.output or "stdout"
    print(f"Wrote {len(records):,} passage(s) from {len(quests):,} quest(s) "
          f"and {len(gossip):,} gossip NPC(s) to {where}.", file=sys.stderr)
    if no_npc:
        print(f"  {no_npc} passage(s) have no NPC and cannot be voice-matched.",
              file=sys.stderr)
    if unknown_markup:
        print(f"  Unhandled markup, check before generating: "
              f"{', '.join(sorted(unknown_markup))}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
