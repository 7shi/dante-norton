"""
Split each tercet-sized (or smaller) Norton text group, as produced by
align3.py, into one fragment per Italian line.

This is stage 3 (final) of the three-level paragraph -> tercet -> line
pipeline (see ALGORITHM.md). It reuses `split_norton_span`
from align3.py unchanged, passing one-line groups (a special case of the
grouped split align3.py already uses for paragraph -> tercet) - no new split
logic is needed here.

Input: the TSV written by align3.py (-i/--input), one row per group: Italian
line(s) joined by '|', tab, Norton text.

Output: a TSV (-o/--output, companion to the log), one row per Italian line:
the line's Italian text, tab, its Norton fragment. A group whose split never
validates is kept as a single merged row (multiple Italian lines joined by
'|'), same convention as align3.py's paragraph-level merge fallback.
"""

import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from align3 import (
    ItalianLine, FinalRow, load_italian_lines, split_norton_span,
    format_final_rows, write_tsv,
)
from dante_norton import LLMClient

import align3 as _align3_module


def load_group_rows(filepath: str, italian_lines: List[ItalianLine]) -> List[FinalRow]:
    """
    Load align3.py's output TSV, reconstructing each row's ItalianLine group
    by consuming lines, in order, from `italian_lines` - the row's line count
    is exactly the number of '|'-joined Italian texts in its first column, and
    rows are written in canto order covering the whole line range, so a
    simple running pointer is enough (no need to re-match text to line
    numbers).
    """
    rows = []
    pos = 0
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line:
                continue
            italian_field, text = line.split('\t')
            parts = italian_field.split('|')
            group = italian_lines[pos:pos + len(parts)]
            pos += len(parts)
            got = '|'.join(l.full_text for l in group)
            if got != italian_field:
                raise ValueError(
                    f"Line mismatch at position {pos}: expected {italian_field!r}, "
                    f"reconstructed {got!r} - input TSV out of sync with the canto's Italian lines")
            rows.append(FinalRow(group, text))
    return rows


def split_group(llm: LLMClient, row: FinalRow) -> List[FinalRow]:
    """Split one group's Norton text into one fragment per Italian line."""
    if len(row.italian_lines) == 1:
        return [row]

    nums = ', '.join(str(l.line_num) for l in row.italian_lines)
    print()  # stdout only - visually separates groups in the console
    _align3_module.notify(f"Splitting group: line(s) {nums}: {row.text}")
    groups = [[line] for line in row.italian_lines]
    # Number by each line's actual (canto-wide) line number - already a
    # serial number, so no separate running counter is needed here.
    fragments = split_norton_span(llm, groups, row.text, start_num=row.italian_lines[0].line_num)
    if fragments is None:
        return [row]
    return [FinalRow([line], fragment) for line, fragment in zip(row.italian_lines, fragments)]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Split each tercet-sized Norton group into one fragment per Italian line')
    parser.add_argument('canto_num', type=int, help='Canto number (e.g., 1 for Canto I)')
    parser.add_argument('-i', '--input', required=True,
                        help='Group TSV path, as written by align3.py')
    parser.add_argument('-o', '--output', required=True,
                        help='Log file path (required); a companion TSV is written alongside it '
                             '(same name, .tsv extension)')
    parser.add_argument('--model', default='ollama:ministral-3:14b', help='LLM model to use')
    parser.add_argument('--temperature', type=float, default=1.0, help='LLM temperature (default: 1.0)')
    parser.add_argument('--think', action='store_true', help='Enable LLM thinking (disabled by default)')
    parser.add_argument('--test', action='store_true',
                        help='Process only the first group, for a quick local smoke test')

    args = parser.parse_args()

    italian_file = f"tokenize/inferno/{args.canto_num:02d}.txt"
    log_file_path = args.output
    tsv_file_path = str(Path(args.output).with_suffix('.tsv'))

    print(f"Splitting Canto {args.canto_num} groups into per-line fragments...")

    with open(log_file_path, 'w', encoding='utf-8') as log_f:
        _align3_module._log_file = log_f
        log_print = _align3_module.log_print

        log_print(f"=== Canto {args.canto_num} Tercet -> Line Split (align1) ===")
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}, "
                  f"Test: {args.test}")
        log_print()

        italian_lines = load_italian_lines(italian_file)
        for i, line in enumerate(italian_lines, 1):
            line.line_num = i
        group_rows = load_group_rows(args.input, italian_lines)

        if args.test:
            group_rows = group_rows[:1]

        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)

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

        log_print("=" * 80)
        log_print("RESULTS")
        log_print("=" * 80)
        log_print()
        log_print(format_final_rows(rows))
        log_print()
        log_print(f"Total rows: {len(rows)}")
        log_print(f"Groups kept merged (split failed): {merged_groups}")
        log_print(f"Coverage: {covered_lines}/{total_lines} lines")

        write_tsv(rows, tsv_file_path)

    print(f"✓ Complete: {len(rows)} rows")
    print(f"✓ Coverage: {covered_lines}/{total_lines} lines")
    print(f"✓ Merged (unsplit) groups: {merged_groups}")
    print(f"✓ Log: {log_file_path}")
    print(f"✓ TSV: {tsv_file_path}")


if __name__ == '__main__':
    main()
