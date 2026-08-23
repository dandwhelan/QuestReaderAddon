# Publishing SpeakStone Narration and its expansion packs

Written 23 Aug 2026. This addon has **not been published anywhere yet** —
this document is a walkthrough for the first publish of the base addon
(`QuestReaderAddon`, displayed as "SpeakStone Narration") plus the growing
family of `QuestReaderAddon_Pack_<Expansion>` companion addons.

## How to read this document

Every claim below is tagged:

- **[VERIFIED]** — confirmed against files actually in this repo, or in the
  local HandyNotes install, as read for this task.
- **[GENERAL KNOWLEDGE — VERIFY]** — from prior training knowledge of
  CurseForge/WoWInterface. This task was done **without internet access**
  (a separate rate-limited process owns the network budget right now), so
  none of this could be checked against the live sites. Platform UIs,
  policies, size limits, and API details change over time and may already
  be wrong. **Confirm every one of these against the live site before
  relying on it.**
- **[UNKNOWN — CHECK LIVE]** — a specific gap where prior knowledge is not
  reliable enough to state anything concrete (approval turnaround, exact
  current size caps, etc). Do not fill these in from guesswork; check the
  site.

A full checklist of everything tagged for verification is repeated at the
end.

---

## 1. Prerequisites

**[GENERAL KNOWLEDGE — VERIFY]**

Before the first upload to either site:

- **CurseForge**: a free CurseForge/Overwolf account. Project creation
  historically happens at `authors.curseforge.com` (formerly
  `wow.curseforge.com/addons/new` under the old site). There has historically
  been little or no manual "author verification" gate beyond having a
  verified email — but CurseForge has changed its authoring flow more than
  once (it moved from the old Twitch/Curse site to the "CurseForge Core"
  author dashboard). **Confirm the current URL and signup flow live.**
- **WoWInterface**: a free WoWInterface forum-style account is required to
  submit an addon. WoWInterface's submission process has historically been
  more manual/curated than CurseForge's (a form with fields for description,
  category, and a manually uploaded zip, sometimes with a review delay
  before the addon appears). **[UNKNOWN — CHECK LIVE]** whether WoWInterface
  currently has any additional author-verification or waiting period.
- **Both sites** identify addons primarily by the zip's internal folder
  name and the `.toc` file inside it, not by any separate "addon ID" you
  set up first — the project is a container you create, then upload
  versions into.
- **Have your `## Author:` field decided before creating projects** — it's
  user-facing on both sites and, per the `.toc` files in this repo, is
  currently `clhammer weirdautomation@gmail.com, Paratus` **[VERIFIED —
  QuestReaderAddon.toc]**. Decide if that's the intended public author
  string before the first project goes live; it's awkward to have two very
  different-looking listings if it's changed after packs are already up.

---

## 2. CurseForge

### 2.1 Creating a project

**[GENERAL KNOWLEDGE — VERIFY]** Roughly: log in to the author dashboard,
"Create Project," choose "WoW Retail" (and/or Classic/Cata Classic, etc. as
separate game-version flags) as the game, give it a name and a URL slug,
pick a license. The project starts private/unlisted until you publish a
file and submit for approval.

### 2.2 Category/tag choices

**[GENERAL KNOWLEDGE — VERIFY]** CurseForge's WoW addon categories have
included things like "Quests & Leveling," "Audio & Video," "Immersion &
RPG," "Data Broker" (relevant here because of the `LibDataBroker-1.1` /
`LibDBIcon-1.0` minimap-icon integration in `QuestReaderAddon.toc`
**[VERIFIED]**), and "Buffs & Debuffs" is clearly not relevant. Given this
addon plays AI-generated voiceover for quest/gossip/book text
(`## Notes: Plays AI-generated voiceovers for quests, books, and gossip.`
**[VERIFIED — QuestReaderAddon.toc]**), the closest real-world fits are
likely **"Audio & Video"** as primary and **"Quests & Leveling"** or
**"Immersion & RPG"** as secondary. **Exact current category names and
whether multi-select is allowed must be checked live** — CurseForge has
reorganized its category taxonomy before.

### 2.3 Upload expectations: zip layout

**[VERIFIED, cross-checked against the packager convention and HandyNotes]**
The zip's top-level folder name must match the addon's actual folder name —
the same name the `.toc` file is named after. For the base addon that's
`QuestReaderAddon/` containing `QuestReaderAddon.toc`; for a pack it's
`QuestReaderAddon_Pack_Cataclysm/` containing
`QuestReaderAddon_Pack_Cataclysm.toc` (confirmed present in this repo at
`packs/QuestReaderAddon_Pack_Cataclysm/`). This is not a stylistic
preference — WoW's addon loader keys off the folder name, and CurseForge
(and every other distributor) preserves that folder name when unzipping
into `Interface/AddOns/`. A zip whose internal folder doesn't match the
`.toc` file name inside it, or has extra nesting (e.g. a zip that unpacks
to `MyRepo-v1.2/QuestReaderAddon/...`) will install wrong or not load. This
is exactly what `pkgmeta.yaml`'s `package-as: QuestReaderAddon`
**[VERIFIED]** exists to guarantee when the CurseForge packager builds the
zip — see 2.4.

### 2.4 Game-version tagging vs. `## Interface:`

**[VERIFIED for what's in the repo, GENERAL KNOWLEDGE for the CurseForge
side]** `QuestReaderAddon.toc` currently declares:

```
## Interface: 110207, 120000, 120001, 120100
## X-Interface: 110207, 120000, 120001, 120100
```

(multi-interface support, i.e. it declares compatibility across several
client builds at once — retail 11.x and what look like 12.x builds).
CurseForge's file-upload form has historically had you pick supported
"Game Versions" (e.g. "The War Within," "Midnight," specific patch numbers)
as flags on each uploaded file, separately from the `.toc`'s own
`## Interface:` numbers — the two are meant to agree, but CurseForge does
not currently (to prior knowledge) parse `## Interface:` to auto-select
game versions; you tag the file yourself. **Verify live whether CurseForge
now auto-detects Interface versions from the toc** — some client-side
addons/tools do this and CurseForge's own uploader UI has changed over the
past few years.

### 2.5 How the CurseForge packager interacts with a GitHub repo

**[VERIFIED from `pkgmeta.yaml`, GENERAL KNOWLEDGE for the CurseForge-side
mechanics]**

This repo already has a `pkgmeta.yaml`:

```yaml
package-as: QuestReaderAddon
enable-nolib-creation: no
ignore:
  - README.md
  - .github
  - .git
  - .gitignore
manual-changelog:
  filename: CHANGELOG.md
  markup-type: markdown
license-output: LICENSE
tools-used:
  - curseforge-packager
```

This is the standard config file for the (now-legacy but still supported,
to prior knowledge) `curseforge-packager` / "BigWigsMods packager" tooling
that CurseForge's own release automation and GitHub Actions workflows both
know how to read. **[GENERAL KNOWLEDGE — VERIFY]** the mechanics: once a
CurseForge project is linked to this GitHub repo (a setting on the project,
"Repository" or "VCS" tab), CurseForge can watch for **tags** and/or
**GitHub Releases** and automatically run the packager against the tagged
commit, producing a zip and publishing it as a new file version — without a
manual upload. The packager reads `pkgmeta.yaml` to know the package name
(`package-as`), what to strip (`ignore`), and where to source a changelog.
**Confirm live**: whether project-to-repo linking is still done the same
way, whether it requires a webhook/OAuth grant to the CurseForge GitHub App,
and whether it's tag-triggered, release-triggered, or both — this has been
described differently across CurseForge's own docs over time.

**Important gap for the multi-addon case**: `pkgmeta.yaml` lives at the
repo root and is written for *this* repo publishing *one* package
(`QuestReaderAddon`). If a pack (e.g. `QuestReaderAddon_Pack_Cataclysm`)
is published from the same monorepo rather than its own repo, it needs its
own `pkgmeta.yaml` alongside its own `.toc`, with `package-as:` set to that
pack's addon name, and the CurseForge project pointed at that subdirectory
as its packager root. **[VERIFIED there is currently only one
`pkgmeta.yaml` in the repo, at the root]** — none exists yet under
`packs/QuestReaderAddon_Pack_Cataclysm/`, so this is a real to-do, not
already handled. `docs/sound_packs.md` §7 in this repo independently notes
the same gap and recommends the same fix (copy the root `pkgmeta.yaml` into
each pack folder, change `package-as`).

Note also that **today the actual publishing mechanism used for packs is
not CurseForge-linked automation at all** — `tools/publish_packs.py`
**[VERIFIED]** pushes each pack's built output to its *own* separate GitHub
repo (`https://github.com/<user>/QuestReaderAddon_Pack_<Name>.git`), not a
subdirectory of this monorepo. If that one-repo-per-pack layout is kept
(rather than moving to a single monorepo with per-pack `pkgmeta.yaml`
files), then each pack repo needs its own top-level `pkgmeta.yaml` — the
monorepo-subdirectory approach described in `docs/sound_packs.md` §7 is an
alternative to that, not what's actually running today. Pick one before
wiring up CurseForge auto-packaging; don't half-do both.

### 2.6 File-size cap

**[GENERAL KNOWLEDGE — the 500 MB figure is stated as fact in
`docs/expansion_addons_plan.md` and `docs/sound_packs.md`, both already in
this repo, so it is being treated as [VERIFIED] for this document's
purposes, but was not independently re-checked against curseforge.com]**
CurseForge caps a single uploaded file at 500 MB. See §4 for how this
interacts with the pack split.

---

## 3. WoWInterface

**[GENERAL KNOWLEDGE — VERIFY, more so than the CurseForge section above,
since WoWInterface's submission flow is less standardized/automatable]**

### 3.1 Creating a project / submitting

Historically: log in, go to the "Submit a File" / "Author" area (something
like `wowinterface.com/downloads/addfile.php` in the old site layout), fill
in a title, category, description, choose a version of WoW it supports, and
upload a zip directly through the web form. There is not (to prior
knowledge) a linked-repo/auto-packager pipeline analogous to CurseForge's —
**WoWInterface uploads are more likely to require a manual zip upload per
release** rather than a tag-triggered build. **Verify live** whether this
has changed; WoWInterface's own toolchain has historically lagged
CurseForge's in automation.

### 3.2 Zip layout

Same underlying constraint as CurseForge — **[VERIFIED — this is a WoW
client fact, not a platform-specific one]**: the zip's top-level folder
must match the addon folder / `.toc` name, for the same load-order reasons
as §2.3.

### 3.3 Categories

**[GENERAL KNOWLEDGE — VERIFY]** WoWInterface's category list has
historically included groupings like "Quests & Leveling," "General," "Data
Export," "Sound" or "Audio" categories may or may not exist as a distinct
top-level category — **[UNKNOWN — CHECK LIVE]**, this project's own
knowledge does not have confident recall of WoWInterface's current category
tree, which has been reshuffled at various points.

### 3.4 Where WoWInterface differs from CurseForge, per prior knowledge

- **[GENERAL KNOWLEDGE — VERIFY]** No confident recall of a GitHub-linked
  auto-packager equivalent — assume a manual zip re-upload is needed for
  every release unless the live site shows otherwise.
- **[UNKNOWN — CHECK LIVE]** Exact per-file size limit. Do not assume it
  matches CurseForge's 500 MB; WoWInterface is a smaller, differently-run
  site and this repo has no data point for its limit at all.
- **[UNKNOWN — CHECK LIVE]** Current review/approval turnaround time for a
  new addon submission on either site. Neither figure is known confidently
  enough to state here.
- **[GENERAL KNOWLEDGE — VERIFY]** WoWInterface has historically been
  somewhat stricter/more manually curated about first-time submissions
  than CurseForge's more automated pipeline — treat first-submission
  timelines as unpredictable and plan the release checklist (§5) so
  WoWInterface isn't a hard blocker on the CurseForge release.

---

## 4. The multi-addon case (base + N packs)

This is the part specific to this project's structure, and where getting it
wrong causes real user confusion, so it's covered in the most depth.

### 4.1 One CurseForge/WoWInterface project per pack

**[GENERAL KNOWLEDGE, but consistent with `docs/sound_packs.md` §7's own
conclusion, which is treated as [VERIFIED] repo guidance]**: neither site
has a native "one repo/project, many downloadable addons" concept. Each
independently-installable, independently-size-capped pack needs to be its
own project:

- `SpeakStone Narration` (base) — `QuestReaderAddon`
- `SpeakStone Narration — Cataclysm Voices` — `QuestReaderAddon_Pack_Cataclysm`
- one more per expansion as they ship (`_Pack_TheWarWithin`,
  `_Pack_Midnight`, `_Pack_Shared`, etc. — see the table in
  `docs/expansion_addons_plan.md` **[VERIFIED]** for current expansion
  status; `Unsorted` is explicitly excluded from publishing by
  `tools/publish_packs.py` **[VERIFIED]** and should not get a project at
  all while it remains a scrap-bin category)

This matches the naming already used in the pack's own `.toc` — e.g.
`## Title: SpeakStone Narration - Cataclysm Voices` **[VERIFIED —
packs/QuestReaderAddon_Pack_Cataclysm/*.toc]** — so the CurseForge/WoWInterface
project display name should follow that same "SpeakStone Narration - X"
convention for discoverability and consistent branding across all N+1
listings.

### 4.2 Dependency declaration: OptionalDeps vs RequiredDeps

**[VERIFIED difference, with a concrete recommendation]**

Currently, both directions use `OptionalDeps`:

- Base addon: `## OptionalDeps: QuestReaderAddon_TWW_EN, QuestReaderAddon_TWW_FR`
- Pack: `## OptionalDeps: QuestReaderAddon` (confirmed in
  `packs/QuestReaderAddon_Pack_Cataclysm/QuestReaderAddon_Pack_Cataclysm.toc`)

HandyNotes' real, published, multi-module addon family does it differently
for exactly this pack→base relationship. `HandyNotes_TheWarWithin.toc`
**[VERIFIED, read from the local install]** declares:

```
## OptionalDeps: Ace3
## RequiredDeps: HandyNotes
```

i.e. the **module** (equivalent to a pack here) marks the **base addon**
(`HandyNotes`) as a `RequiredDeps`, and reserves `OptionalDeps` for genuinely
optional libraries (`Ace3`).

**Recommendation: change each pack's `.toc` from `OptionalDeps:
QuestReaderAddon` to `RequiredDeps: QuestReaderAddon`.** Reasoning:

- A pack is audio data with a tiny `Register.lua` shim — it does nothing
  and provides no value at all if `QuestReaderAddon` isn't installed and
  loaded (confirmed by reading `Register.lua`: it calls
  `QuestReaderAddon_RegisterSoundPack`, a global defined only in
  `QuestReaderAddon.lua`). That is the textbook case `RequiredDeps` exists
  for — HandyNotes modules are the same shape (map-pin data with no
  standalone use) and use `RequiredDeps` for exactly this relationship.
  `OptionalDeps` is correct on the *base* addon's side for `_TWW_EN`/`_TWW_FR`
  (the base addon works fine without them) but is the wrong relationship
  for pack→base.
- **[GENERAL KNOWLEDGE — VERIFY]** `RequiredDeps` also affects installer
  behavior on both platforms: CurseForge's app and (per prior knowledge,
  less certainly) WoWInterface's downloader use `RequiredDeps` to prompt
  "this addon requires X — install it too?" at install time, which is
  exactly the discovery behavior wanted here (§4.3). `OptionalDeps` at most
  suggests, and by some client versions doesn't surface as a prompt at all
  — treat the exact current install-time behavior of both fields on both
  platforms as something to verify live before relying on it for user
  experience, but the direction of the recommendation (use `RequiredDeps`
  for pack→base) holds regardless.
- This is a genuinely easy edit later (a one-line `.toc` change per pack)
  but is flagged here rather than made, per this task's constraint against
  touching any `.toc` file.

This does **not** apply to the base addon's own `OptionalDeps` line for
`QuestReaderAddon_TWW_EN`/`_FR` — that direction is correctly optional and
should stay as-is.

### 4.3 How users discover packs from the main addon's page

**[GENERAL KNOWLEDGE — VERIFY]** Neither platform automatically cross-lists
"related addons" on a project page from `.toc` metadata alone, to prior
knowledge — this needs deliberate authoring:

- On the base addon's project page (both sites), add a "Related
  Addons"/description section that names and links every pack project by
  its slug/URL, updated as new packs ship. This is manual maintenance,
  not automatic.
- **[GENERAL KNOWLEDGE — VERIFY]** If `RequiredDeps` (per §4.2) does drive
  an install-time prompt on either platform, that's an additional, install-
  time discovery path *from pack to base*, but does not help the reverse
  direction (someone installing only the base addon still needs the
  description-section links to find packs). Confirm both directions live.
- CurseForge supports project "tags" separate from categories; tagging all
  base+pack projects with a shared, distinctive tag (e.g. matching the
  addon family name) may aid discovery via search — **[UNKNOWN — CHECK
  LIVE]** whether CurseForge's current UI surfaces tag-based "more like
  this" browsing prominently enough to be worth the effort.
- Since the base addon's `.toc` `## Title:` and pack `.toc` `## Title:`
  already share the "SpeakStone Narration" prefix (`SpeakStone Narration`
  vs `SpeakStone Narration - Cataclysm Voices` **[VERIFIED]**), search by
  name on either site should surface the family together reasonably well
  even before manual cross-linking is done — but don't rely on that alone.

### 4.4 The file-size cap and auto-splitting

**[VERIFIED figures from `docs/sound_packs.md`, treated as authoritative
repo data for this section]**

CurseForge's 500 MB per-file cap (§2.6) is why the whole per-expansion pack
split exists in the first place — `docs/expansion_addons_plan.md` states
the full-game audio projection at ~9 GB, which could never ship as one
CurseForge file.

Per `docs/sound_packs.md` §4's measured dry-run numbers, today's largest
built pack (`Unsorted`, the catch-all for quest audio with no known
expansion) is ~1.1 GB — already over the 500 MB cap on its own, while every
real per-expansion pack currently built is 3–34 MB, far under it. Two
things follow:

1. **`Unsorted` is explicitly excluded from publishing** by
   `tools/publish_packs.py` (`if pack_name == "Unsorted": continue`
   **[VERIFIED]**) — it is not shipped as a downloadable pack at all today,
   so it does not currently need to be split for the size cap; it needs
   expansion-mapping coverage to improve first (per `docs/sound_packs.md`
   §3, only ~10% of quest audio currently maps to a known expansion).
2. **As real per-expansion coverage grows**, `docs/sound_packs.md` §4
   flags that any single expansion's pack *could* eventually exceed 500 MB
   too, particularly large-content expansions like The War Within or
   Midnight once fully voiced — at which point that pack, specifically,
   would need further splitting (e.g. by zone or by content patch within
   the expansion), the same way `Unsorted` would if it were ever published.
   **No such further-split has been designed or built yet** — this is a
   forward-looking flag from the repo's own docs, not a mechanism that
   exists today. Treat "some packs will exceed [the cap] and are being
   auto-split" as describing a plan/risk noted in the docs, not a shipped
   feature — `build_sound_packs.py` splits by expansion only; it does not
   currently detect or auto-split an over-cap pack.

---

## 5. Release checklist

A concrete sequence for cutting a release across the base addon and N packs
without them drifting apart. This combines what's already true of the repo
(`.toc` version fields, the packager, `publish_packs.py`) with
recommendations for the parts not yet automated.

1. **Freeze what's being released.** Confirm `SoundLengths.lua` and
   `Sounds/` reflect the intended finished state (per `docs/sound_packs.md`
   §5, `tools/build_sound_packs.py` never writes into these — it only
   reads them — so this step is "stop touching them," not a build step).
2. **Rebuild packs from that frozen state**:
   `python tools/build_sound_packs.py --out packs_build` (matches the
   `--out` used by `publish_packs.py` **[VERIFIED]**). Check the
   `Unsorted`-share line the script prints to stderr (per
   `docs/sound_packs.md` §6) as a sanity check that nothing regressed.
3. **Version numbers, in lockstep**: bump `## Version:` in
   `QuestReaderAddon.toc` (currently `1.2` **[VERIFIED]**) and in every
   pack's `.toc` for the packs being released this round. Recommend a
   single shared release tag/version string across the whole family for
   any release where the base addon's Lua actually changed (e.g. tag
   everything `v1.3` together) so a support request naming "version 1.3"
   is unambiguous — packs whose audio didn't change but ship alongside a
   base-addon bugfix release should still bump their version number to
   keep the family's version numbers moving together, even if their
   content is identical to the prior release. **This
   lockstep-versioning convention is a recommendation, not something
   currently enforced by any script** — `publish_packs.py` does not touch
   `.toc` version fields at all today.
4. **`## Interface:` must match everywhere before publishing.** Per
   `docs/expansion_addons_plan.md`'s own "what still needs a decision"
   section **[VERIFIED]**, `build_sound_packs.py` currently hardcodes the
   Interface line rather than reading it from `QuestReaderAddon.toc` — so
   if the base addon's Interface line was bumped since packs were last
   built, **check this by hand** (diff the pack `.toc` files against the
   base) before publishing, don't assume the build script kept them in
   sync.
5. **Publish the base addon first.** Packs declare a dependency on it
   (currently `OptionalDeps`, recommended to become `RequiredDeps` — §4.2);
   publishing the base addon's new version first means CurseForge's/
   WoWInterface's dependency-resolution (whatever form it takes — see the
   verify-live note in §4.2) always has a matching or newer base version to
   point at, never a pack momentarily depending on a version that isn't
   live yet.
6. **Publish/refresh each pack.** Today this is
   `python tools/publish_packs.py --github-user dandwhelan` **[VERIFIED]**,
   which pushes each pack's rebuilt content to its own GitHub repo. Use
   `--only Midnight,Cataclysm` to scope a release to specific packs, and
   `--dry-run` first to confirm exactly which packs would be touched — the
   script already supports this **[VERIFIED]**. Pushing to GitHub is not
   the same as publishing on CurseForge/WoWInterface — see step 7.
7. **Trigger the actual CurseForge build.** If project-to-repo linking and
   tag-triggered packaging (§2.5) is set up, push a matching tag in each
   pack's repo (or create a GitHub Release) to trigger it. If it is not yet
   set up for a given project, build+upload the zip by hand for that
   project this time, and note it as a to-do. **[GENERAL KNOWLEDGE —
   VERIFY]** exact tag format CurseForge expects (this is often
   configurable per-project, e.g. `v*` or a specific pattern) — confirm on
   each project's settings page rather than assuming.
8. **Upload/refresh WoWInterface manually.** Per §3.4, no repo-linked
   automation is assumed there — re-zip and re-upload by hand for each
   project being released this round unless the live site is confirmed to
   now support something more automated.
9. **Update the base addon's project description** with any new packs
   published for the first time this round (§4.3) — this is the one step
   most likely to be forgotten because it lives outside any script.
10. **Smoke-test post-publish**: download the just-published base addon +
    at least one pack from the live site (not from the repo) into a test
    `Interface/AddOns/` folder, per the testing steps already laid out in
    `docs/sound_packs.md` §5 **[VERIFIED]** — confirms the zip's folder
    layout survived the platform's own packaging step, which is a
    CurseForge/WoWInterface-side risk this repo's own tools can't catch.

---

## 6. What to automate later

Sketches only — not built in this task, per the task's constraints.

- **GitHub Actions release workflow**: a workflow triggered on tag push
  (e.g. `v*`) in the base repo that runs the existing
  `curseforge-packager` tool referenced in `pkgmeta.yaml` **[VERIFIED
  the tool is already named/expected]**, likely via the
  community-maintained `BigWigsMods/packager` GitHub Action (**[GENERAL
  KNOWLEDGE — VERIFY]** this is the commonly-used action for this exact
  `pkgmeta.yaml` format; confirm it's still current) — passing a
  `CF_API_KEY` secret for CurseForge's upload API (§6 below) and, if kept,
  a WoWInterface credential for a scripted upload. For the pack repos, a
  near-identical workflow per pack repo, or a matrix job in one workflow
  keyed off the pack list, triggered by `tools/publish_packs.py` pushing a
  tag instead of (or in addition to) a plain commit.
- **CurseForge upload API**: CurseForge exposes an authenticated file-
  upload API (`https://minecraft.curseforge.com/api/...`-style endpoints
  historically, though the exact current host/path for WoW addons should
  be re-confirmed — **[UNKNOWN — CHECK LIVE]**, no confident recall of the
  present-day endpoint) keyed by a per-account API token, which the
  `BigWigsMods/packager` action already wraps. Longer-term automation here
  is "make sure the Actions workflow above has that token as a secret,"
  not a new client to build.
- **WoWInterface**: to prior knowledge, no comparably well-known public
  upload API exists — **[UNKNOWN — CHECK LIVE]** whether one has appeared
  since. If not, automating WoWInterface specifically may mean browser
  automation against their upload form rather than a clean API, which is a
  meaningfully bigger and more fragile lift than the CurseForge side; worth
  scoping separately rather than assuming parity with CurseForge's
  automation.
- **Version-bump automation**: a small script that bumps `## Version:` in
  the base `.toc` and every pack `.toc` together from one invocation, so
  step 3 of the release checklist stops being a manual multi-file edit —
  a natural companion to `tools/publish_packs.py`, not built yet.
- **Interface-version propagation**: fixing the exact gap
  `docs/expansion_addons_plan.md` already flags — have
  `build_sound_packs.py` parse `## Interface:`/`## X-Interface:` out of
  `QuestReaderAddon.toc` at build time instead of the current hardcoded
  string, so step 4 of the release checklist stops being a manual check.

---

## Verification checklist for the owner

Everything below was stated from general platform knowledge, not confirmed
against the live sites (no internet access during this task). Check all of
these before relying on this document operationally:

- [ ] Current CurseForge author-dashboard URL and project-creation flow
- [ ] Whether CurseForge requires any author verification beyond email
- [ ] WoWInterface's current account/submission flow and URL
- [ ] Whether WoWInterface has an approval delay or manual review step
- [ ] Current CurseForge WoW addon category list and which best fit
      "Audio & Video" / "Quests & Leveling" / "Immersion & RPG"
- [ ] Current WoWInterface category list (unknown whether a Sound/Audio
      category even exists there)
- [ ] Whether CurseForge's uploader auto-detects game versions from
      `## Interface:` or still requires manual game-version tagging
- [ ] Current mechanics of linking a CurseForge project to a GitHub repo
      (webhook/App install steps, tag- vs release-triggered builds, exact
      tag pattern expected)
- [ ] Whether CurseForge's or WoWInterface's client/installer actually
      surfaces `RequiredDeps` as an install-time "also install X" prompt,
      and how (this motivates the OptionalDeps→RequiredDeps recommendation
      but the UX consequence itself is unverified)
- [ ] WoWInterface's current per-file size limit (no figure is known for
      this at all — do not assume it matches CurseForge's 500 MB)
- [ ] Current approval/review turnaround time on both sites for new
      submissions
- [ ] Whether WoWInterface has any upload API, or whether automation there
      requires browser-level scripting
- [ ] Current CurseForge upload API host/paths and auth token format
- [ ] Whether `BigWigsMods/packager` (or an equivalent) is still the
      standard GitHub Action for this `pkgmeta.yaml` format
- [ ] Whether CurseForge tag-based project discovery ("more like this") is
      prominent enough in the current UI to be worth using for cross-pack
      discovery

## Summary of the one concrete code recommendation in this document

**Change `OptionalDeps: QuestReaderAddon` to `RequiredDeps: QuestReaderAddon`
in every pack's `.toc`** (not done here — `.toc` edits were out of scope for
this task). Reasoning: a pack has zero standalone value without the base
addon (verified by reading `Register.lua`, which only calls a global the
base addon defines), and HandyNotes' real, shipped multi-module family uses
exactly this base→required, library→optional split
(`HandyNotes_TheWarWithin.toc`: `RequiredDeps: HandyNotes`,
`OptionalDeps: Ace3`) for the identical structural relationship. Leave the
base addon's own `OptionalDeps: QuestReaderAddon_TWW_EN,
QuestReaderAddon_TWW_FR` as-is — that direction is genuinely optional.
