"""
Identify, for each Norton paragraph, the range of Italian lines it
corresponds to - in a single LLM call over the whole canto.

This is a coarser, whole-canto alternative to align3.py's tercet-first
extraction: instead of extracting Norton *text* per Italian chunk, it asks
only for a *line-range correspondence* (which Italian lines a given Norton
paragraph covers), using the two full, independently-numbered listings
(Italian lines 1..N; Norton paragraphs, numbered the same way align3.py
enumerates them, skipping the summary paragraph) as the entire prompt
context. No text extraction, no verbatim-match validation - only the
mechanical checks that the returned ranges are contiguous, in order, and
cover the whole canto (see `validate_ranges`).

Rationale (see MEMO.md "Two-stage, tercet-first alignment" and
alignment/HANDOFF.md): before reaching for segments/*.jsonl (external,
scene-boundary line ranges) as an intermediate granularity between tercets
and the whole canto, try asking the LLM to do this whole-canto comparison
directly. If that holds up, the imported segments are unnecessary; if the
canto is too large for reliable output, segments become the fallback
chunking mechanism for the same range-identification task.
"""

import sys
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))

from dante_norton import LLMClient
from align3 import ItalianLine, load_italian_lines, load_norton_paragraphs, parse_json_object


# Retries for a range mapping that fails mechanical validation
MAX_ATTEMPTS = 3


class ParagraphRange(BaseModel):
    """One Norton paragraph's corresponding Italian line range."""
    paragraph: int
    start_line: int
    end_line: int


class RangeMapping(BaseModel):
    """One entry per Norton paragraph, in paragraph order."""
    ranges: List[ParagraphRange] = Field(description="One entry per Norton paragraph, in order")


# Global log file handle
_log_file = None


def log_print(*args, **kwargs):
    if _log_file:
        print(*args, **kwargs, file=_log_file)
        _log_file.flush()


def notify(*args, **kwargs):
    print(*args, **kwargs, flush=True)
    log_print(*args, **kwargs)


def validate_ranges(ranges: List[ParagraphRange], paragraphs: List[tuple[int, str]],
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
                    paragraphs: List[tuple[int, str]]) -> List[ParagraphRange] | None:
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


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Identify Italian line ranges per Norton paragraph (whole-canto, single LLM call)')
    parser.add_argument('canto_num', type=int, help='Canto number (e.g., 1 for Canto I)')
    parser.add_argument('-o', '--output', required=True,
                        help='Log file path (required); a companion TSV is written alongside it '
                             '(same name, .tsv extension)')
    parser.add_argument('--model', default='ollama:ministral-3:14b', help='LLM model to use')
    parser.add_argument('--temperature', type=float, default=1.0, help='LLM temperature (default: 1.0)')
    parser.add_argument('--think', action='store_true', help='Enable LLM thinking (disabled by default)')

    args = parser.parse_args()

    italian_file = f"tokenize/inferno/{args.canto_num:02d}.txt"
    norton_file = f"en-norton/inferno/{args.canto_num:02d}.txt"
    log_file_path = args.output
    tsv_file_path = str(Path(args.output).with_suffix('.tsv'))

    print(f"Identifying paragraph ranges for Canto {args.canto_num} (whole-canto, single call)...")

    global _log_file
    with open(log_file_path, 'w', encoding='utf-8') as log_f:
        _log_file = log_f

        log_print(f"=== Canto {args.canto_num} Range Identification (align_ranges) ===")
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}")
        log_print()

        italian_lines = load_italian_lines(italian_file)
        for i, line in enumerate(italian_lines, 1):
            line.line_num = i
        paragraphs = load_norton_paragraphs(norton_file)

        log_print(f"{len(italian_lines)} Italian lines, {len(paragraphs)} Norton paragraphs "
                  f"(paragraph numbers: {[p[0] for p in paragraphs]})")
        log_print()

        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)
        ranges = identify_ranges(llm, italian_lines, paragraphs)

        if ranges is None:
            print("✗ Failed to identify a valid range mapping")
            log_print("FAILED: no valid range mapping produced")
            return

        log_print()
        log_print("=" * 80)
        log_print("RESULTS")
        log_print("=" * 80)
        for r in ranges:
            span = ' '.join(l.full_text for l in italian_lines[r.start_line - 1:r.end_line])
            log_print(f"Paragraph {r.paragraph}: lines {r.start_line}-{r.end_line}")
            log_print(f"  {span}")
            log_print()

        with open(tsv_file_path, 'w', encoding='utf-8') as f:
            for r in ranges:
                f.write(f"{r.paragraph}\t{r.start_line}\t{r.end_line}\n")

    print(f"✓ Complete: {len(ranges)} paragraph ranges")
    print(f"✓ Log: {log_file_path}")
    print(f"✓ TSV: {tsv_file_path}")


if __name__ == '__main__':
    main()
