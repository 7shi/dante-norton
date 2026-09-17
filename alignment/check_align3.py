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

Token usage is appended to the repo root's `usage.jsonl` once per canto right
after that canto finishes (see dante_norton.usage), not accumulated across
cantos - the run's final on-screen total is a display-only sum of those
already-recorded per-canto entries, so it is never written again itself.

    uv run python alignment/check_align3.py inferno -c 1 -m openai:gpt-5.6-terra
"""

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Tuple

ALIGNMENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ALIGNMENT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

import align
import dante_corpus
from dante_corpus import tokenize, has_alpha
from dante_norton.usage import USAGE_PATH, append_usage
from llm7shi import Client
from llm7shi.statusline import StatusLine

CANTICLES = ["inferno", "purgatorio", "paradiso"]

# Retries per group's correspondence-table call before giving up
MAX_ATTEMPTS = 3


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
    align.align_canto. This canto's summed Usage is appended to usage.jsonl
    before returning (once per canto, not accumulated across cantos) and
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
    append_usage(canto_usage, args.model)
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
    args = parser.parse_args()

    if err := dante_corpus.api.check_canto_spec([args.canticle], args.canto):
        parser.error(err)

    ui = StatusLine()
    n_cantos = len(dante_corpus.api.cantos(args.canticle))
    usages = []
    for canto in dante_corpus.api.select_cantos(args.canticle, args.canto):
        usage = check_canto(args.canticle, canto, args, n_cantos, ui)
        if usage:
            usages.append(usage)

    if usages:
        total_usage = sum(usages)
        ui.log(f"--- Total Usage ---")
        ui.log(f"{total_usage}")


if __name__ == '__main__':
    main()
