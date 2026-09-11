"""
Split each Norton paragraph into tercet-sized (or smaller trailing) chunks of
Norton text, one per fixed-size group of Italian lines, using the paragraph's
Italian line range already identified by align_ranges.py.

This is stage 2 of the three-level paragraph -> tercet -> line pipeline (see
ALGORITHM.md). Unlike the earlier extraction-based
align3.py, this never extracts or verbatim-matches Norton text: the
paragraph's line range is already known (from align_ranges.py's output), so
this only needs to REARRANGE the paragraph's own words into `block_size`-line
groups, validated by word-multiset equality with the source paragraph text
(mirrors the Bard prompt in PRIOR_WORK.md: "you may rearrange the words in B
as needed, but do not replace them with different words").

Input: the TSV written by align_ranges.py (-i/--input), one row per Norton
paragraph: paragraph number, start_line, end_line (tab-separated).

Output: a TSV (-o/--output, companion to the log), one row per tercet-sized
group: the group's Italian line(s) joined by '|', tab, the corresponding
Norton text. A paragraph whose split never validates is kept as a single
merged row spanning its whole line range, rather than dropping text - same
convention as the old align3.py's stage-2 merge fallback.

`split_norton_span` here is deliberately generic (groups of Italian lines,
not just single lines) so that align1.py's tercet -> line split can reuse it
unchanged, passing one-line groups.
"""

import re
import sys
import json
from pathlib import Path
from typing import List, NamedTuple, Tuple

# Add parent directory to path to import dante_norton
sys.path.insert(0, str(Path(__file__).parent.parent))

from dante_norton import Canto, LLMClient


# Split attempts per paragraph before giving up and keeping it merged
MAX_ATTEMPTS = 3

# Default number of Italian lines per group (normally a whole tercet)
DEFAULT_BLOCK_SIZE = 3


def parse_json_object(text: str) -> dict:
    """
    Parse a JSON object out of an LLM response, tolerating surrounding
    Markdown code fences (opening and/or closing, or neither) and any
    other trailing text the model may add.
    """
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)  # strip leading fence, if any
    return json.JSONDecoder().raw_decode(text)[0]  # ignore trailing garbage


# Global log file handle
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


class ItalianLine:
    """Represents a single line from tokenize/inferno/*.txt"""

    def __init__(self, line_text: str):
        parts = line_text.split('|')
        self.full_text = parts[0]
        self.tokens = parts[1:] if len(parts) > 1 else []
        self.line_num = 0  # Will be set later

    def __repr__(self):
        return f"ItalianLine({self.full_text!r}, tokens={len(self.tokens)})"


def load_italian_lines(filepath: str) -> List[ItalianLine]:
    """Load Italian lines from tokenize/inferno/*.txt file"""
    lines = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if line:
                italian_line = ItalianLine(line)
                italian_line.line_num = line_num
                lines.append(italian_line)
    return lines


def load_norton_paragraphs(filepath: str) -> List[Tuple[int, str]]:
    """
    Load Norton paragraphs as (paragraph_num, text) pairs, numbering them the
    same way (and skipping the summary paragraph the same way) as
    align_ranges.py's identify_ranges() and the old align3.py's align_canto()
    did, so paragraph numbers line up with a ranges TSV produced by
    align_ranges.py.
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


class ParagraphRange(NamedTuple):
    """One row of align_ranges.py's output TSV."""
    paragraph: int
    start_line: int
    end_line: int


def load_ranges(filepath: str) -> List[ParagraphRange]:
    """Load a ranges TSV as written by align_ranges.py."""
    ranges = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line:
                continue
            paragraph, start_line, end_line = line.split('\t')
            ranges.append(ParagraphRange(int(paragraph), int(start_line), int(end_line)))
    return ranges


def chunk_lines(lines: List[ItalianLine], block_size: int) -> List[List[ItalianLine]]:
    """
    Split a flat list of Italian lines into consecutive groups of
    `block_size` lines. The final group may be shorter when the line count
    is not a multiple of `block_size` (a paragraph's line range is not
    guaranteed to be, in general).
    """
    return [lines[i:i + block_size] for i in range(0, len(lines), block_size)]


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

    Generic over the group size: a group may be a single Italian line (used
    by align1.py for the tercet -> line split) or several (used here, in
    align3.py, for the paragraph -> tercet split) - the prompt just shows
    each group's lines joined together as one numbered item.

    Output is plain numbered-line text (not structured JSON output), with
    the Italian side renumbered by group starting at `start_num` (a serial
    number - callers thread the running group/line count through so it
    doesn't reset to 1 for every paragraph or tercet, making cross-call log
    output traceable) rather than by original per-paragraph line number, and
    the requested English output numbered to match - mirroring the numbering
    1:1 makes the correspondence explicit to the model, which matters most
    for cases like an Italian hyperbaton spanning a group boundary, where
    achieving the requested grouping requires actually moving a phrase
    rather than just picking a cut point.

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
    italian_lines: List[ItalianLine]  # `block_size` lines normally; >1 or fewer at paragraph edges/failure
    text: str


def split_paragraph(llm: LLMClient, italian_lines: List[ItalianLine], para_num: int,
                    norton_text: str, block_size: int, start_num: int) -> List[FinalRow]:
    """
    Split one Norton paragraph's text into tercet-sized (or smaller trailing)
    groups matching its already-known Italian line range. Falls back to a
    single merged row (the whole range) if the split never validates.

    `start_num` is this paragraph's first group's serial number in the
    canto-wide group numbering (see split_norton_span) - the caller advances
    it by this paragraph's group count for the next call.
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


def format_final_rows(rows: List[FinalRow]) -> str:
    """Format the final output as bilingual rows."""
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


def write_tsv(rows: List[FinalRow], tsv_path: str):
    """
    Write one TSV row per FinalRow: Italian line(s) joined by '|' (a single
    line if the group wasn't split), tab, Norton fragment.
    """
    with open(tsv_path, 'w', encoding='utf-8') as f:
        for row in rows:
            italian = '|'.join(l.full_text for l in row.italian_lines)
            f.write(f"{italian}\t{row.text}\n")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Split each Norton paragraph into tercet-sized groups (paragraph -> tercet)')
    parser.add_argument('canto_num', type=int, help='Canto number (e.g., 1 for Canto I)')
    parser.add_argument('-i', '--input', required=True,
                        help='Ranges TSV path, as written by align_ranges.py')
    parser.add_argument('-o', '--output', required=True,
                        help='Log file path (required); a companion TSV is written alongside it '
                             '(same name, .tsv extension)')
    parser.add_argument('--model', default='ollama:ministral-3:14b', help='LLM model to use')
    parser.add_argument('--temperature', type=float, default=1.0, help='LLM temperature (default: 1.0)')
    parser.add_argument('--think', action='store_true', help='Enable LLM thinking (disabled by default)')
    parser.add_argument('--block-size', type=int, default=DEFAULT_BLOCK_SIZE,
                        help=f'Number of Italian lines per group (default: {DEFAULT_BLOCK_SIZE})')
    parser.add_argument('--test', action='store_true',
                        help='Process only the first paragraph, for a quick local smoke test')

    args = parser.parse_args()

    italian_file = f"tokenize/inferno/{args.canto_num:02d}.txt"
    norton_file = f"en-norton/inferno/{args.canto_num:02d}.txt"
    log_file_path = args.output
    tsv_file_path = str(Path(args.output).with_suffix('.tsv'))

    print(f"Splitting Canto {args.canto_num} paragraphs into groups of {args.block_size} line(s)...")

    global _log_file
    with open(log_file_path, 'w', encoding='utf-8') as log_f:
        _log_file = log_f

        log_print(f"=== Canto {args.canto_num} Paragraph -> Tercet Split (align3) ===")
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}, "
                  f"Block size: {args.block_size}, Test: {args.test}")
        log_print()

        italian_lines = load_italian_lines(italian_file)
        for i, line in enumerate(italian_lines, 1):
            line.line_num = i
        ranges = load_ranges(args.input)
        paragraphs = dict(load_norton_paragraphs(norton_file))

        if args.test:
            ranges = ranges[:1]

        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)

        rows: List[FinalRow] = []
        merged_paragraphs = 0
        next_num = 1  # serial group number, canto-wide (never resets per paragraph)
        for r in ranges:
            group_lines = italian_lines[r.start_line - 1:r.end_line]
            para_rows = split_paragraph(llm, group_lines, r.paragraph, paragraphs[r.paragraph],
                                        args.block_size, next_num)
            if len(para_rows) == 1 and len(para_rows[0].italian_lines) > 1 and len(group_lines) > args.block_size:
                merged_paragraphs += 1
            rows.extend(para_rows)
            next_num += len(chunk_lines(group_lines, args.block_size))
            log_print()

        covered_lines = sum(len(row.italian_lines) for row in rows)
        total_lines = sum(r.end_line - r.start_line + 1 for r in ranges)

        log_print("=" * 80)
        log_print("RESULTS")
        log_print("=" * 80)
        log_print()
        log_print(format_final_rows(rows))
        log_print()
        log_print(f"Total groups: {len(rows)}")
        log_print(f"Paragraphs kept merged (split failed): {merged_paragraphs}")
        log_print(f"Coverage: {covered_lines}/{total_lines} lines")

        write_tsv(rows, tsv_file_path)

    print(f"✓ Complete: {len(rows)} groups")
    print(f"✓ Coverage: {covered_lines}/{total_lines} lines")
    print(f"✓ Merged (unsplit) paragraphs: {merged_paragraphs}")
    print(f"✓ Log: {log_file_path}")
    print(f"✓ TSV: {tsv_file_path}")


if __name__ == '__main__':
    main()
