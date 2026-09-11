"""
Align Italian lines from Dante's *Inferno* with Charles Eliot Norton's
English prose translation: a three-stage pipeline that hierarchically
decomposes each Norton paragraph down to one row per Italian line, never
extracting or verbatim-matching text - each stage only rearranges words
already known to belong to a given span, validated by word-multiset
equality. See ALGORITHM.md for the full design rationale.

Stages (run in sequence, in one process):
1. Whole-canto: identify, for each Norton paragraph, the range of Italian
   lines it corresponds to (one LLM call, no text extraction).
2. Paragraph -> tercet: split each paragraph's Norton text into
   `--block-size`-line (default 3) groups.
3. Tercet -> line: further split any multi-line group into one fragment per
   Italian line.

Output (English text only, no Italian side - one file per stage, written to
alignment/<cantica>/, canto-number-prefixed):
- `<NN>-ranges.tsv`: paragraph<TAB>start_line<TAB>end_line, one row per
  Norton paragraph (stage 1).
- `<NN>-3.txt`: one Norton fragment per line, one line per tercet-sized
  group (stage 2). A group whose split never validated is kept merged as a
  single line spanning multiple Italian lines.
- `<NN>-1.txt`: same, one line per Italian line (stage 3), merged fallback
  likewise possible.
- `<NN>.log`: full processing trace (all three stages), printed to console
  as the script runs; unconditionally overwritten every run.

Each stage is skipped when its output file already exists: stage 1 loads
`<NN>-ranges.tsv` instead of calling the LLM, and likewise for stages 2 and
3 with their own output files - `<NN>-3.txt`/`<NN>-1.txt` carry no Italian
side, so a skipped stage's line groups are recomputed deterministically
(assuming no merge fallback) and cross-checked against the loaded file's
row count; a mismatch aborts with a message to delete the file and
regenerate. Delete any of the three files to force that stage (and any
stage after it that depends on newly generated input) to rerun.
"""

import re
import sys
import json
import argparse
from pathlib import Path
from typing import List, NamedTuple, Tuple
from pydantic import BaseModel, Field

ALIGNMENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ALIGNMENT_DIR.parent

sys.path.insert(0, str(REPO_ROOT))

import dante_corpus
from dante_norton import Canto, LLMClient


# Retries per range-identification or split call before giving up
MAX_ATTEMPTS = 3

# Default number of Italian lines per group in stage 2 (normally a tercet)
DEFAULT_BLOCK_SIZE = 3


# ============================================================================
# Logging
# ============================================================================

_log_file = None


def log_print(*args, **kwargs):
    """Print to log file only"""
    if _log_file:
        print(*args, **kwargs, file=_log_file)
        _log_file.flush()


def notify(*args, **kwargs):
    """Print to both console (progress/errors) and log file"""
    print(*args, **kwargs, flush=True)
    log_print(*args, **kwargs)


# ============================================================================
# Shared parsing/loading helpers
# ============================================================================

def parse_json_object(text: str) -> dict:
    """
    Parse a JSON object out of an LLM response, tolerating surrounding
    Markdown code fences (opening and/or closing, or neither) and any
    other trailing text the model may add.
    """
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)  # strip leading fence, if any
    return json.JSONDecoder().raw_decode(text)[0]  # ignore trailing garbage


class ItalianLine:
    """Represents a single line of Italian source text, read via dante_corpus."""

    def __init__(self, full_text: str, line_num: int):
        self.full_text = full_text
        self.line_num = line_num

    def __repr__(self):
        return f"ItalianLine({self.full_text!r}, line_num={self.line_num})"


def load_italian_lines(cantica: str, canto: int) -> List[ItalianLine]:
    """Load a canto's Italian lines via the dante_corpus API."""
    return [ItalianLine(line.text, line.no)
            for line in dante_corpus.canto(cantica, canto).lines()]


def load_norton_paragraphs(filepath: str) -> List[Tuple[int, str]]:
    """
    Load Norton paragraphs as (paragraph_num, text) pairs, numbering them
    1-based by position and skipping paragraph 1 (the summary paragraph, not
    part of the translation) - the numbering stage 1's ranges are keyed by.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        norton_canto = Canto(f.read())

    paragraphs = []
    for para_num, (text, _) in enumerate(norton_canto.lines, 1):
        if not text.strip():
            continue
        if para_num == 1:
            continue  # summary paragraph, not part of the translation
        clean_text = re.sub(r'\[\d+\]', '', text)
        paragraphs.append((para_num, clean_text))
    return paragraphs


def word_multiset(text: str) -> List[str]:
    """Lowercased word tokens, for order-insensitive content comparison."""
    return sorted(re.findall(r"\w+", text.lower(), re.UNICODE))


# ============================================================================
# Stage 1: paragraph -> Italian line range (whole canto, one LLM call)
# ============================================================================

class ParagraphRange(BaseModel):
    """One Norton paragraph's corresponding Italian line range."""
    paragraph: int
    start_line: int
    end_line: int


class RangeMapping(BaseModel):
    """One entry per Norton paragraph, in paragraph order."""
    ranges: List[ParagraphRange] = Field(description="One entry per Norton paragraph, in order")


def validate_ranges(ranges: List[ParagraphRange], paragraphs: List[Tuple[int, str]],
                    total_lines: int) -> str | None:
    """
    Mechanically check a candidate mapping. Returns None if valid, else a
    description of the first problem found.
    """
    expected_paragraph_nums = [p[0] for p in paragraphs]
    got_paragraph_nums = [r.paragraph for r in ranges]
    if got_paragraph_nums != expected_paragraph_nums:
        return (f"paragraph numbers {got_paragraph_nums} do not match "
                f"expected {expected_paragraph_nums}")

    if ranges[0].start_line != 1:
        return f"first range starts at line {ranges[0].start_line}, expected 1"
    if ranges[-1].end_line != total_lines:
        return f"last range ends at line {ranges[-1].end_line}, expected {total_lines}"

    prev_end = 0
    for r in ranges:
        if r.start_line <= prev_end:
            return f"paragraph {r.paragraph}: start_line {r.start_line} overlaps previous end {prev_end}"
        if r.start_line != prev_end + 1:
            return f"paragraph {r.paragraph}: gap between line {prev_end} and start_line {r.start_line}"
        if r.end_line < r.start_line:
            return f"paragraph {r.paragraph}: end_line {r.end_line} < start_line {r.start_line}"
        if r.end_line > total_lines:
            return f"paragraph {r.paragraph}: end_line {r.end_line} exceeds total {total_lines}"
        prev_end = r.end_line

    return None


def identify_ranges(llm: LLMClient, italian_lines: List[ItalianLine],
                    paragraphs: List[Tuple[int, str]]) -> List[ParagraphRange] | None:
    """
    Ask the LLM, in one call, to map each Norton paragraph to the Italian
    line range it corresponds to. Retries on mechanical validation failure.
    """
    italian_numbered = '\n'.join(f"{line.line_num}. {line.full_text}" for line in italian_lines)
    paragraphs_numbered = '\n\n'.join(f"[Paragraph {num}]\n{text}" for num, text in paragraphs)
    total_lines = len(italian_lines)

    prompt = f"""Below are the full Italian text of a canto (numbered by line) and the full
Norton English prose translation of the same canto (split into paragraphs).

For each Norton paragraph, identify the range of Italian line numbers it
translates (start_line to end_line, inclusive). The ranges must be
contiguous and in order: the first starts at line 1, the last ends at line
{total_lines}, and each paragraph's start_line is exactly one more than the
previous paragraph's end_line (no gaps, no overlaps).

Italian lines (1-{total_lines}):
{italian_numbered}

Norton paragraphs:
{paragraphs_numbered}

Output in JSON format:
- ranges: a list with one entry per Norton paragraph above, in order, each
  with "paragraph" (the paragraph number shown above), "start_line", and
  "end_line\""""

    for attempt in range(MAX_ATTEMPTS):
        llm.history = []
        try:
            response = llm.call(prompt, schema=RangeMapping)
        except Exception as e:
            notify(f"  ✗ LLM call failed: {e}")
            continue

        try:
            result = parse_json_object(response)
            ranges = [ParagraphRange(**r) for r in result.get("ranges", [])]
        except Exception as e:
            log_print(f"  ✗ Failed to parse structured output: {e}")
            continue

        log_print(f"  Candidate ranges: {[(r.paragraph, r.start_line, r.end_line) for r in ranges]}")

        problem = validate_ranges(ranges, paragraphs, total_lines)
        if problem is not None:
            log_print(f"  ✗ Invalid: {problem}")
            continue

        log_print(f"  ✓ Accepted")
        return ranges

    notify(f"  ✗ Failed after {MAX_ATTEMPTS} attempts")
    return None


def write_ranges_tsv(ranges: List[ParagraphRange], path: str):
    with open(path, 'w', encoding='utf-8') as f:
        for r in ranges:
            f.write(f"{r.paragraph}\t{r.start_line}\t{r.end_line}\n")


def load_ranges_tsv(path: str) -> List[ParagraphRange]:
    """Load a ranges TSV as written by write_ranges_tsv (stage 1's own output)."""
    ranges = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line:
                continue
            paragraph, start_line, end_line = line.split('\t')
            ranges.append(ParagraphRange(paragraph=int(paragraph), start_line=int(start_line),
                                         end_line=int(end_line)))
    return ranges


# ============================================================================
# Stages 2 & 3: rearranging split (paragraph -> tercet, tercet -> line)
# ============================================================================

def chunk_lines(lines: List[ItalianLine], block_size: int) -> List[List[ItalianLine]]:
    """
    Split a flat list of Italian lines into consecutive groups of
    `block_size` lines. The final group may be shorter when the line count
    is not a multiple of `block_size` (a paragraph's line range is not
    guaranteed to be, in general).
    """
    return [lines[i:i + block_size] for i in range(0, len(lines), block_size)]


def expected_tercet_groups(italian_lines: List[ItalianLine], ranges: List[ParagraphRange],
                           block_size: int) -> List[List[ItalianLine]]:
    """
    The stage-2 line groups a fresh run would produce for `ranges`, assuming
    no merge fallback - used to resume from an existing `<NN>-3.txt` (which
    carries no Italian side to reconstruct groups from) by recomputing the
    same deterministic chunking. Does not account for a paragraph that was
    actually kept merged on the run that produced the file; the caller must
    cross-check the row count against this and bail out if they disagree.
    """
    groups = []
    for r in ranges:
        group_lines = italian_lines[r.start_line - 1:r.end_line]
        groups.extend(chunk_lines(group_lines, block_size))
    return groups


def parse_numbered_lines(text: str, n: int, start_num: int = 1) -> List[str] | None:
    """
    Parse a response formatted as `n` numbered lines ("<start_num> ...",
    "<start_num + 1> ...", ..., one per line, matching the numbering shown to
    the model in the prompt's Italian line groups) into an ordered list of
    fragments. Returns None if the numbers found don't form exactly
    start_num..start_num+n-1, in order, with no gaps, duplicates, or extras -
    it does not attempt partial recovery, since the caller retries wholesale
    on failure.
    """
    fragments = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r'(\d+)[.)]?\s+(.*)', line)
        if not match:
            return None
        num, fragment = int(match.group(1)), match.group(2)
        if num != start_num + len(fragments):
            return None
        fragments.append(fragment)
    return fragments if len(fragments) == n else None


def split_norton_span(llm: LLMClient, italian_groups: List[List[ItalianLine]],
                      matched_text: str, start_num: int = 1) -> List[str] | None:
    """
    Split a Norton text (`matched_text`) into one fragment per group of
    Italian lines in `italian_groups`, rearranging words as needed but never
    substituting them (mirrors the Bard prompt in PRIOR_WORK.md: "you may
    rearrange the words in B as needed, but do not replace them with
    different words").

    Generic over the group size: a group may be a single Italian line (stage
    3's tercet -> line split) or several (stage 2's paragraph -> tercet
    split) - the prompt just shows each group's lines joined together as one
    numbered item.

    Validated mechanically: the fragment count must match the number of
    groups, and the case-insensitive word multiset of the concatenated
    fragments must equal that of `matched_text` exactly (nothing added,
    dropped, or substituted - only reordered and re-split).

    Returns:
        A list of fragments (one per group, in order), or None if no attempt
        validated.
    """
    n = len(italian_groups)
    end_num = start_num + n - 1
    italian_numbered = '\n'.join(
        f"{start_num + i} {'|'.join(line.full_text for line in group)}"
        for i, group in enumerate(italian_groups))
    target_words = word_multiset(matched_text)

    split_prompt = f"""The following English text is a single unit that corresponds to {n} Italian line group(s)
listed below (numbered {start_num}-{end_num} by group; each group's original lines are joined with '|').
Split the English text into exactly {n} fragments, one per Italian line group, in order.

You may REARRANGE the words as needed so each fragment matches its Italian line group's content,
but you must NOT replace, add, or remove any word. Every word in the English text must be
used exactly once across the fragments, in some fragment.

Italian line groups:
{italian_numbered}

English text (split and reorder this, do not change wording):
{matched_text}

Output exactly {n} lines, one per Italian line group above, each starting with that group's
number followed by a space and then the fragment (e.g. "{start_num} <fragment>"). No other text."""

    for attempt in range(MAX_ATTEMPTS):
        llm.history = []
        try:
            response = llm.call(split_prompt)
        except Exception as e:
            notify(f"    ✗ Split LLM call failed: {e}")
            continue

        fragments = parse_numbered_lines(response, n, start_num)
        if fragments is None:
            log_print(f"    ✗ Failed to parse {n} numbered line(s) from response: {response!r}")
            continue

        # Reject any empty (or whitespace-only) fragment: it would otherwise
        # contribute zero words to the multiset check below and pass silently,
        # producing a fragment with no content for that Italian line group.
        if any(not f.strip() for f in fragments):
            log_print(f"    ✗ Split has an empty fragment: {fragments}")
            continue

        combined_words = word_multiset(' '.join(fragments))
        if combined_words != target_words:
            log_print(f"    ✗ Split word multiset does not match source "
                      f"({len(combined_words)} vs {len(target_words)} words)")
            continue

        log_print(f"    ✓ Split accepted: {fragments}")
        return fragments

    notify(f"    ✗ Split failed after {MAX_ATTEMPTS} attempts - keeping merged")
    return None


class FinalRow(NamedTuple):
    """One row of output: a group of Italian lines and its Norton text."""
    italian_lines: List[ItalianLine]  # >1 lines normally at stage 2, or on a merge fallback
    text: str


def split_paragraph(llm: LLMClient, italian_lines: List[ItalianLine], para_num: int,
                    norton_text: str, block_size: int, start_num: int) -> List[FinalRow]:
    """
    Stage 2: split one Norton paragraph's text into tercet-sized (or smaller
    trailing) groups matching its already-known Italian line range. Falls
    back to a single merged row (the whole range) if the split never
    validates.

    `start_num` is this paragraph's first group's serial number in the
    canto-wide group numbering - the caller advances it by this paragraph's
    group count for the next call.
    """
    groups = chunk_lines(italian_lines, block_size)
    print()  # stdout only - visually separates paragraphs in the console
    notify(f"Paragraph {para_num}: lines {italian_lines[0].line_num}-{italian_lines[-1].line_num} "
          f"-> {len(groups)} group(s) (numbered {start_num}-{start_num + len(groups) - 1})")
    log_print(f"  Norton text: {norton_text}")

    if len(groups) == 1:
        # Nothing to split: the whole range is already one group.
        return [FinalRow(groups[0], norton_text)]

    fragments = split_norton_span(llm, groups, norton_text, start_num)
    if fragments is None:
        return [FinalRow(italian_lines, norton_text)]
    return [FinalRow(group, fragment) for group, fragment in zip(groups, fragments)]


def split_group(llm: LLMClient, row: FinalRow) -> List[FinalRow]:
    """Stage 3: split one group's Norton text into one fragment per Italian line."""
    if len(row.italian_lines) == 1:
        return [row]

    nums = ', '.join(str(l.line_num) for l in row.italian_lines)
    print()  # stdout only - visually separates groups in the console
    notify(f"Splitting group: line(s) {nums}: {row.text}")
    groups = [[line] for line in row.italian_lines]
    # Number by each line's actual (canto-wide) line number - already a
    # serial number, so no separate running counter is needed here.
    fragments = split_norton_span(llm, groups, row.text, start_num=row.italian_lines[0].line_num)
    if fragments is None:
        return [row]
    return [FinalRow([line], fragment) for line, fragment in zip(row.italian_lines, fragments)]


def format_final_rows(rows: List[FinalRow]) -> str:
    """Format the final output as bilingual rows, for the log only."""
    lines = []
    for row_num, row in enumerate(rows, 1):
        lines.append(f"=== Row {row_num} ===")
        lines.append("[Italian]")
        for italian_line in row.italian_lines:
            lines.append(italian_line.full_text)
        lines.append("[English (Norton)]")
        lines.append(row.text)
        lines.append("")
    return '\n'.join(lines)


def write_lines(rows: List[FinalRow], path: str):
    """
    Write one line of English text per row - no Italian side, no
    tab-separation. A merge-fallback row (spanning multiple Italian lines)
    still contributes exactly one line, so the output may have fewer lines
    than the canto's Italian line count.
    """
    with open(path, 'w', encoding='utf-8') as f:
        for row in rows:
            f.write(row.text + '\n')


# ============================================================================
# Pipeline driver
# ============================================================================

def run_stage1(llm: LLMClient, italian_lines: List[ItalianLine],
               paragraphs: List[Tuple[int, str]]) -> List[ParagraphRange] | None:
    log_print("=" * 80)
    log_print("STAGE 1: paragraph -> Italian line range")
    log_print("=" * 80)
    log_print(f"{len(italian_lines)} Italian lines, {len(paragraphs)} Norton paragraphs "
              f"(paragraph numbers: {[p[0] for p in paragraphs]})")
    log_print()

    ranges = identify_ranges(llm, italian_lines, paragraphs)
    if ranges is None:
        return None

    log_print()
    for r in ranges:
        span = ' '.join(l.full_text for l in italian_lines[r.start_line - 1:r.end_line])
        log_print(f"Paragraph {r.paragraph}: lines {r.start_line}-{r.end_line}")
        log_print(f"  {span}")
        log_print()
    return ranges


def run_stage2(llm: LLMClient, italian_lines: List[ItalianLine], ranges: List[ParagraphRange],
              paragraphs: dict, block_size: int, test: bool) -> Tuple[List[FinalRow], int, int]:
    log_print("=" * 80)
    log_print("STAGE 2: paragraph -> tercet-sized group")
    log_print("=" * 80)
    log_print(f"Block size: {block_size}, Test: {test}")
    log_print()

    if test:
        ranges = ranges[:1]

    rows: List[FinalRow] = []
    merged_paragraphs = 0
    next_num = 1  # serial group number, canto-wide (never resets per paragraph)
    for r in ranges:
        group_lines = italian_lines[r.start_line - 1:r.end_line]
        para_rows = split_paragraph(llm, group_lines, r.paragraph, paragraphs[r.paragraph],
                                    block_size, next_num)
        if len(para_rows) == 1 and len(para_rows[0].italian_lines) > 1 and len(group_lines) > block_size:
            merged_paragraphs += 1
        rows.extend(para_rows)
        next_num += len(chunk_lines(group_lines, block_size))
        log_print()

    covered_lines = sum(len(row.italian_lines) for row in rows)
    total_lines = sum(r.end_line - r.start_line + 1 for r in ranges)

    log_print("-" * 80)
    log_print("Stage 2 results")
    log_print("-" * 80)
    log_print()
    log_print(format_final_rows(rows))
    log_print()
    log_print(f"Total groups: {len(rows)}")
    log_print(f"Paragraphs kept merged (split failed): {merged_paragraphs}")
    log_print(f"Coverage: {covered_lines}/{total_lines} lines")
    log_print()

    print(f"✓ Stage 2 complete: {len(rows)} groups, "
          f"coverage {covered_lines}/{total_lines}, merged {merged_paragraphs}")
    return rows, covered_lines, total_lines


def run_stage3(llm: LLMClient, group_rows: List[FinalRow], test: bool) -> Tuple[List[FinalRow], int, int]:
    log_print("=" * 80)
    log_print("STAGE 3: tercet -> per-line")
    log_print("=" * 80)
    log_print(f"Test: {test}")
    log_print()

    if test:
        group_rows = group_rows[:1]

    rows: List[FinalRow] = []
    merged_groups = 0
    for group_row in group_rows:
        split_rows = split_group(llm, group_row)
        if len(split_rows) == 1 and len(split_rows[0].italian_lines) > 1:
            merged_groups += 1
        rows.extend(split_rows)
        log_print()

    covered_lines = sum(len(row.italian_lines) for row in rows)
    total_lines = sum(len(row.italian_lines) for row in group_rows)

    log_print("-" * 80)
    log_print("Stage 3 results")
    log_print("-" * 80)
    log_print()
    log_print(format_final_rows(rows))
    log_print()
    log_print(f"Total rows: {len(rows)}")
    log_print(f"Groups kept merged (split failed): {merged_groups}")
    log_print(f"Coverage: {covered_lines}/{total_lines} lines")
    log_print()

    print(f"✓ Stage 3 complete: {len(rows)} rows, "
          f"coverage {covered_lines}/{total_lines}, merged {merged_groups}")
    return rows, covered_lines, total_lines


def align_canto(cantica: str, canto: int, args: argparse.Namespace) -> None:
    """Run the full pipeline for one canto of one cantica."""
    norton_file = REPO_ROOT / "en-norton" / cantica / f"{canto:02d}.txt"
    out_dir = ALIGNMENT_DIR / cantica
    out_dir.mkdir(parents=True, exist_ok=True)
    ranges_path = out_dir / f"{canto:02d}-ranges.tsv"
    tercet_path = out_dir / f"{canto:02d}-3.txt"
    line_path = out_dir / f"{canto:02d}-1.txt"
    log_path = out_dir / f"{canto:02d}.log"

    print(f"Aligning {cantica.capitalize()} Canto {canto}...")

    global _log_file
    with open(log_path, 'w', encoding='utf-8') as log_f:
        _log_file = log_f

        log_print(f"=== {cantica.capitalize()} Canto {canto} Alignment (align.py) ===")
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}, "
                  f"Block size: {args.block_size}, Test: {args.test}")
        log_print()

        italian_lines = load_italian_lines(cantica, canto)
        paragraphs = load_norton_paragraphs(norton_file)

        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)

        if ranges_path.exists():
            ranges = load_ranges_tsv(str(ranges_path))
            notify(f"✓ Stage 1 skipped: {ranges_path} already exists ({len(ranges)} ranges loaded)")
        else:
            ranges = run_stage1(llm, italian_lines, paragraphs)
            if ranges is None:
                print("✗ Stage 1 failed to identify a valid range mapping")
                log_print("FAILED: no valid range mapping produced")
                return
            write_ranges_tsv(ranges, str(ranges_path))
            print(f"✓ Stage 1 complete: {len(ranges)} paragraph ranges")

        if tercet_path.exists():
            expected_groups = expected_tercet_groups(italian_lines, ranges, args.block_size)
            texts = tercet_path.read_text(encoding='utf-8').splitlines()
            if len(texts) != len(expected_groups):
                notify(f"✗ {tercet_path} has {len(texts)} line(s) but {len(expected_groups)} "
                      f"expected for block-size {args.block_size} (a previous merge-fallback row, "
                      f"or a different --block-size, may be the cause) - delete the file to regenerate")
                return
            tercet_rows = [FinalRow(g, t) for g, t in zip(expected_groups, texts)]
            notify(f"✓ Stage 2 skipped: {tercet_path} already exists ({len(tercet_rows)} rows loaded)")
        else:
            tercet_rows, _, _ = run_stage2(llm, italian_lines, ranges, dict(paragraphs),
                                           args.block_size, args.test)
            write_lines(tercet_rows, str(tercet_path))

        if line_path.exists():
            expected_line_count = sum(len(row.italian_lines) for row in tercet_rows)
            texts = line_path.read_text(encoding='utf-8').splitlines()
            if len(texts) != expected_line_count:
                notify(f"✗ {line_path} has {len(texts)} line(s) but {expected_line_count} "
                      f"expected (a previous merge-fallback row may be the cause) - "
                      f"delete the file to regenerate")
                return
            notify(f"✓ Stage 3 skipped: {line_path} already exists ({len(texts)} rows loaded)")
        else:
            line_rows, _, _ = run_stage3(llm, tercet_rows, args.test)
            write_lines(line_rows, str(line_path))

    print(f"✓ Ranges: {ranges_path}")
    print(f"✓ Tercets: {tercet_path}")
    print(f"✓ Lines: {line_path}")
    print(f"✓ Log: {log_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Align Italian lines with Norton\"s English translation "
                    "(paragraph -> range -> tercet -> line, one LLM pipeline)")
    parser.add_argument("cantica", choices=["inferno", "purgatorio", "paradiso"], help="Cantica name")
    parser.add_argument("-c", "--canto", metavar="SPEC", help=dante_corpus.api.CANTO_SPEC_HELP)
    parser.add_argument("-m", "--model", default="ollama:ministral-3:14b", help="LLM model to use")
    parser.add_argument("--temperature", type=float, default=1.0, help="LLM temperature (default: 1.0)")
    parser.add_argument("--think", action="store_true",
                        help="Enable LLM thinking (disabled by default - thinking was observed to make "
                             "the rearranging split worse, see README.md)")
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE,
                        help=f"Number of Italian lines per stage-2 group (default: {DEFAULT_BLOCK_SIZE})")
    parser.add_argument("--test", action="store_true",
                        help="Process only the first paragraph/group at each stage, for a quick local smoke test")

    args = parser.parse_args()

    if err := dante_corpus.api.check_canto_spec([args.cantica], args.canto):
        parser.error(err)

    for canto in dante_corpus.api.select_cantos(args.cantica, args.canto):
        align_canto(args.cantica, canto, args)


if __name__ == '__main__':
    main()
