"""
Jev (TypeSafe System One) variant of check_align3.py: checks word-level
Italian-English correspondence in align.py's already-confirmed align3 output,
asking typed Choice questions in a forward and a reverse pass per group instead
of having a generative LLM fill in a markdown table.

See ALIGN3.md for why the check exists, what the two passes are for, what `-`
means, and how the two scripts compare on Inferno 1.

This is a checking tool, not part of the alignment pipeline: it only reads
align.py's output (`<NN>-ranges.tsv`, `<NN>-3.txt`) and never writes to them.
Results go to `<NN>-3-jev.tsv` (`Group<TAB>Italian<TAB>English`, same columns as
check_align3.py's `<NN>-3.tsv` so the two can be diffed directly), with a full
trace - including every probability - in `<NN>-3-jev.log`. A group already
present in an existing `<NN>-3-jev.tsv` (Italian words unchanged) is kept as-is
and skipped, so a rerun after an interrupted or partial run only fills in what's
missing; delete the file (or edit out a group's rows) to force a recheck.

Every request's usage is collected in USAGES (TypeSafeClient keeps no
`usages` list of its own, unlike llm7shi's Client). Their sum is appended to the
shared account-level usage.jsonl (see llm7shi.usage.find_usage_file) once per
run, in a `finally` so an interrupted run still records what it consumed; the
run's total and today's totals are printed only on normal completion. The log
additionally carries each request's own input tokens, a breakdown usage.jsonl's
per-run record cannot reconstruct.

Requires a TypeSafe API key in `TYPESAFE_API_KEY`.

    uv run python alignment/check_align3_jev.py inferno -c 1

`--scores` scores an already-written `<NN>-3-jev.tsv` to stdout, making no
request, writing no file and needing no API key:

    uv run python alignment/check_align3_jev.py inferno -c 1 --scores

It is check_align3.py's `--scores` applied to this script's table, and it calls
that code rather than repeating it (`check_align3.run_scores`). Nothing here has
to change for it to work: the deficit rate reads the `-` rows, and the surplus
rate subtracts what the table claimed from the group's English fragment in
`<NN>-3.txt`, so it needs nothing of the table but the three columns above -
which is also why a reverse answer skipped as a duplicate still counts as
surplus, being claimed by no row. See ALIGN3.md for both rates.
"""

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, NamedTuple, Tuple

ALIGNMENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ALIGNMENT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(ALIGNMENT_DIR))

import align
import check_align3
import dante_corpus
from dante_corpus import tokenize, has_alpha
from llm7shi.statusline import StatusLine
from llm7shi.usage import Usage, append_usage, find_usage_file, format_usage_line, print_today_totals
from typesafe_sdk import Choice, TypeSafeClient

# Set to a path to record usage; None means "don't record"
USAGE_PATH = None

# Every successful System One request's usage in this run, standing in for
# llm7shi Client.usages; summed and recorded once by main()
USAGES: List[Usage] = []

CANTICLES = ["inferno", "purgatorio", "paradiso"]

# Retries per group's System One call before giving up
MAX_ATTEMPTS = 3

# Probability of the NONE label at or above which the Italian word is recorded
# as having no English counterpart, and the top real word's probability below
# which an accepted alignment is flagged as low-confidence in the log.
NONE_THRESHOLD = 0.5
LOW_CONFIDENCE = 0.5

# How much of each forward Choice's distribution the log keeps. The response
# object is discarded once the row is written, so anything left out of the log
# costs a rerun to get back - and reruns have already been needed once to answer
# a question the top probability alone could not.
DIST_FLOOR = 0.01
DIST_MAX = 8


# ============================================================================
# Logging (mirrors align.py)
# ============================================================================

_log_file = None


def log_print(*args, **kwargs):
    """Print to log file only"""
    if _log_file:
        print(*args, **kwargs, file=_log_file)
        _log_file.flush()


def notify(ui: StatusLine, text: str, error: bool = False) -> None:
    """Print to both the console (via the status bar's shared Rich console)
    and this canto's log file."""
    (ui.stream.error if error else ui.log)(text)
    log_print(text)


# ============================================================================
# Word correspondence: build the System One request, read back the answers
# ============================================================================

def italian_words(text: str) -> List[str]:
    """Italian word tokens via dante_corpus.tokenize, in original order, with
    punctuation/symbol tokens (has_alpha False) excluded."""
    return [t for t in tokenize(text) if has_alpha(t)]


# The English side cannot reuse dante_corpus.tokenize: that splits at an
# apostrophe because Italian elides before one ("l'ora" is two words), where
# English keeps it inside the word ("God's", "glow'd"). Hyphenated compounds
# stay whole too - Norton renders "lonza" as "she-leopard", and splitting it
# would leave no option matching the Italian word. A leading or trailing
# apostrophe is a quote mark and stays out; em dashes never join words.
ENGLISH_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")


def english_words(text: str) -> List[str]:
    """English word tokens, in original order, with punctuation and digits
    excluded and intra-word apostrophes and hyphens kept."""
    return ENGLISH_WORD_RE.findall(text)


def word_id(prefix: str, i: int) -> str:
    return f"{prefix}{i:02d}"


class WordRow(NamedTuple):
    italian: str
    english: str


NONE_LABEL = "NONE"

_ROLE = "Judge by the role a word plays in its sentence, not by surface similarity."

# A pass only defines the numbering its own instructions cite; the numbering its
# answer labels use is already spelled out by the criteria. The API bills the
# state once per question (see ALIGN3.md), so a line the other direction would
# never read is still paid for by every question.
FORWARD_GUIDANCE = [
    "`S00`, `S01`, ... name the words of `source_text` in reading order, punctuation skipped.",
    _ROLE,
    f"Pick {NONE_LABEL} when no English word renders the Italian word: it was dropped, "
    f"left untranslated, or absorbed into a looser rendering.",
]
REVERSE_GUIDANCE = [
    "`W00`, `W01`, ... name the words of `target_text` in reading order, punctuation skipped.",
    _ROLE,
    f"Pick {NONE_LABEL} when the English word renders no Italian word of its own: it was "
    f"supplied by English grammar, or spread across a looser rendering.",
]


def build_state(italian_text: str, english_text: str, guidance: List[str]) -> dict:
    """Named JSON state: both texts verbatim plus this pass's guidance.

    The words are deliberately not listed here - each question's criteria
    already map every label to its word, and a state-side copy would be the
    same text billed again by every question.
    """
    return {
        "task": "Word-level alignment of an Italian source text with its English translation",
        "source_text": italian_text,
        "target_text": english_text,
        "guidance": guidance,
    }


def build_questions(it_words: List[str], en_words: List[str]) -> dict:
    """One Choice per Italian word, all sharing the same criteria object.

    `NONE` leads the criteria rather than trailing twenty-odd words, so it does
    not read as an afterthought.
    """
    criteria = {NONE_LABEL: "No word of `target_text` renders this Italian word."}
    criteria.update({word_id("W", j): w for j, w in enumerate(en_words)})
    return {
        f"where_{i}": Choice(
            instructions=f'Which word of `target_text` renders `{word_id("S", i)}` '
                         f'("{it_word}")? Follow `guidance`.',
            criteria=criteria,
        )
        for i, it_word in enumerate(it_words)
    }


def build_reverse_questions(it_words: List[str], en_words: List[str],
                            unclaimed: List[int]) -> dict:
    """One Choice per English word in `unclaimed`, asking which Italian word it
    renders - the same question in the other direction."""
    criteria = {NONE_LABEL: "No word of `source_text` is rendered by this English word."}
    criteria.update({word_id("S", i): w for i, w in enumerate(it_words)})
    return {
        f"back_{j}": Choice(
            instructions=f'Which word of `source_text` does `{word_id("W", j)}` '
                         f'("{en_words[j]}") render? Follow `guidance`.',
            criteria=criteria,
        )
        for j in unclaimed
    }


@dataclass
class WordVerdict:
    italian: str
    best: str     # top real English word of the forward pass, kept even when NONE won
    none_prob: float
    prob: float
    status: str
    dist: List[Tuple[str, str, float]]  # (word ID, English word, probability), descending
    # English words assigned to this Italian word as (index, word), so the TSV
    # can join them in the order they appear in the English text
    assigned: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def english(self) -> str:
        return " ".join(word for _, word in sorted(self.assigned)) or "-"


def read_answers(response, it_words: List[str], en_words: List[str]) -> List[WordVerdict]:
    """Turn the forward pass's answers into one verdict per Italian word, in order.

    `best` keeps the top real English word even when NONE wins, so a
    NONE_THRESHOLD sweep can be replayed against the log without another run.
    """
    verdicts = []
    for i, it_word in enumerate(it_words):
        probs = response.choices[f"where_{i}"].probabilities
        none_prob = probs.get(NONE_LABEL, 0.0)
        ranked = sorted(((j, en_words[j], probs.get(word_id("W", j), 0.0))
                         for j in range(len(en_words))), key=lambda e: -e[2])
        dist = [(word_id("W", j), w, p) for j, w, p in ranked[:DIST_MAX] if p >= DIST_FLOOR]
        best_j, best_en, best_prob = ranked[0]

        if none_prob >= NONE_THRESHOLD:
            status, assigned = "unaligned", []
        else:
            status = "aligned" if best_prob >= LOW_CONFIDENCE else "fuzzy"
            assigned = [(best_j, best_en)]
        verdicts.append(WordVerdict(it_word, best_en, none_prob, best_prob, status, dist, assigned))
    return verdicts


def unclaimed_english(verdicts: List[WordVerdict], en_words: List[str]) -> List[int]:
    """Indices of the English words no Italian word took in the forward pass."""
    claimed = {j for v in verdicts for j, _ in v.assigned}
    return [j for j in range(len(en_words)) if j not in claimed]


def attach_reverse(response, verdicts: List[WordVerdict], en_words: List[str],
                   unclaimed: List[int]) -> int:
    """Add each reverse answer's English word to the Italian word it renders,
    and return how many were attached.

    When the two passes disagree - the forward one called the Italian word
    unaligned, this one hands it a counterpart - the alignment wins, since the
    TSV has no way to record a word that is both aligned and not.
    """
    attached = 0
    for j in unclaimed:
        answer = response.choices[f"back_{j}"]
        prob = answer.probabilities[answer.choice]
        none_prob = answer.probabilities.get(NONE_LABEL, 0.0)

        if answer.choice == NONE_LABEL:
            verdict, skip, target = None, "none", NONE_LABEL
        else:
            i = int(answer.choice.removeprefix("S"))
            verdict = verdicts[i]
            # A second "the" for an Italian word that already has one is the
            # other article of the fragment, not a second rendering of that word.
            skip = "dup" if any(w == en_words[j] for _, w in verdict.assigned) else ""
            target = f"{answer.choice}={verdict.italian}"

        log_print(f"      [rev] {word_id('W', j)}={en_words[j]:<14} -> {target:<24} "
                  f"p={prob:.2f} none={none_prob:.2f}"
                  f"{f'  [skipped: {skip}]' if skip else ''}")
        if skip:
            continue

        verdict.assigned.append((j, en_words[j]))
        if verdict.status == "unaligned":
            verdict.status = "reverse"
        attached += 1
    return attached


def ask(client: TypeSafeClient, args: argparse.Namespace, ui: StatusLine, kind: str,
        state: dict, questions: dict) -> object | None:
    """One System One request, retried on failure, its usage added to USAGES.
    Returns None if no attempt succeeded after MAX_ATTEMPTS tries."""
    for attempt in range(MAX_ATTEMPTS):
        started = time.monotonic()
        try:
            response = client.system_one(state=state, questions=questions, model=args.model)
        except Exception as e:
            notify(ui, f"    ✗ {kind} attempt {attempt + 1}/{MAX_ATTEMPTS}: System One call "
                       f"failed after {time.monotonic() - started:.1f}s: {e}", error=True)
            continue
        elapsed = time.monotonic() - started
        usage = Usage(raw={"input_tokens": response.usage.input_tokens,
                           "output_tokens": response.usage.output_tokens})
        USAGES.append(usage)
        notify(ui, f"    ✓ {kind} attempt {attempt + 1}/{MAX_ATTEMPTS}: "
                   f"{len(questions)} question(s) answered ({elapsed:.1f}s, "
                   f"input: {usage.input_tokens:,})")
        return response

    notify(ui, f"    ✗ {kind} failed after {MAX_ATTEMPTS} attempts", error=True)
    return None


def check_group(client: TypeSafeClient, args: argparse.Namespace, ui: StatusLine,
                italian_text: str, english_text: str) -> List[WordVerdict] | None:
    """
    Ask System One for one group's word correspondences: the forward pass, then
    - unless --no-reverse - the reverse pass over what it left unclaimed.
    Returns None if the forward pass never succeeded; a failed reverse
    pass keeps the forward result rather than discarding the group.
    """
    it_words = italian_words(italian_text)
    en_words = english_words(english_text)
    if not it_words or not en_words:
        notify(ui, f"    ✗ group has no Italian or no English word tokens", error=True)
        return None

    response = ask(client, args, ui, "forward",
                          build_state(italian_text, english_text, FORWARD_GUIDANCE),
                          build_questions(it_words, en_words))
    if response is None:
        return None
    verdicts = read_answers(response, it_words, en_words)
    for v in verdicts:
        dist = " ".join(f"{lbl}={w}:{p:.2f}" for lbl, w, p in v.dist)
        log_print(f"      {v.italian:<20} -> {v.best:<20} p={v.prob:.2f} "
                  f"none={v.none_prob:.2f} [{v.status:<9}] {dist}")

    unclaimed = [] if args.no_reverse else unclaimed_english(verdicts, en_words)
    if unclaimed:
        log_print(f"    {len(unclaimed)} unclaimed English word(s): "
                  f"{' '.join(en_words[j] for j in unclaimed)}")
        back = ask(client, args, ui, "reverse",
                               build_state(italian_text, english_text, REVERSE_GUIDANCE),
                               build_reverse_questions(it_words, en_words, unclaimed))
        if back is not None:
            attached = attach_reverse(back, verdicts, en_words, unclaimed)
            log_print(f"    {attached}/{len(unclaimed)} attached")

    return verdicts


# ============================================================================
# Driver
# ============================================================================

def write_tsv(results: List[Tuple[int, List[WordRow]]], path: Path) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write("Group\tItalian\tEnglish\n")
        for group, rows in results:
            for row in rows:
                f.write(f"{group}\t{row.italian}\t{row.english}\n")


def load_existing_tsv(path: Path) -> Dict[int, List[WordRow]]:
    """
    Load a previous run's `<NN>-3-jev.tsv` as {group serial: rows}, in the file's
    row order. Returns {} if the file doesn't exist yet. The caller only reuses a
    group's cached rows if its Italian column still matches the freshly
    recomputed words for that group - a stale file (e.g. after a --block-size
    change) falls back to reprocessing instead of silently keeping mismatched
    rows.
    """
    existing: Dict[int, List[WordRow]] = {}
    if not path.exists():
        return existing
    lines = path.read_text(encoding='utf-8').splitlines()
    for line in lines[1:]:  # skip header
        if not line:
            continue
        group, italian, english = line.split('\t')
        existing.setdefault(int(group), []).append(WordRow(italian, english))
    return existing


def check_canto(client: TypeSafeClient, canticle: str, canto: int, args: argparse.Namespace,
                n_cantos: int, ui: StatusLine) -> None:
    """
    Run the word-correspondence check for one canto's confirmed align3
    (`<NN>-3.txt`) groups, writing `<NN>-3-jev.tsv`. `n_cantos` feeds the status
    bar's label (`{canticle} {canto}/{n_cantos}`), mirroring align.align_canto.
    Usage accumulates in USAGES; this canto's share is only shown (and
    logged), recording is left to main().
    """
    out_dir = ALIGNMENT_DIR / canticle
    ranges_path = out_dir / f"{canto:02d}-ranges.tsv"
    tercet_path = out_dir / f"{canto:02d}-3.txt"
    words_path = out_dir / f"{canto:02d}-3-jev.tsv"
    log_path = out_dir / f"{canto:02d}-3-jev.log"
    label = f"{canticle.capitalize()} {canto}/{n_cantos}"

    global _log_file
    with open(log_path, 'w', encoding='utf-8') as log_f:
        _log_file = log_f

        log_print(f"=== {canticle.capitalize()} Canto {canto} align3 word-check (check_align3_jev.py) ===")
        log_print(f"Model: {args.model}, Block size: {args.block_size}, Test: {args.test}")
        log_print(f"Thresholds: none >= {NONE_THRESHOLD}, low confidence < {LOW_CONFIDENCE}; "
                  f"reverse pass: {not args.no_reverse}")
        log_print()

        if not ranges_path.exists() or not tercet_path.exists():
            missing = ranges_path if not ranges_path.exists() else tercet_path
            notify(ui, f"✗ {label}: {missing} not found - run align.py first", error=True)
            return None

        italian_lines = align.load_italian_lines(canticle, canto)
        ranges = align.load_ranges_tsv(str(ranges_path))
        groups = align.expected_tercet_groups(italian_lines, ranges, args.block_size)
        tercets = tercet_path.read_text(encoding='utf-8').splitlines()
        if len(tercets) != len(groups):
            notify(ui, f"✗ {label}: {tercet_path} has {len(tercets)} row(s) but "
                       f"{len(groups)} expected for block-size {args.block_size} - "
                       f"stale file?", error=True)
            return None

        if args.test:
            groups, tercets = groups[:1], tercets[:1]

        existing = load_existing_tsv(words_path)

        first_usage = len(USAGES)
        kept = 0
        results: List[Tuple[int, List[WordRow]]] = []
        with ui.progress(len(italian_lines), label=label, dual=True) as prog:
            for index, (group, english_text) in enumerate(zip(groups, tercets), 1):
                italian_text = ' '.join(line.full_text for line in group)
                lo, hi = group[0].line_num, group[-1].line_num
                prog.update(lo)
                log_print("")
                log_print(f"Group {index}/{len(groups)} (lines {lo}-{hi})")
                log_print(f"  Italian: {italian_text}")
                log_print(f"  English: {english_text}")

                cached = existing.get(index)
                words = italian_words(italian_text)
                if cached is not None and [row.italian for row in cached] == words:
                    log_print(f"  kept from {words_path.name}")
                    results.append((index, cached))
                    kept += 1
                    write_tsv(results, words_path)
                    continue

                if not english_text.strip():
                    log_print("  blank align3 row - skipped")
                    continue
                verdicts = check_group(client, args, ui, italian_text, english_text)
                if verdicts is None:
                    continue
                results.append((index, [WordRow(v.italian, v.english) for v in verdicts]))
                write_tsv(results, words_path)

        suffix = f" ({kept} kept from disk)" if kept else ""
        notify(ui, f"✓ {label}: {len(results)}/{len(groups)} group(s) checked{suffix}")

        # inside the log-file block so the total lands in the log next to the
        # per-request lines it sums
        canto_usages = USAGES[first_usage:]
        if canto_usages:
            notify(ui, f"✓ Usage: {format_usage_line(args.model, sum(canto_usages))}")

    ui.log(f"✓ Words: {words_path}")
    ui.log(f"✓ Log: {log_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Check word-level Italian-English correspondence in align.py's "
                    "confirmed align3 (<NN>-3.txt) output, using TypeSafe System One")
    parser.add_argument("canticle", choices=CANTICLES, help="Canticle name")
    parser.add_argument("-c", "--canto", help=dante_corpus.api.CANTO_SPEC_HELP)
    parser.add_argument("-m", "--model", default="jev-latest", help="TypeSafe model to use")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Per-request timeout in seconds (default: 120)")
    parser.add_argument("--block-size", type=int, default=align.DEFAULT_BLOCK_SIZE,
                        help=f"Italian lines per align3 group, must match the value align.py used "
                             f"(default: {align.DEFAULT_BLOCK_SIZE})")
    parser.add_argument("--no-reverse", action="store_true",
                        help="Skip the reverse pass, leaving every Italian word with at most "
                             "one English word (one request per group instead of two)")
    parser.add_argument("--test", action="store_true",
                        help="Process only the first group of each canto, for a quick smoke test")
    parser.add_argument("--scores", action="store_true",
                        help="Score an already-written <NN>-3-jev.tsv instead of calling "
                             "System One: report each group's deficit and surplus rate "
                             "to stdout and flag the outliers")
    args = parser.parse_args()

    global USAGE_PATH
    USAGE_PATH = find_usage_file()

    if err := dante_corpus.api.check_canto_spec([args.canticle], args.canto):
        parser.error(err)

    if args.scores:
        # check_align3.py's own scoring, pointed at this script's table.
        check_align3.run_scores(args, tag="-jev")
        return

    ui = StatusLine()
    n_cantos = len(dante_corpus.api.cantos(args.canticle))
    try:
        with TypeSafeClient(timeout=args.timeout) as client:
            for canto in dante_corpus.api.select_cantos(args.canticle, args.canto):
                check_canto(client, args.canticle, canto, args, n_cantos, ui)
    finally:
        # Record silently so an interrupted run still logs what it consumed;
        # the report below is printed only on normal completion.
        if USAGES and USAGE_PATH is not None:
            append_usage(sum(USAGES), args.model, USAGE_PATH)

    if USAGES:
        print(f"\n--- Total Usage ---\n{sum(USAGES)}")
        if USAGE_PATH is not None:
            print()
            print_today_totals(USAGE_PATH, models=[args.model])


if __name__ == '__main__':
    main()
