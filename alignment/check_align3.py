"""
Check word-level Italian-English correspondence in align.py's already-confirmed
align3 output: for each tercet-sized group (`<NN>-3.txt`, stage 2 of align.py's
pipeline - see ALGORITHM.md), shows the group's Italian lines and its Norton
English fragment, then asks the LLM to fill in a word-correspondence table - one
row per Italian word, in original order, with the English word(s) it corresponds
to (or `-` if none).

This is a checking tool, not part of the alignment pipeline: it only reads
align.py's output (`<NN>-ranges.tsv`, `<NN>-3.txt`) and never writes to them.
Results are written to `<NN>-3.tsv` (`Group<TAB>Italian<TAB>English`, one row per
Italian word - `Group` is the align3 group's serial number), with a full trace
in `<NN>-3-words.log` (printed to console as the script runs), mirroring
align.py's own output convention. A group already present in an existing
`<NN>-3.tsv` (Italian words unchanged) is kept as-is and skipped, so a rerun
after an interrupted or partial run only fills in what's missing; delete the
file (or edit out a group's rows) to force it to be rechecked.

Token usage is appended to the shared account-level usage.jsonl (see
llm7shi.usage.find_usage_file) once per canto right after that canto finishes,
not accumulated across cantos - the run's final on-screen total is a
display-only sum of those already-recorded per-canto entries, so it is never
written again itself.

    uv run python alignment/check_align3.py inferno -c 1 -m openai:gpt-5.6-terra

`--scores` scores an already-written `<NN>-3.tsv` instead of calling the LLM:
it reads the table back and reports, per group, the deficit and surplus rates
described under "Two rates" below. It makes no LLM call and writes no file at
all - the scores go to stdout as one TSV, the closing summary to stderr - so it
is safe to run over the whole poem and can be redirected or piped.

    uv run python alignment/check_align3.py inferno --scores
    uv run python alignment/check_align3.py inferno --scores > scores.tsv

Both rates are computed from the table plus the group's English fragment, so any
table in the same three columns scores the same way. `score_canto()` and
`run_scores()` therefore take a filename `tag`, and check_align3_jev.py's own
`--scores` calls them to score its `<NN>-3-jev.tsv` with this same code.

Two rates
---------

The filled-in table is an assignment between the group's Italian and English
words, and reading it from either end answers a different question:

- **deficit** - the fraction of Italian words marked `-`. High when the group's
  English fragment is missing text those Italian lines need.
- **surplus** - the fraction of the group's English words that no Italian word
  claimed. High when the fragment carries text belonging to another group.

A group whose English fragment was displaced raises exactly one of the two, so
neither rate alone finds both failures, and a group scoring high on surplus is
usually adjacent to the group that scores high on deficit - the same displaced
text seen from its two ends.
"""

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, NamedTuple, Tuple

ALIGNMENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ALIGNMENT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

import align
import dante_corpus
from dante_corpus import tokenize, has_alpha
from llm7shi import Client
from llm7shi.statusline import StatusLine
from llm7shi.usage import append_usage, find_usage_file, print_today_totals

USAGE_PATH = find_usage_file()

CANTICLES = ["inferno", "purgatorio", "paradiso"]

# Retries per group's correspondence-table call before giving up
MAX_ATTEMPTS = 3

# Rates at or above which --scores flags a group. Both are the 99th percentile
# of their own rate over the 4,841 groups of the full 100-canto run, so roughly
# 1% of groups clear each on a canto whose split is sound.
DEFICIT_FLAG = 0.271
SURPLUS_FLAG = 0.312


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
# Word-correspondence table: build the prompt, parse and validate the reply
# ============================================================================

def italian_words(text: str) -> List[str]:
    """Italian word tokens via dante_corpus.tokenize, in original order, with
    punctuation/symbol tokens (has_alpha False) excluded."""
    return [t for t in tokenize(text) if has_alpha(t)]


# The English side cannot reuse dante_corpus.tokenize: that splits at an
# apostrophe because Italian elides before one ("l'ora" is two words), where
# English keeps it inside the word ("God's", "glow'd"). Hyphenated compounds
# stay whole too - Norton renders "lonza" as "she-leopard". A leading or
# trailing apostrophe is a quote mark and stays out.
ENGLISH_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")


def english_words(text: str) -> List[str]:
    """English word tokens, in original order, with punctuation and digits
    excluded and intra-word apostrophes and hyphens kept."""
    return ENGLISH_WORD_RE.findall(text)


class WordRow(NamedTuple):
    italian: str
    english: str


def build_prompt(italian_text: str, english_text: str, words: List[str]) -> str:
    prototype_rows = "\n".join(f"|{w}| |" for w in words)
    return f"""Below is an Italian text and its English (Norton) translation, followed by a
word-correspondence table: one row per Italian word above, in the same order.

Fill in the "English" column of each row with the English word(s) from the text
above that correspond to that Italian word. If an Italian word has no
corresponding English word, put `-`. Do not change the "Italian" column, and do
not add, remove, merge, or reorder rows - the table must keep exactly {len(words)}
row(s), one per Italian word shown.

[Italian]
{italian_text}

[English]
{english_text}

|Italian|English|
|---|---|
{prototype_rows}

Output only the filled-in table (header and separator included), no other text."""


TABLE_ROW_RE = re.compile(r'^\s*\|(.*)\|(.*)\|\s*$')


def parse_word_table(text: str, words: List[str]) -> List[WordRow] | None:
    """
    Parse a markdown table response into one WordRow per `words` entry, in
    order. Skips the header row ("Italian"/"English") and the `---`
    separator row. Returns None if the parsed Italian column doesn't
    reproduce `words` exactly (count and content, in order) - the caller
    retries wholesale on failure, mirroring align.py's split validation.
    """
    rows: List[WordRow] = []
    for line in text.strip().splitlines():
        match = TABLE_ROW_RE.match(line)
        if not match:
            continue
        italian, english = match.group(1).strip(), match.group(2).strip()
        if italian.lower() == "italian" or set(italian) <= {'-'}:
            continue  # header or separator row
        rows.append(WordRow(italian, english or "-"))

    if [row.italian for row in rows] != words:
        return None
    return rows


def check_group(args: argparse.Namespace, ui: StatusLine, italian_text: str,
                english_text: str) -> Tuple[List[WordRow] | None, object]:
    """
    Ask the LLM to fill in the word-correspondence table for one group.
    Retries on parse/validation failure. Returns (None, None) if no attempt
    validated after MAX_ATTEMPTS tries.
    """
    words = italian_words(italian_text)
    prompt = build_prompt(italian_text, english_text, words)

    for attempt in range(MAX_ATTEMPTS):
        client = Client(model=args.model, include_thoughts=args.think, temperature=args.temperature,
                        file=ui.stream, show_params=False)
        started = time.monotonic()
        ui.log("")
        try:
            response = client(prompt)
        except Exception as e:
            ui.stream.end()
            notify(ui, f"    ✗ attempt {attempt + 1}/{MAX_ATTEMPTS}: LLM call failed "
                  f"after {time.monotonic() - started:.1f}s: {e}", error=True)
            continue
        ui.stream.end()
        elapsed = time.monotonic() - started

        rows = parse_word_table(response.text, words)
        if rows is None:
            notify(ui, f"    ✗ attempt {attempt + 1}/{MAX_ATTEMPTS}: table did not reproduce "
                  f"the {len(words)} Italian word(s) in order ({elapsed:.1f}s)", error=True)
            log_print(f"      response: {response.text!r}")
            continue

        notify(ui, f"    ✓ attempt {attempt + 1}/{MAX_ATTEMPTS}: accepted ({elapsed:.1f}s)")
        return rows, response.usage

    notify(ui, f"    ✗ failed after {MAX_ATTEMPTS} attempts", error=True)
    return None, None


# ============================================================================
# Scoring a filled-in table: the deficit and surplus rates (--scores)
# ============================================================================

class GroupScore(NamedTuple):
    deficit: float          # Italian words marked `-`, as a fraction
    surplus: float          # English words no Italian word claimed, as a fraction
    n_italian: int
    n_english: int
    unclaimed: List[str]    # the surplus English words, in the fragment's order


def surplus_words(english_text: str, rows: List[WordRow]) -> List[str]:
    """
    The group's English words that no row of the table claimed, in the order
    they appear in the fragment.

    Matching is by lowercased token and consumes one claim per occurrence, so a
    fragment's second "the" counts as surplus unless a second row claimed it. A
    row may name a word the fragment does not contain (the model occasionally
    writes an inflected form); such a claim matches nothing and is ignored
    rather than cancelling an unrelated word.
    """
    claimed = Counter()
    for row in rows:
        if row.english != '-':
            claimed.update(word.lower() for word in english_words(row.english))

    unclaimed = []
    for word in english_words(english_text):
        key = word.lower()
        if claimed[key]:
            claimed[key] -= 1
        else:
            unclaimed.append(word)
    return unclaimed


def score_group(english_text: str, rows: List[WordRow]) -> GroupScore | None:
    """Both rates for one group's filled-in table, or None if it has no words
    to divide by."""
    n_italian = len(rows)
    n_english = len(english_words(english_text))
    if not n_italian or not n_english:
        return None
    unclaimed = surplus_words(english_text, rows)
    return GroupScore(
        deficit=sum(1 for row in rows if row.english == '-') / n_italian,
        surplus=len(unclaimed) / n_english,
        n_italian=n_italian,
        n_english=n_english,
        unclaimed=unclaimed,
    )


SCORES_HEADER = ("Canticle\tCanto\tGroup\tStart\tEnd\tItalianWords\tEnglishWords\t"
                 "Deficit\tSurplus\tSurplusWords")


def err(text: str) -> None:
    """Print to stderr, keeping stdout for the scores alone."""
    print(text, file=sys.stderr)


def score_canto(canticle: str, canto: int, args: argparse.Namespace,
                tag: str = "") -> List[Tuple[int, int, int, GroupScore]] | None:
    """
    Score one canto's already-written `<NN>-3{tag}.tsv`, writing its rows to
    stdout. Writes no file, and the table it reads is left untouched. Returns
    the scored rows, or None after reporting on stderr why the canto's inputs
    are missing or stale.

    `tag` selects which checker's table to score: "" for this script's own,
    "-jev" for check_align3_jev.py's. The scoring is identical either way - the
    surplus rate comes from the group's English fragment in `<NN>-3.txt` minus
    what the table claimed, so it needs nothing of the table but the three
    columns both checkers write. Only `args.block_size` is read from `args`.

    There is no status line: scoring a canto is pure computation over two files
    already on disk, and finishes before a progress display would mean anything.
    """
    stem = f"{canto:02d}-3{tag}"
    out_dir = ALIGNMENT_DIR / canticle
    ranges_path = out_dir / f"{canto:02d}-ranges.tsv"
    tercet_path = out_dir / f"{canto:02d}-3.txt"
    words_path = out_dir / f"{stem}.tsv"
    label = f"{canticle.capitalize()} {canto}"

    for path in (ranges_path, tercet_path, words_path):
        if not path.exists():
            err(f"✗ {label}: {path} not found - run the check first")
            return None

    italian_lines = align.load_italian_lines(canticle, canto)
    ranges = align.load_ranges_tsv(str(ranges_path))
    groups = align.expected_tercet_groups(italian_lines, ranges, args.block_size)
    tercets = tercet_path.read_text(encoding='utf-8').splitlines()
    if len(tercets) != len(groups):
        err(f"✗ {label}: {tercet_path} has {len(tercets)} row(s) but "
            f"{len(groups)} expected for block-size {args.block_size} - stale file?")
        return None

    existing = load_existing_tsv(words_path)
    scores = []
    for index, (group, english_text) in enumerate(zip(groups, tercets), 1):
        rows = existing.get(index)
        if rows is None or not english_text.strip():
            continue
        # Same staleness guard as the check itself: only score a table whose
        # Italian column still matches the freshly recomputed words.
        italian_text = ' '.join(line.full_text for line in group)
        if [row.italian for row in rows] != italian_words(italian_text):
            err(f"✗ {label}: group {index} in {words_path.name} does not "
                f"match its Italian words - stale file?")
            return None
        score = score_group(english_text, rows)
        if score is not None:
            scores.append((index, group[0].line_num, group[-1].line_num, score))

    if not scores:
        err(f"✗ {label}: {words_path} has no scorable group")
        return None

    # The scores themselves go to stdout, one row per group, so a run can be
    # redirected or piped; everything else this path prints goes to stderr.
    for group, lo, hi, s in scores:
        print(f"{canticle}\t{canto}\t{group}\t{lo}\t{hi}\t{s.n_italian}\t{s.n_english}\t"
              f"{s.deficit:.3f}\t{s.surplus:.3f}\t{' '.join(s.unclaimed)}")
    return scores


def run_scores(args: argparse.Namespace, tag: str = "") -> None:
    """
    Score every canto named by `args.canticle`/`args.canto`, writing the scores
    to stdout as a single TSV (one row per group, `SCORES_HEADER` first) and the
    closing summary and any errors to stderr. The whole `--scores` driver, shared
    with check_align3_jev.py so that both checkers' tables are scored by exactly
    the same code.

    No file is written: a scored table is cheap enough to recompute that keeping
    one on disk only invites it to go stale against the table it came from.
    Redirect stdout to keep a copy.

    `tag` is `score_canto`'s: "" for `<NN>-3.tsv`, "-jev" for `<NN>-3-jev.tsv`.
    """
    print(SCORES_HEADER)
    scored = flagged = cantos = 0
    for canto in dante_corpus.api.select_cantos(args.canticle, args.canto):
        scores = score_canto(args.canticle, canto, args, tag)
        if scores is None:
            continue
        cantos += 1
        scored += len(scores)
        flagged += sum(1 for s in scores
                       if s[3].deficit >= DEFICIT_FLAG or s[3].surplus >= SURPLUS_FLAG)
    err(f"--- {scored} group(s) scored over {cantos} canto(s), {flagged} flagged "
        f"(deficit >= {DEFICIT_FLAG}, surplus >= {SURPLUS_FLAG}) ---")
    if tag:
        # Both thresholds are percentiles of Terra's distribution, not this
        # table's; see ALIGN3.md.
        err("    thresholds are Terra percentiles - flags on this table are provisional")


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
    Load a previous run's `<NN>-3.tsv` as {group serial: rows}, in the file's
    row order. Returns {} if the file doesn't exist yet. The caller only
    reuses a group's cached rows if its Italian column still matches the
    freshly recomputed words for that group - a stale file (e.g. after a
    --block-size change) falls back to reprocessing instead of silently
    keeping mismatched rows.
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


def check_canto(canticle: str, canto: int, args: argparse.Namespace, n_cantos: int,
                ui: StatusLine) -> object | None:
    """
    Run the word-correspondence check for one canto's confirmed align3
    (`<NN>-3.txt`) groups, writing `<NN>-3.tsv`. `n_cantos` feeds the status
    bar's label (`{canticle} {canto}/{n_cantos}`), mirroring
    align.align_canto. This canto's summed Usage is appended to the shared
    usage.jsonl before returning (once per canto, not accumulated across cantos) and
    also returned, for the caller's own display-only running total (None if
    no LLM call succeeded).
    """
    out_dir = ALIGNMENT_DIR / canticle
    ranges_path = out_dir / f"{canto:02d}-ranges.tsv"
    tercet_path = out_dir / f"{canto:02d}-3.txt"
    words_path = out_dir / f"{canto:02d}-3.tsv"
    log_path = out_dir / f"{canto:02d}-3-words.log"
    label = f"{canticle.capitalize()} {canto}/{n_cantos}"

    global _log_file
    with open(log_path, 'w', encoding='utf-8') as log_f:
        _log_file = log_f

        log_print(f"=== {canticle.capitalize()} Canto {canto} align3 word-check (check_align3.py) ===")
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}, "
                  f"Block size: {args.block_size}, Test: {args.test}")
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

        # Blank rows are the pending marker (align.py's convention): a group
        # already on disk in words_path with matching Italian words is kept
        # as-is and skipped, so a rerun only fills in what's missing.
        existing = load_existing_tsv(words_path)

        usages = []
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
                rows, usage = check_group(args, ui, italian_text, english_text)
                if rows is None:
                    continue
                if usage:
                    usages.append(usage)
                results.append((index, rows))
                write_tsv(results, words_path)

        suffix = f" ({kept} kept from disk)" if kept else ""
        notify(ui, f"✓ {label}: {len(results)}/{len(groups)} group(s) checked{suffix}")

    ui.log(f"✓ Words: {words_path}")
    ui.log(f"✓ Log: {log_path}")

    if not usages:
        return None
    canto_usage = sum(usages)
    append_usage(canto_usage, args.model, USAGE_PATH)
    ui.log(f"✓ Usage: {canto_usage} -> {USAGE_PATH}")
    return canto_usage


def main():
    parser = argparse.ArgumentParser(
        description="Check word-level Italian-English correspondence in align.py's "
                    "confirmed align3 (<NN>-3.txt) output")
    parser.add_argument("canticle", choices=CANTICLES, help="Canticle name")
    parser.add_argument("-c", "--canto", help=dante_corpus.api.CANTO_SPEC_HELP)
    parser.add_argument("-m", "--model", default="openai:gpt-5.6-terra", help="LLM model to use")
    parser.add_argument("--temperature", type=float, default=1.0, help="LLM temperature (default: 1.0)")
    parser.add_argument("--think", action="store_true", help="Enable LLM thinking (disabled by default)")
    parser.add_argument("--block-size", type=int, default=align.DEFAULT_BLOCK_SIZE,
                        help=f"Italian lines per align3 group, must match the value align.py used "
                             f"(default: {align.DEFAULT_BLOCK_SIZE})")
    parser.add_argument("--test", action="store_true",
                        help="Process only the first group of each canto, for a quick smoke test")
    parser.add_argument("--scores", action="store_true",
                        help="Score an already-written <NN>-3.tsv instead of calling the LLM: "
                             "report each group's deficit and surplus rate to "
                             "stdout and flag the outliers")
    args = parser.parse_args()

    if err := dante_corpus.api.check_canto_spec([args.canticle], args.canto):
        parser.error(err)

    if args.scores:
        run_scores(args)
        return

    ui = StatusLine()
    n_cantos = len(dante_corpus.api.cantos(args.canticle))

    usages = []
    for canto in dante_corpus.api.select_cantos(args.canticle, args.canto):
        usage = check_canto(args.canticle, canto, args, n_cantos, ui)
        if usage:
            usages.append(usage)

    if usages:
        total_usage = sum(usages)
        print(f"--- Total Usage ---\n{total_usage}\n")
        print_today_totals(USAGE_PATH)


if __name__ == '__main__':
    main()
