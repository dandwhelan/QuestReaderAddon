#!/usr/bin/env python3
"""Generate quest voiceovers from harvested text and extracted reference audio.

Takes the passages produced by wowhead_quests.py or harvest_export.py, matches
each speaker to the reference clips extracted for that NPC, and synthesizes one
audio file per passage named as the addon expects: <questID>_<passage>.ogg.

The default engine is Coqui XTTS-v2, which clones zero-shot from a few seconds
of reference audio and outputs at 24 kHz — the same rate as the audio the addon
already ships. It needs a GPU to be practical and is imported only when
generation actually runs, so planning works on any machine.

Work is resumable. Existing output files are skipped, so an interrupted run
continues where it stopped rather than regenerating everything.

Usage:
    synthesize.py passages.json --reference ./reference-audio --dry-run
    synthesize.py passages.json --reference ./reference-audio -o ./generated
    synthesize.py passages.json --reference ./ref -o ./gen --only 95300,95301
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_sources import normalize as fold_name
from build_soundlengths import duration as audio_duration

# Clips used to condition the clone. More does not reliably improve the voice,
# and every extra second is loaded per NPC.
REFERENCE_CLIPS = 6
XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"

# F5-TTS resembles the source voice more closely than XTTS on identical
# references — measured against Riftblade Maella's own clips, its error in the
# 800 Hz-2.5 kHz presence band, where articulation and emphasis sit, was
# 0.4 dB against XTTS's 2.0 dB, and it was closer in every other band too.
F5_MODEL = "F5TTS_v1_Base"

# F5 clips its reference internally, so the long budget that suits XTTS only
# wastes Whisper time here.
F5_REFERENCE_SECONDS = 15.0

# Zero-shot cloning wants ordinary connected speech, budgeted by duration
# rather than counted in clips.
#
# This was 12 seconds, on the documented advice that a large reference set
# makes the model average across it and read flat. Compared by ear against 30
# and 60 on an NPC with 103 clips, 60 was the clear winner: the resemblance to
# the actual voice matters more here than the flattening costs, because these
# are quest givers a player already knows the sound of. The advice is not
# wrong, it is just aimed at a different goal. Override per run with
# --reference-seconds; NPCs with only a handful of clips will fall short of
# the budget anyway and are unaffected.
MIN_REFERENCE_SECONDS = 2.5
MAX_REFERENCE_SECONDS = 15.0
REFERENCE_BUDGET_SECONDS = 60.0

# Combat and reaction lines are shouted, clipped and often processed. Cloning
# from them produces a voice that sounds permanently angry, so they are used
# only when nothing else is available.
#
# This only bites where filenames describe their contents. Older sets do
# ("..._attack_01.ogg"), but everything recorded for Midnight is named
# vo_<patch>_<npc>_<fileID>.ogg — across 446 clips extracted for 12.1 NPCs,
# not one matched any marker below. Where that is so, selection falls through
# to the duration rules, which is a weaker filter than it looks: nothing here
# can tell a shout from a greeting. Judging that needs the audio itself.
COMBAT_MARKERS = ("attack", "aggro", "death", "pain", "wound", "kill",
                  "flee", "taunt", "battle", "spell", "cast", "grunt",
                  "laugh", "cheer", "yell", "scream")

# Matching the game's own dialogue, measured at about -16 LUFS. The shipped
# library ranges from -13 to -20, so normalising also makes it more consistent
# than it is today.
TARGET_LUFS = -16.0

# Longest silence left inside a clip. Measured on 93024_completion, whose worst
# gap ran to 1.81 s; capping at this leaves the phrasing intact and reclaimed
# 3.7 s of dead air from an 18.8 s clip. Set to 0 to leave pauses alone.
MAX_PAUSE_SECONDS = 0.45

# Pitch matching was tried here and removed. A clone can sit an interval off
# the voice it came from — measured at +6.5% on Tak'lejo against -1.7% on
# Riftblade Maella — and correcting it is a plain frequency ratio, so it
# looked worth doing. Measured over five clips it improved four and sent the
# fifth further out, because the median F0 of a passage is not stable enough
# to steer by: on a clip whose pitch is bimodal the estimator's median jumps
# between modes, and the correction then points the wrong way. Matching a
# whole voice needs a target measured across a speaker, not one utterance.


# The harvester reverts the player's own name to "$n" before anything is
# written, so harvested text carries the token rather than whoever happened to
# capture it (see QuestReaderHarvester.lua). Something still has to be said
# aloud in its place: "champion" is what the game itself falls back to when it
# addresses you without a name, and it survives being heard by any player.
#
# $g/$G carry a "male:female;" branch. Nothing in a harvested line records which
# branch the listener should hear -- that is the player's own gender, not the
# speaker's -- so take the first and move on. Left unhandled the model reads the
# punctuation aloud, which is worse than picking.
#
# The two sources spell the same idea differently and both reach synthesis.
# The in-game harvester writes the server's own "$n"; wowhead_quests.py scrapes
# pages that render it as "<name>", with "<class>" and "<race>" alongside. Only
# the dollar form was handled at first, so "<name>" travelled all the way into
# the audio and was read aloud as a word -- audible in quest 93024.
#
# $m is not a token the client documents, but it appears once ("Dey must be
# dealt with, $m.") in a position that plainly addresses the player, so it is
# read the same way rather than left to be spoken.
PLAYER_NAME_TOKEN = re.compile(r"\$[nNmM]|<\s*name\s*>", re.IGNORECASE)
PLAYER_CLASS_TOKEN = re.compile(r"\$[cC]|<\s*class\s*>", re.IGNORECASE)
PLAYER_RACE_TOKEN = re.compile(r"\$[rR]|<\s*race\s*>", re.IGNORECASE)
GENDER_TOKEN = re.compile(r"\$[gG]\s*([^:;]*):([^;]*);?")

# Angle brackets also carry stage directions -- "<Brek laughs.>", "<Soridormi
# winks.>" -- which the game shows the player but nobody speaks. Left in, the
# NPC narrates their own body language in their own voice. Dropped after the
# placeholders above are resolved, so only genuine directions are left to match.
STAGE_DIRECTION = re.compile(r"<[^<>]{2,120}?>")

# Anything token-shaped still standing after rendering is a class of artifact
# nobody has taught this function about yet. That has happened twice -- "<name>"
# and "$m" both reached generated audio and were read aloud -- and both times
# the only symptom was someone hearing it months later. Cheap to notice here.
RESIDUAL_TOKEN = re.compile(r"\$[a-zA-Z]|<[^<>]{1,120}>|&[a-zA-Z]{2,8};|&#\d+;")


def render_tokens(text, player_term="champion", class_term="hero",
                  race_term="traveller", drop_stage_directions=True):
    """Turn substitution tokens and stage directions into speakable text.

    Both harvest sources are handled together because a passage does not
    record which one it came from, and a token left unresolved is not silently
    wrong -- it is read out loud.
    """
    text = PLAYER_NAME_TOKEN.sub(player_term, text)
    text = PLAYER_CLASS_TOKEN.sub(class_term, text)
    text = PLAYER_RACE_TOKEN.sub(race_term, text)
    text = GENDER_TOKEN.sub(lambda m: m.group(1).strip(), text)
    if drop_stage_directions:
        text = STAGE_DIRECTION.sub(" ", text)
    # Removing a direction can leave a doubled space or a space before a stop.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    return text.strip()


def load_lexicon(path):
    """Compile the pronunciation lexicon into one substitution pass.

    Warcraft proper nouns are not English, and the model reads them as if
    they were — Zul'Aman came out wrong the first time anyone listened. The
    lexicon respells known offenders phonetically before the text reaches the
    engine. One compiled alternation keeps a long lexicon from meaning many
    passes over every passage.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            entries = {k: v for k, v in json.load(handle).items()
                       if not k.startswith("_")}
    except FileNotFoundError:
        return None
    if not entries:
        return None
    folded = {k.lower(): v for k, v in entries.items()}
    # The trailing guard blocks only word characters, not apostrophes, so a
    # possessive still matches: "Zul'Aman's" respells as "Zool-Ah-Mahn's".
    #
    # sorted(..., key=len, reverse=True) is load-bearing, not tidiness. Some
    # keys are prefixes of others -- "Amani" of "Amani'Zar", "K'aresh" of
    # "K'areshi" -- and because the trailing guard deliberately allows an
    # apostrophe, the short key *would* match inside the long one if the
    # alternation reached it first. Longest-first is what stops "Amani'Zar"
    # becoming "Ah-mah-nee'Zar". Replacing this with insertion or alphabetical
    # order breaks those names silently.
    pattern = re.compile(
        r"(?<![\w'])(" + "|".join(re.escape(k) for k in
                                  sorted(entries, key=len, reverse=True))
        + r")(?!\w)", re.IGNORECASE)
    return lambda text: pattern.sub(
        lambda m: folded[m.group(1).lower()], text)


def index_reference(directory):
    """Map folded NPC name -> list of reference clips.

    wow.export preserves the sound/creature/<npc>/ layout, so each leaf
    directory is one NPC's voice.
    """
    voices = {}
    for root, _, files in os.walk(directory):
        clips = sorted(os.path.join(root, f) for f in files
                       if f.lower().endswith((".ogg", ".wav", ".mp3")))
        if clips:
            voices.setdefault(fold_name(os.path.basename(root)), []).extend(clips)
    return voices


def load_voice_bank(path, voices):
    """Map each NPC to its group's donor clips.

    Produces {folded npc name: clips} covering every NPC in the bank, not only
    those the bank flagged. The bank records which NPCs have no audio *in the
    game*; synthesis needs a stand-in whenever audio was not *extracted here*,
    which is a larger set. The donor is only consulted after an NPC's own clips
    fail to resolve, so offering one for everybody costs nothing.
    """
    with open(path, encoding="utf-8") as handle:
        bank = json.load(handle)

    fallback, unusable = {}, set()
    for group_name, group in bank.items():
        donor = group.get("donor")
        clips = voices.get(donor["folder"]) if donor else None
        if not clips:
            # The bank names a donor whose audio was never extracted; recording
            # it as unusable is more honest than silently leaving NPCs unvoiced.
            if donor:
                unusable.add(f"{group_name} (donor {donor['folder']})")
            continue
        for member in group.get("members", []):
            if member.get("npcName"):
                fallback[fold_name(member["npcName"])] = clips
    return fallback, unusable


def reference_seconds(path):
    try:
        return audio_duration(path)
    except Exception:
        return None


def pick_references(clips, budget=REFERENCE_BUDGET_SECONDS, limit=None):
    """Choose the clips most likely to yield a clean, characterful clone.

    Two things this gets right that the obvious approach does not. Selection is
    not alphabetical — that quietly cloned NPCs from their combat barks, since
    "attack" sorts before "quest". And it stops at a duration budget rather than
    taking a fixed number of clips: handing the model everything available makes
    it average across the lot and read flatter, so more reference is worse.
    """
    # The clip count is a guard against pathologically short clips, not a
    # control in its own right — the budget is what governs. Scaling it with
    # the budget keeps a raised budget from being silently capped here.
    if limit is None:
        limit = max(REFERENCE_CLIPS,
                    int(budget / MIN_REFERENCE_SECONDS) + 1)

    scored = []
    for path in clips:
        name = os.path.basename(path).lower()
        combat = any(marker in name for marker in COMBAT_MARKERS)
        seconds = reference_seconds(path)
        usable = (seconds is not None
                  and MIN_REFERENCE_SECONDS <= seconds <= MAX_REFERENCE_SECONDS)
        scored.append((not combat, usable, seconds or 0.0, path))

    # Ordinary dialogue first, then clips of a usable length, then longer ones.
    scored.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)

    chosen, total = [], 0.0
    for _, _, seconds, path in scored:
        if len(chosen) >= limit or total >= budget:
            break
        chosen.append(path)
        total += seconds
    return chosen or clips[:1]


def match_voice(voices, npc_name, fallback=None):
    """Find an NPC's reference clips, tolerating title differences."""
    if not npc_name:
        return None
    key = fold_name(npc_name)
    if key in voices:
        return voices[key]
    # Sound folders often carry an epithet the quest data omits, and the
    # reverse. Accept a unique containment either way.
    candidates = [k for k in voices if key in k.split("_") or key in k]
    if len(candidates) == 1:
        return voices[candidates[0]]
    # No audio of its own: fall back to the donor for this NPC's race and sex.
    if fallback:
        return fallback.get(key)
    return None


def encode_ogg(wav_path, ogg_path, quality=4, normalize=True, speed=1.0,
               max_pause=MAX_PAUSE_SECONDS):
    """Trim, level and encode to Ogg Vorbis, the format game and addon share.

    Synthesis output varies in level between clips and often carries a beat of
    silence at each end. Left alone that is audible as lines jumping in volume
    against the game's own dialogue, so both are corrected in the same pass.
    """
    filters = []
    if normalize:
        filters.append("silenceremove=start_periods=1:start_threshold=-50dB"
                       ":start_silence=0.05:detection=peak")
        filters.append(f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11")
        # loudnorm resamples to 192 kHz internally; bring it back to the rate
        # the rest of the library uses.
        filters.append("aresample=24000")
    if max_pause:
        # F5 decides sentence-boundary pauses stochastically, and on an unlucky
        # sample they run to 1.8 s -- measured on 93024_completion, which holds
        # two of them 0.32 s apart, stranding one word between two silences and
        # spending 37% of the clip on dead air. Length of text does not predict
        # it and regenerating is a reroll, so the pause is capped here instead.
        #
        # Capped, not removed: the short pauses are the phrasing. F5's own
        # remove_silence uses split_on_silence and takes all of them, which is
        # why it stays off.
        #
        # After loudnorm so the threshold reads against a known level, and
        # before atempo so the cap is stated in the text's own timebase.
        filters.append(f"silenceremove=stop_periods=-1:stop_duration={max_pause}"
                       f":stop_threshold=-40dB:detection=peak")
    if abs(speed - 1.0) > 0.001:
        # atempo changes tempo only, unlike asetrate, which moves pitch along
        # with it — a faster read should not also sound higher-pitched.
        filters.append(f"atempo={speed:.3f}")

    command = ["ffmpeg", "-loglevel", "error", "-y", "-i", wav_path]
    if filters:
        command += ["-af", ",".join(filters)]
    command += ["-ac", "1", "-c:a", "libvorbis", "-q:a", str(quality), ogg_path]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[:200]}")
    os.remove(wav_path)


class XttsEngine:
    """Coqui XTTS-v2. Conditions on a list of clips, needs no transcript."""

    def __init__(self, device, language):
        # XTTS ships under a licence the model asks the user to accept at an
        # interactive prompt. An unattended run would block on it
        # indefinitely, so the acceptance is recorded up front.
        os.environ.setdefault("COQUI_TOS_AGREED", "1")
        try:
            from TTS.api import TTS
        except ImportError as exc:
            # Report what actually failed. The import pulls in torch,
            # transformers and torchcodec, and a version clash in any of them
            # surfaces here as an ImportError that has nothing to do with the
            # package being absent.
            sys.exit(
                f"Could not import Coqui TTS:\n    {exc}\n\n"
                f"The original 'TTS' package is archived and will not install\n"
                f"on Python 3.12 or newer. Use the maintained fork, which\n"
                f"provides the same TTS module:\n"
                f"    pip install coqui-tts \"transformers<5\"\n\n"
                f"Use --dry-run to plan the work without generating.")
        self.language = language
        print(f"Loading {XTTS_MODEL} on {device} ...", file=sys.stderr)
        self.api = TTS(XTTS_MODEL).to(device)

    def synthesize(self, text, clips, npc, wav_path):
        self.api.tts_to_file(text=text, speaker_wav=clips,
                             language=self.language, file_path=wav_path)


class F5Engine:
    """F5-TTS. Closer to the source voice than XTTS on the same references.

    It wants one reference clip and its transcript, where XTTS takes a list
    and needs no text at all. Both gaps are closed here: the chosen clips are
    concatenated into one file, and the transcript is recovered by the
    Whisper pass F5 already bundles. Both are cached per NPC, so a speaker is
    prepared once no matter how many passages they have.
    """

    def __init__(self, device, language):
        try:
            from f5_tts.api import F5TTS
            from f5_tts.infer.utils_infer import preprocess_ref_audio_text
        except ImportError as exc:
            sys.exit(
                f"Could not import F5-TTS:\n    {exc}\n\n"
                f"    pip install f5-tts\n"
                f"    pip uninstall torchcodec\n\n"
                f"Removing torchcodec is not optional on Windows. F5 declares\n"
                f"it, but it only loads against FFmpeg's shared libraries and\n"
                f"the usual Windows ffmpeg builds are static, so importing it\n"
                f"fails at generation time. Without it F5 falls back to\n"
                f"torchaudio and works.\n\n"
                f"It does not share an environment with coqui-tts easily;\n"
                f"a separate venv per engine is the path of least resistance.\n\n"
                f"Use --dry-run to plan the work without generating.")
        self._preprocess = preprocess_ref_audio_text
        print(f"Loading {F5_MODEL} on {device or 'default device'} ...",
              file=sys.stderr)
        self.model = F5TTS(model=F5_MODEL, device=device)
        self.prepared = {}

    def _prepare(self, clips, npc):
        """One reference file plus its transcript, built once per NPC."""
        if npc in self.prepared:
            return self.prepared[npc]

        handle, listing = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            for clip in clips:
                # The concat demuxer treats a quote as syntax, so a path
                # containing one would silently truncate the reference set.
                out.write("file '%s'\n"
                          % os.path.abspath(clip).replace("'", r"'\''"))
        merged = tempfile.mktemp(suffix=".wav")
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat",
             "-safe", "0", "-i", listing, "-t", str(F5_REFERENCE_SECONDS),
             "-ac", "1", "-ar", "24000", merged], check=True)
        os.remove(listing)

        audio, transcript = self._preprocess(merged, "", show_info=lambda *a: None)
        self.prepared[npc] = (audio, transcript)
        print(f"  {npc}: reference transcribed as "
              f"\"{transcript.strip()[:60]}...\"", file=sys.stderr)
        return self.prepared[npc]

    def synthesize(self, text, clips, npc, wav_path):
        audio, transcript = self._prepare(clips, npc)
        self.model.infer(ref_file=audio, ref_text=transcript, gen_text=text,
                         file_wave=wav_path, remove_silence=False,
                         show_info=lambda *a: None)


def load_engine(name, device, language):
    return (F5Engine if name == "f5" else XttsEngine)(device, language)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("passages", help="passages.json")
    parser.add_argument("--reference", required=True,
                        help="directory of extracted reference audio")
    parser.add_argument("-o", "--output", default="./generated")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the plan without generating")
    parser.add_argument("--only", help="comma-separated quest IDs")
    parser.add_argument("--engine", choices=("f5", "xtts"), default="f5",
                        help="f5 (default) resembles the source voice more "
                             "closely; xtts is the older path. They do not "
                             "share an environment well — expect one venv each")
    parser.add_argument("--device", default="cuda",
                        help="cuda or cpu (default cuda)")
    parser.add_argument("--language", default="en")
    parser.add_argument("--reference-seconds", type=float,
                        default=REFERENCE_BUDGET_SECONDS,
                        help=f"seconds of reference audio to condition on "
                             f"(default {REFERENCE_BUDGET_SECONDS:.0f}). More "
                             f"can improve how much the clone resembles the "
                             f"NPC; too much averages its delivery flat. "
                             f"XTTS-v2 accepts up to about 30")
    parser.add_argument("--keep-wav", action="store_true",
                        help="skip Ogg encoding and leave WAV output")
    parser.add_argument("--no-normalize", action="store_true",
                        help="skip loudness matching and silence trimming")
    parser.add_argument("--speed", type=float, default=1.2,
                        help="playback speed applied at encode time, e.g. "
                             "1.2 for 20%% faster (default 1.2). Pitch is "
                             "held constant; only pace changes")
    parser.add_argument("--narrator-speed", type=float, default=1.3,
                        help="speed for narrated passages -- those with no "
                             "NPC, read by --narrator. They run slower than "
                             "dialogue does, so they carry more (default 1.3)")
    parser.add_argument("--narrator", default="chen_stormstout",
                        help="reference folder to narrate passages that have "
                             "no NPC at all: books, objects, item text. Unlike "
                             "--default-voice this does not stand in for named "
                             "NPCs, who should sound like themselves")
    parser.add_argument("--player-term", default="champion",
                        help="what to say where the text carries the player's "
                             "own name, as $n or <name> (default champion)")
    parser.add_argument("--class-term", default="hero",
                        help="what to say for $c / <class> (default hero)")
    parser.add_argument("--race-term", default="traveller",
                        help="what to say for $r / <race> (default traveller)")
    parser.add_argument("--keep-stage-directions", action="store_true",
                        help="speak '<Brek laughs.>' aloud instead of dropping "
                             "it. Off by default: nobody says these lines")
    parser.add_argument("--lexicon",
                        default=os.path.join(
                            os.path.dirname(os.path.abspath(__file__)),
                            "pronunciations.json"),
                        help="phonetic respellings for proper nouns the "
                             "model mispronounces (default "
                             "pronunciations.json beside this script)")
    parser.add_argument("--voice-bank",
                        help="voicebank.json, giving NPCs without reference "
                             "audio a stand-in voice for their race and sex")
    parser.add_argument("--default-voice",
                        help="reference folder name to speak for anyone "
                             "nothing else covers: NPCs absent from the "
                             "client tables, and passages with no NPC at all "
                             "(objects, auto-accepted quests). A last resort "
                             "by construction — own clips, then the voice "
                             "bank, are always preferred")
    args = parser.parse_args()

    try:
        with open(args.passages, encoding="utf-8") as handle:
            passages = json.load(handle)["passages"]
    except FileNotFoundError:
        sys.exit(f"No such file: {args.passages}\n"
                 f"Produce it with wowhead_quests.py or harvest_export.py.")
    except (KeyError, json.JSONDecodeError) as exc:
        sys.exit(f"Could not read {args.passages}: {exc}")

    if not os.path.isdir(args.reference):
        sys.exit(f"No such directory: {args.reference}\n"
                 f"Extract reference audio first; see the README.")

    voices = index_reference(args.reference)
    if not voices:
        sys.exit(f"No audio found under {args.reference}.")
    print(f"{len(voices):,} voice(s) available in {args.reference}.",
          file=sys.stderr)

    fallback, unusable = {}, set()
    if args.voice_bank:
        try:
            fallback, unusable = load_voice_bank(args.voice_bank, voices)
        except FileNotFoundError:
            sys.exit(f"No such file: {args.voice_bank}\n"
                     f"Build one with voice_bank.py.")
        print(f"Voice bank covers {len(fallback):,} NPC(s) with a stand-in.",
              file=sys.stderr)
        for entry in sorted(unusable)[:5]:
            print(f"  donor audio not extracted for {entry}", file=sys.stderr)

    wanted = None
    if args.only:
        wanted = {q.strip() for q in args.only.split(",") if q.strip()}

    os.makedirs(args.output, exist_ok=True)

    default_clips = None
    if args.default_voice:
        default_clips = voices.get(fold_name(args.default_voice))
        if not default_clips:
            sys.exit(f"--default-voice {args.default_voice!r} matches no "
                     f"folder under {args.reference}.")

    # Passages with no NPC are books, plaques and item text -- nobody is
    # speaking them, so they get a narrator rather than a stand-in for an NPC
    # who does not exist.
    narrator_clips = None
    if args.narrator:
        narrator_clips = voices.get(fold_name(args.narrator))
        if not narrator_clips:
            sys.exit(f"--narrator {args.narrator!r} matches no folder under "
                     f"{args.reference}.")

    respell = load_lexicon(args.lexicon)
    planned, done, novoice, residual = [], 0, {}, []
    for passage in passages:
        quest_id = str(passage["questID"])
        if wanted and quest_id not in wanted:
            continue
        if not passage.get("text"):
            continue

        name = f"{quest_id}_{passage['passage']}"
        target = os.path.join(args.output,
                              name + (".wav" if args.keep_wav else ".ogg"))
        if os.path.exists(target):
            done += 1
            continue

        narrated = not passage.get("npcName")
        clips = match_voice(voices, passage.get("npcName"), fallback)
        if not clips and narrated and narrator_clips:
            clips = narrator_clips
        if not clips and default_clips:
            clips = default_clips
        if not clips:
            # Without reference audio there is nothing to clone from. These
            # need a fallback voice decided per NPC, not a silent skip.
            novoice.setdefault(passage.get("npcName") or "(no NPC)", 0)
            novoice[passage.get("npcName") or "(no NPC)"] += 1
            continue

        text = render_tokens(passage["text"], args.player_term,
                             args.class_term, args.race_term,
                             drop_stage_directions=not args.keep_stage_directions)
        leftover = RESIDUAL_TOKEN.findall(text)
        if leftover:
            residual.append((name, sorted(set(leftover))[:4]))
        text = respell(text) if respell else text
        planned.append((target, text,
                        pick_references(clips, budget=args.reference_seconds),
                        passage.get("npcName"),
                        args.narrator_speed if narrated else args.speed))

    print(f"\n  to generate : {len(planned):>5}", file=sys.stderr)
    print(f"  already done: {done:>5}", file=sys.stderr)
    print(f"  no voice    : {sum(novoice.values()):>5}"
          f" across {len(novoice)} NPC(s)", file=sys.stderr)

    if residual:
        print(f"\n{len(residual)} passage(s) still carry something token-shaped "
              f"after rendering. Anything left here gets SPOKEN ALOUD -- check "
              f"before generating:", file=sys.stderr)
        for name, found in residual[:10]:
            print(f"  {name}: {', '.join(found)}", file=sys.stderr)
        if len(residual) > 10:
            print(f"  ... and {len(residual) - 10} more", file=sys.stderr)

    if novoice:
        print("\nNPCs with no reference audio, needing a fallback voice:",
              file=sys.stderr)
        for npc, count in sorted(novoice.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {count:>4}  {npc}", file=sys.stderr)

    if args.dry_run:
        print("\nDry run; nothing written.", file=sys.stderr)
        return 0
    if not planned:
        print("\nNothing to do.", file=sys.stderr)
        return 0

    engine = load_engine(args.engine, args.device, args.language)
    failures = 0
    for position, (target, text, clips, npc, speed) in enumerate(planned, 1):
        wav_target = target[:-4] + ".wav" if target.endswith(".ogg") else target
        try:
            engine.synthesize(text, clips, npc or "(no NPC)", wav_target)
            if not args.keep_wav:
                encode_ogg(wav_target, target,
                           normalize=not args.no_normalize, speed=speed)
        except Exception as exc:  # engine and codec failures are both fatal
            failures += 1                                # to this clip only
            print(f"  failed {os.path.basename(target)} ({npc}): {exc}",
                  file=sys.stderr)
            continue
        if position % 25 == 0 or position == len(planned):
            print(f"  {position}/{len(planned)}", file=sys.stderr)

    generated = len(planned) - failures
    print(f"\nGenerated {generated:,} clip(s) into {args.output}.",
          file=sys.stderr)
    if failures:
        print(f"{failures} failed; rerun to retry only those.", file=sys.stderr)
    print(f"Rebuild the addon index next:\n"
          f"    python3 build_soundlengths.py {args.output} "
          f"-o ../SoundLengths.lua", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
