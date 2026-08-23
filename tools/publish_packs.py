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

Usage:
    python tools/publish_packs.py --github-user dandwhelan
    python tools/publish_packs.py --github-user dandwhelan --only Midnight,Classic
    python tools/publish_packs.py --github-user dandwhelan --dry-run
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO_ROOT, "tools")


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
    args = ap.parse_args()

    build_dir = os.path.join(REPO_ROOT, "packs_build")
    print("Building packs from current Sounds/ + SoundLengths.lua ...")
    run([sys.executable, os.path.join(TOOLS, "build_sound_packs.py"),
         "--out", build_dir])

    only = set(args.only.split(",")) if args.only else None
    pack_dirs = sorted(d for d in os.listdir(build_dir)
                       if os.path.isdir(os.path.join(build_dir, d))
                       and d.startswith("QuestReaderAddon_Pack_"))

    for pack_dir_name in pack_dirs:
        pack_name = pack_dir_name.replace("QuestReaderAddon_Pack_", "")
        if only and pack_name not in only:
            continue
        # Unsorted is not a real pack -- its whole point is "not yet mapped",
        # publishing it would ship the exact bloat the split exists to avoid.
        if pack_name == "Unsorted":
            continue

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
                print(f"  clone failed (repo missing?) -- skipping: "
                      f"{clone.stderr.strip()[:200]}")
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
                continue

            run(["git", "commit", "-q", "-m",
                 f"Refresh {pack_name} pack from current library"], cwd=clone_dir)
            run(["git", "push"], cwd=clone_dir)
            print(f"  pushed changes")

    print("\nDone.")


if __name__ == "__main__":
    main()
