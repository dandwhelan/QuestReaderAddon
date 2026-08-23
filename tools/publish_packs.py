#!/usr/bin/env python3
"""Refresh every per-expansion/content-type pack repo from the current
Sounds/ + SoundLengths.lua, and push it.

This is the step that was done by hand today: build_sound_packs.py, then for
each pack directory, clone its repo fresh, replace the content, commit if
anything changed, push. Doing it by hand does not scale past a handful of
repos and is exactly the kind of step that gets skipped under time pressure,
which is how a pack repo goes stale against the library silently.

A fresh clone each run (not a persistent local checkout) trades a slower run
for zero state to get wrong -- no stale branch, no leftover merge conflict
from a run that died halfway, nothing to resync by hand.

build_sound_packs.py can now split one oversized pack (e.g. Classic, once it
crosses --max-pack-mb) into several sequential sub-packs -- Classic_Part1,
Classic_Part2, etc. Each of those is just another directory under the build
output, named QuestReaderAddon_Pack_Classic_Part1 and so on, so this script
needed no structural change to *handle* them: the existing "one repo per
pack directory name" loop already treats a sub-pack exactly like any other
pack. What it did need: (1) never assume a repo already exists -- a
newly-split pack has no repo yet, by definition, so a clean, unambiguous
report of exactly which repo names this run expects, up front, before
touching git at all, so a missing repo is a known, expected gap rather than
a cryptic clone failure buried in the middle of a long run; and (2) the
"never publish Unsorted" rule has to match Unsorted_Part1, Unsorted_Part2,
etc. too, not just the literal name "Unsorted".

This script never creates a GitHub repo itself -- see the summary printed at
the end for exactly which repo(s) the owner still needs to create by hand.

Usage:
    python tools/publish_packs.py --github-user dandwhelan
    python tools/publish_packs.py --github-user dandwhelan --only Midnight,Classic
    python tools/publish_packs.py --github-user dandwhelan --dry-run
    python tools/publish_packs.py --github-user dandwhelan --max-pack-mb 500
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO_ROOT, "tools")
PART_SUFFIX = re.compile(r"^(.*)(_Part\d+)$")


def base_pack_name(pack_name):
    """"Classic_Part1" -> "Classic"; "Midnight" -> "Midnight" unchanged.

    Used only to apply name-based rules (e.g. "never publish Unsorted") to
    every sub-pack of a split pack, not just an exact, unsplit match.
    """
    match = PART_SUFFIX.match(pack_name)
    return match.group(1) if match else pack_name


def run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{result.stderr}")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--github-user", required=True)
    ap.add_argument("--only", help="comma-separated pack names, e.g. Midnight,Classic")
    ap.add_argument("--dry-run", action="store_true",
                     help="build packs and show what would be pushed, push nothing")
    ap.add_argument("--max-pack-mb", type=float, default=None,
                     help="passed through to build_sound_packs.py; leave "
                          "unset to use its own default (500 MB)")
    args = ap.parse_args()

    build_dir = os.path.join(REPO_ROOT, "packs_build")
    print("Building packs from current Sounds/ + SoundLengths.lua ...")
    build_cmd = [sys.executable, os.path.join(TOOLS, "build_sound_packs.py"),
                 "--out", build_dir]
    if args.max_pack_mb is not None:
        build_cmd += ["--max-pack-mb", str(args.max_pack_mb)]
    run(build_cmd)

    only = set(args.only.split(",")) if args.only else None
    pack_dirs = sorted(d for d in os.listdir(build_dir)
                       if os.path.isdir(os.path.join(build_dir, d))
                       and d.startswith("QuestReaderAddon_Pack_"))

    # This repo never creates GitHub repos itself (see the module docstring
    # and CLAUDE.md's hard constraints) -- so the very first thing this run
    # does is state, plainly and up front, exactly which repos it is about
    # to assume exist. A sub-pack that was just created by a fresh split
    # (e.g. Classic_Part2, the first time Classic crossed --max-pack-mb) has
    # no repo yet by definition; that should read as "known, expected gap
    # the owner needs to fill in," not a mysterious clone failure halfway
    # through a long run.
    expected = []
    for pack_dir_name in pack_dirs:
        pack_name = pack_dir_name.replace("QuestReaderAddon_Pack_", "")
        if only and pack_name not in only:
            continue
        if base_pack_name(pack_name) == "Unsorted":
            continue
        expected.append((pack_name, pack_dir_name))

    print(f"\nThis run expects the following {len(expected)} repo(s) to "
          f"already exist under https://github.com/{args.github_user}/ "
          f"(this script will not create any of them):")
    for pack_name, pack_dir_name in expected:
        print(f"  - {pack_dir_name}")

    created_missing = []
    pushed = []
    unchanged = []

    for pack_name, pack_dir_name in expected:
        src = os.path.join(build_dir, pack_dir_name)
        repo_url = f"https://github.com/{args.github_user}/{pack_dir_name}.git"
        print(f"\n=== {pack_dir_name} ===")

        if args.dry_run:
            n = sum(len(f) for _, _, f in os.walk(src))
            print(f"  would sync {n} file(s) to {repo_url}")
            continue

        with tempfile.TemporaryDirectory() as clone_dir:
            clone = run(["git", "clone", "--depth", "1", repo_url, clone_dir],
                       check=False)
            if clone.returncode != 0:
                print(f"  repo does not exist or is unreachable -- skipping "
                      f"(create it at {repo_url.removesuffix('.git')} first): "
                      f"{clone.stderr.strip()[:200]}")
                created_missing.append(pack_dir_name)
                continue

            for name in os.listdir(clone_dir):
                if name == ".git":
                    continue
                path = os.path.join(clone_dir, name)
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            for name in os.listdir(src):
                s, d = os.path.join(src, name), os.path.join(clone_dir, name)
                shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)

            run(["git", "add", "-A"], cwd=clone_dir)
            status = run(["git", "status", "--porcelain"], cwd=clone_dir)
            if not status.stdout.strip():
                print("  no changes")
                unchanged.append(pack_dir_name)
                continue

            run(["git", "commit", "-q", "-m",
                 f"Refresh {pack_name} pack from current library"], cwd=clone_dir)
            run(["git", "push"], cwd=clone_dir)
            print(f"  pushed changes")
            pushed.append(pack_dir_name)

    if not args.dry_run:
        print(f"\nSummary: {len(pushed)} pushed, {len(unchanged)} unchanged, "
              f"{len(created_missing)} missing repo(s).")
        if created_missing:
            print("Repos that need to be created before they can be "
                  "published (this script will not create them):")
            for name in created_missing:
                print(f"  - https://github.com/{args.github_user}/{name}")

    print("\nDone.")


if __name__ == "__main__":
    main()
