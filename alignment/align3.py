"""
Align Italian and Norton English translation using a two-stage, tercet-first
LLM process.

This is a variant of align_canto.py built to test the hypothesis (see
MEMO.md "Comparison against the fixed gold reference") that most of the
block-boundary mismatch against the hand-built gold reference (01-1.txt)
comes from the extraction-only algorithm having no notion of a sentence unit
larger than one Italian line. The Bard experiments that produced 01-1.txt
(see PRIOR_WORK.md, and 01-3.txt for the intermediate 3-line version) used a
two-stage process instead: first segment Norton's prose at tercet (3-line)
granularity, matching complete sentences/clauses, then only within that
fixed span rearrange words to match each individual Italian line - never
across a tercet boundary.

Stage 1 (coarse): unlike align_canto.py, the block is a FIXED chunk of
`--block-size` (default 3) Italian lines - normally a whole tercet - with no
growth mechanism: align_canto.py's island/window extension existed only to
recover when a single line was too little context, and a 3-line chunk is
assumed to already give enough context that extension is not needed. A
chunk's span must start exactly at the beginning of the remaining text
(strict prefix, no window tolerance); if no attempt (within extract_norton_span's
own per-query retries) produces such a span, the chunk is skipped - it is
never grown, only abandoned - and the same-size next chunk is tried against
the same remaining text.

Stage 2 (decomposition): for a coarse block spanning more than one Italian
line, a second LLM call is asked to split the now-fixed Norton span into
one fragment per Italian line, using only the words already extracted
(rearranged as needed, no substitution - mirroring the Bard prompt: "you may
rearrange the words in B as needed, but do not replace them with different
words"). The split is validated mechanically: the concatenation of the
fragments must use exactly the same words (case-insensitive multiset) as the
input span. If no attempt validates, the block is kept merged (one row
spanning all its Italian lines, matching the older window-mode TSV
convention) rather than silently dropping text.

Unlike align_canto.py, the log path is not derived from the canto number:
it must be given explicitly with -o/--output. Two companion TSVs are written
alongside it: <output>.stage1.tsv (one row per stage-1/coarse block, before
decomposition) and <output>.tsv (the final per-line rows, one per Italian
line, or one row per still-merged block on a stage-2 failure).
"""

import re
import sys
import json
from pathlib import Path
from typing import List, NamedTuple, Tuple
from pydantic import BaseModel, Field

# Add parent directory to path to import dante_norton
sys.path.insert(0, str(Path(__file__).parent.parent))

from dante_norton import Canto, LLMClient


# Maximum word-count ratio of extracted English to source Italian
MAX_LENGTH_RATIO = 2.0

# Extraction attempts per query before giving up and asking for more context
MAX_ATTEMPTS = 3

# Default number of Italian lines per stage-1 (coarse) block
DEFAULT_BLOCK_SIZE = 3


# Structured output for stage-1 extraction
class LineResult(BaseModel):
    """Extract line."""
    italian: str
    english: str


# Structured output for stage-2 decomposition
class SplitResult(BaseModel):
    """One Norton fragment per Italian line, in order."""
    lines: List[str] = Field(description="One fragment per Italian line, same count and order")


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


class AlignmentBlock:
    """Represents a stage-1 (coarse) block of Italian lines aligned to a Norton span"""

    def __init__(self, italian_lines: List[ItalianLine], english_text: str, matched_text: str = ""):
        self.italian_lines = italian_lines
        self.english_text = english_text  # Original paragraph text
        self.matched_text = matched_text  # Only the matched portion

    def __repr__(self):
        return f"AlignmentBlock({len(self.italian_lines)} lines)"


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


class Extraction(NamedTuple):
    """An accepted Norton span within the remaining paragraph text."""
    text: str           # the span, verbatim from Norton (punctuation restored)
    offset_words: int   # words preceding the span; 0 means it starts the text
    offset_chars: int   # character offset of the span
    end_pos: int        # character offset just past the span


def count_words(text: str) -> int:
    """
    Count word tokens, ignoring punctuation-only fragments.

    Used for the length-ratio check and for measuring how far into the
    remaining Norton text a span starts. Punctuation must not count as a word,
    otherwise a span preceded only by a stray comma would look displaced.
    """
    return len(re.findall(r"\w+", text, re.UNICODE))


def word_multiset(text: str) -> List[str]:
    """Lowercased word tokens, for order-insensitive content comparison."""
    return sorted(re.findall(r"\w+", text.lower(), re.UNICODE))


def extract_norton_span(llm: LLMClient, italian_block: List[ItalianLine],
                        norton_text: str, skip_translation: bool = False) -> Extraction | None:
    """
    Ask the LLM for the Norton span corresponding to the current (fixed-size)
    Italian block.

    Stage 1 of the two-stage process: the block is normally a whole tercet
    (see DEFAULT_BLOCK_SIZE), so this asks for a coarse, sentence-scale span
    rather than a single line's worth of text. There is no block-growth
    mechanism (see module docstring), so a span must start exactly at the
    beginning of `norton_text` (strict prefix) to be accepted - there is no
    recovery path for a match found further in.

    In --translate mode the Italian is first rendered into simple modern
    English, which is then used as the meaning reference; otherwise the Italian
    text is the reference directly.

    The answer is validated mechanically: it must be non-empty, appear verbatim
    at the start of `norton_text` (anti-hallucination plus strict-prefix), and
    stay within MAX_LENGTH_RATIO of the Italian word count.

    Args:
        skip_translation: If True, use the Italian text directly as reference

    Returns:
        An Extraction, or None if no attempt produced a valid span
    """
    # Join Italian lines with space instead of newline to avoid LLM adding newlines to English
    italian_text = ' '.join(line.full_text for line in italian_block)
    num_italian_lines = len(italian_block)

    if skip_translation:
        # Use Italian text directly as reference
        reference_text = italian_text
        log_print(f"    Using Italian directly: {reference_text}")
    else:
        # Stage 0: Translate Italian to modern English
        # For single lines, translate just that line
        # For multiple lines (enjambment/tercet), translate all lines together
        if num_italian_lines == 1:
            text_to_translate = italian_block[0].full_text
        else:
            text_to_translate = italian_text

        translate_prompt = f"""Translate the following Italian text to simple, modern English.
Maintain the exact meaning but use clear, straightforward language.

Italian:
{text_to_translate}

Output only the translation, nothing else."""

        llm.history = []
        translation_response = llm.call(translate_prompt)
        reference_text = translation_response.strip()
        log_print(f"    Modern translation: {reference_text}")

    # Stage: Find matching text in Norton English
    position_rule = "- Start from the very beginning of the Norton text"

    for attempt in range(MAX_ATTEMPTS):
        # Extract Norton text corresponding to Italian line(s)
        llm.history = []

        extraction_prompt = f"""Extract the Norton English text corresponding to the Italian line(s).

Italian line(s): {italian_text}
Meaning: {reference_text}

Norton English text (extract FROM this):
{norton_text[:500]}

INSTRUCTIONS:
- Find the Norton text that matches the Italian line(s)'s meaning: "{reference_text}"
{position_rule}
- Extract ONLY the portion corresponding to these Italian line(s)
- Copy EXACTLY from Norton (verbatim, no paraphrasing)
- Do NOT include content from other Italian lines

Output in JSON format:
- italian: the Italian line(s) text (copy exactly: {italian_text})
- english: the corresponding Norton English text (verbatim extraction)"""

        try:
            response = llm.call(extraction_prompt, schema=LineResult)
        except Exception as e:
            notify(f"    ✗ LLM call failed: {e}")
            continue

        try:
            result = parse_json_object(response)
            extracted = result.get("english", "").strip()
        except Exception as e:
            log_print(f"    ✗ Failed to parse structured output: {e}")
            continue

        # Strip quotes only if text is fully enclosed in matching quotes
        if (extracted.startswith('"') and extracted.endswith('"')) or \
           (extracted.startswith("'") and extracted.endswith("'")):
            extracted = extracted[1:-1]

        # Reject empty extractions: an empty string would otherwise pass the
        # length-ratio check (0 / N = 0.0) and be found at offset 0 by str.find,
        # so it would be wrongly accepted below.
        if not extracted.strip():
            log_print(f"    ✗ Empty extraction")
            continue

        # Check for hallucination: extracted text must exist in Norton text
        # Strip leading/trailing quotes and whitespace for matching
        extracted_for_search = extracted.strip().strip('"\'“”‘’').strip()
        idx = norton_text.lower().find(extracted_for_search.lower())
        if idx == -1:
            log_print(f"    Extracted: {extracted[:100]}{'...' if len(extracted) > 100 else ''}")
            log_print(f"    ✗ Hallucination: text not found in Norton")
            continue
        # Use the cleaned version for further processing
        extracted = extracted_for_search

        # Restore punctuation from original Norton text if missing
        end_pos = idx + len(extracted)
        if end_pos < len(norton_text):
            next_char = norton_text[end_pos]
            if next_char in ',.;:!?' and not extracted.endswith(next_char):
                # Add the punctuation if missing
                extracted = extracted + next_char
                end_pos += 1

        log_print(f"    Extracted: {extracted[:100]}{'...' if len(extracted) > 100 else ''}")

        # Length ratio check (hard constraint)
        italian_word_count = count_words(italian_text)
        extracted_word_count = count_words(extracted)
        ratio = extracted_word_count / italian_word_count if italian_word_count > 0 else 0

        if ratio > MAX_LENGTH_RATIO:
            log_print(f"    ✗ Length ratio {ratio:.2f} exceeds {MAX_LENGTH_RATIO} "
                      f"(IT:{italian_word_count} EN:{extracted_word_count})")
            continue

        # Position check: no block-growth mechanism exists to recover a match
        # found further in (see module docstring), so it must start exactly
        # at the beginning of the remaining text.
        offset_words = count_words(norton_text[:idx])
        if offset_words > 0:
            log_print(f"    ✗ Not at beginning ({offset_words} word(s) precede the match)")
            continue

        log_print(f"    ✓ Accepted (ratio: {ratio:.2f})")
        return Extraction(extracted, offset_words, idx, end_pos)

    # No attempt produced a valid span - the chunk is skipped, not grown
    notify(f"    ✗ Failed after {MAX_ATTEMPTS} attempts")
    return None


def split_norton_span(llm: LLMClient, italian_block: List[ItalianLine],
                      matched_text: str) -> List[str] | None:
    """
    Stage 2: split a fixed, already-matched Norton span into one fragment
    per Italian line in the block, rearranging words as needed but never
    substituting them (mirrors the Bard prompt in PRIOR_WORK.md: "you may
    rearrange the words in B as needed, but do not replace them with
    different words").

    Validated mechanically: the fragment count must match the Italian line
    count, and the case-insensitive word multiset of the concatenated
    fragments must equal that of `matched_text` exactly (nothing added,
    dropped, or substituted - only reordered and re-split).

    Returns:
        A list of fragments (one per italian_block line, in order), or None
        if no attempt validated.
    """
    n = len(italian_block)
    italian_numbered = '\n'.join(f"{i+1}. {line.full_text}" for i, line in enumerate(italian_block))
    target_words = word_multiset(matched_text)

    split_prompt = f"""The following English text is a single unit that corresponds to {n} Italian lines
listed below. Split the English text into exactly {n} fragments, one per Italian line, in order.

You may REARRANGE the words as needed so each fragment matches its Italian line's content,
but you must NOT replace, add, or remove any word. Every word in the English text must be
used exactly once across the fragments, in some fragment.

Italian lines:
{italian_numbered}

English text (split and reorder this, do not change wording):
{matched_text}

Output in JSON format:
- lines: a list of exactly {n} strings, one fragment per Italian line above, in the same order"""

    for attempt in range(MAX_ATTEMPTS):
        llm.history = []
        try:
            response = llm.call(split_prompt, schema=SplitResult)
        except Exception as e:
            notify(f"    ✗ Split LLM call failed: {e}")
            continue

        try:
            result = parse_json_object(response)
            fragments = result.get("lines", [])
        except Exception as e:
            log_print(f"    ✗ Failed to parse split output: {e}")
            continue

        if len(fragments) != n:
            log_print(f"    ✗ Split returned {len(fragments)} fragment(s), expected {n}")
            continue

        # Reject any empty (or whitespace-only) fragment: it would otherwise
        # contribute zero words to the multiset check below and pass silently,
        # producing a fragment with no content for that Italian line (same
        # class of bug as the empty-extraction bug fixed in align_canto.py).
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

    notify(f"    ✗ Split failed after {MAX_ATTEMPTS} attempts - keeping block merged")
    return None


def align_paragraph(llm: LLMClient, italian_lines: List[ItalianLine],
                    norton_paragraph: str, start_idx: int,
                    skip_translation: bool = False,
                    block_size: int = DEFAULT_BLOCK_SIZE) -> Tuple[List[AlignmentBlock], int, str, bool]:
    """
    Align a fixed-size chunk of Italian lines (normally a whole tercet) to
    the current position in a Norton paragraph.

    No growth: the chunk is exactly `block_size` Italian lines (fewer only
    if the paragraph's Italian lines run out). If extraction fails (see
    extract_norton_span), the chunk is skipped outright - it is never
    extended with more lines - and the caller retries with the next chunk
    against the same, still-unconsumed paragraph text.

    Args:
        llm: LLM client for extraction queries
        italian_lines: Full list of Italian lines
        norton_paragraph: Remaining Norton paragraph text
        start_idx: Starting index in italian_lines
        skip_translation: If True, use Italian directly instead of translating to English
        block_size: Number of Italian lines in the chunk

    Returns:
        Tuple of (List[AlignmentBlock], next_start_idx, remaining_paragraph_text, skipped)
        skipped is True if the chunk failed to align; the unconsumed
        norton_paragraph text is preserved (not discarded) so the caller can
        retry with the next chunk against it.
    """
    if start_idx >= len(italian_lines):
        return [], start_idx, norton_paragraph, False

    n = min(block_size, len(italian_lines) - start_idx)
    italian_block = italian_lines[start_idx:start_idx + n]
    idx = start_idx + n

    print()
    nums = ', '.join(str(l.line_num) for l in italian_block)
    notify(f"  Line(s) {nums}/{len(italian_lines)}: "
          f"{' '.join(l.full_text for l in italian_block)}")

    # Query LLM for the corresponding Norton span
    result = extract_norton_span(llm, italian_block, norton_paragraph, skip_translation)

    if result is None:
        # Chunk failed to align: preserve norton_paragraph untouched so the
        # next chunk can still be matched against it
        return [], idx, norton_paragraph, True

    matched_text = result.text
    notify(f"    ✓ Complete: {matched_text}")

    remaining_text = norton_paragraph[result.end_pos:].lstrip(" ,;.!?")

    block = AlignmentBlock(italian_block, norton_paragraph, matched_text)
    return [block], idx, remaining_text, False


def align_canto(llm: LLMClient, italian_filepath: str, norton_filepath: str,
                max_lines: int | None = None, log_file = None,
                skip_translation: bool = False,
                block_size: int = DEFAULT_BLOCK_SIZE) -> Tuple[List[AlignmentBlock], int]:
    """
    Align a full canto (Italian and Norton translation), stage 1 only
    (coarse, tercet-first blocks). Stage 2 (per-line decomposition) is run
    afterward in main() over the returned blocks.

    Args:
        llm: LLM client for word correspondence queries
        italian_filepath: Path to tokenize/inferno/*.txt file
        norton_filepath: Path to en-norton/inferno/*.txt file
        max_lines: Maximum number of Italian lines to process (for testing)
        skip_translation: If True, use Italian directly instead of translating
        block_size: Number of Italian lines in each fixed-size stage-1 chunk

    Returns:
        Tuple of (List[AlignmentBlocks], total Italian line count)
    """
    # Load Italian lines
    italian_lines = load_italian_lines(italian_filepath)
    if max_lines is not None:
        italian_lines = italian_lines[:max_lines]

    # Load Norton text
    with open(norton_filepath, 'r', encoding='utf-8') as f:
        norton_text = f.read()
    norton_canto = Canto(norton_text)

    log_print(f"Processing {len(italian_lines)} Italian lines")
    log_print()

    # Process each Norton paragraph
    blocks = []
    italian_idx = 0

    for para_num, (norton_paragraph, _) in enumerate(norton_canto.lines, 1):
        # Skip empty paragraphs
        if not norton_paragraph.strip():
            continue

        # Skip first paragraph (summary, not translation)
        if para_num == 1:
            continue

        notify(f"Paragraph {para_num} (Italian line {italian_idx + 1}/{len(italian_lines)}): {norton_paragraph[:60]}...")

        # Remove annotation markers
        clean_paragraph = re.sub(r'\[\d+\]', '', norton_paragraph)

        # Process multiple blocks within this paragraph
        remaining_text = clean_paragraph
        block_num = 1

        # Minimum remaining text length to continue processing
        MIN_REMAINING_LENGTH = 10

        while remaining_text.strip() and len(remaining_text.strip()) >= MIN_REMAINING_LENGTH and italian_idx < len(italian_lines):
            # Align this block (may return multiple blocks if split)
            new_blocks, italian_idx, remaining_text, skipped = align_paragraph(
                llm, italian_lines, remaining_text, italian_idx, skip_translation,
                block_size
            )

            if skipped:
                # These Italian line(s) failed to align; remaining_text is
                # unchanged, so retry with the next line(s) against it
                log_print()
                continue

            blocks.extend(new_blocks)
            block_num += len(new_blocks)
            log_print()

            if italian_idx >= len(italian_lines):
                break

        if italian_idx >= len(italian_lines):
            break

    return blocks, len(italian_lines)


class FinalRow(NamedTuple):
    """One row of final, per-line (or still-merged) output."""
    italian_lines: List[ItalianLine]  # 1 line normally; >1 if stage 2 could not split
    text: str


def decompose_blocks(llm: LLMClient, blocks: List[AlignmentBlock]) -> List[FinalRow]:
    """
    Stage 2: decompose each multi-line stage-1 block into one row per
    Italian line via split_norton_span(). Single-line blocks pass through
    unchanged. A block whose split never validates is kept merged (one row
    spanning all its Italian lines) rather than dropping text.
    """
    rows = []
    for block in blocks:
        if len(block.italian_lines) == 1:
            rows.append(FinalRow(block.italian_lines, block.matched_text))
            continue

        notify(f"Splitting block: lines {block.italian_lines[0].line_num}-"
              f"{block.italian_lines[-1].line_num}: {block.matched_text}")
        fragments = split_norton_span(llm, block.italian_lines, block.matched_text)
        if fragments is None:
            rows.append(FinalRow(block.italian_lines, block.matched_text))
        else:
            for line, fragment in zip(block.italian_lines, fragments):
                rows.append(FinalRow([line], fragment))
    return rows


def format_aligned_output(blocks: List[AlignmentBlock]) -> str:
    """
    Format stage-1 alignment blocks as bilingual output with line breaks at
    block boundaries.
    """
    lines = []

    for block_num, block in enumerate(blocks, 1):
        # Block header
        lines.append(f"=== Block {block_num} ===")
        lines.append("")

        # Italian lines
        lines.append("[Italian]")
        for italian_line in block.italian_lines:
            lines.append(italian_line.full_text)
        lines.append("")

        # English text (this is the portion of Norton translation for this block)
        lines.append("[English (Norton)]")
        matched = block.matched_text if block.matched_text else block.english_text
        lines.append(matched[:200] + "..." if len(matched) > 200 else matched)
        lines.append("")

        # Blank line between blocks
        lines.append("")

    return '\n'.join(lines)


def format_final_rows(rows: List[FinalRow]) -> str:
    """Format the final per-line output as bilingual rows."""
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
    Write one TSV row per FinalRow: Italian line(s) joined by '|' if the row
    is still a merged (un-split) block, tab, Norton fragment. Matches the
    convention used by the window-mode TSVs from align_canto.py runs.
    """
    with open(tsv_path, 'w', encoding='utf-8') as f:
        for row in rows:
            italian = '|'.join(l.full_text for l in row.italian_lines)
            f.write(f"{italian}\t{row.text}\n")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Align Italian and Norton English translation (two-stage, tercet-first)')
    parser.add_argument('canto_num', type=int, help='Canto number (e.g., 1 for Canto I)')
    parser.add_argument('-o', '--output', required=True,
                        help='Log file path (required); companion TSVs are written at '
                             '<output>.stage1.tsv (coarse) and <output>.tsv (final per-line)')
    parser.add_argument('--model', default='ollama:ministral-3:14b', help='LLM model to use (default: ollama:ministral-3:14b)')
    parser.add_argument('--max-lines', type=int, default=None, help='Max Italian lines to process (default: unlimited)')
    parser.add_argument('--temperature', type=float, default=1.0, help='LLM temperature (default: 1.0)')
    parser.add_argument('--think', action='store_true', help='Enable LLM thinking (disabled by default)')
    parser.add_argument('--translate', action='store_true', help='Translate Italian to English before matching (default: use Italian directly)')
    parser.add_argument('--block-size', type=int, default=DEFAULT_BLOCK_SIZE,
                        help=f'Number of Italian lines in each fixed-size stage-1 (coarse) chunk (default: {DEFAULT_BLOCK_SIZE})')

    args = parser.parse_args()

    # Format paths
    italian_file = f"tokenize/inferno/{args.canto_num:02d}.txt"
    norton_file = f"en-norton/inferno/{args.canto_num:02d}.txt"
    log_file_path = args.output
    tsv_file_path = args.output + ".tsv"
    stage1_tsv_file_path = args.output + ".stage1.tsv"

    print(f"Aligning Canto {args.canto_num} (two-stage, block size {args.block_size})...")

    # Open log file
    global _log_file
    with open(log_file_path, 'w', encoding='utf-8') as log_f:
        _log_file = log_f

        log_print(f"=== Canto {args.canto_num} Alignment (align3, two-stage) ===")
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}, "
                  f"Translate: {args.translate}, Block size: {args.block_size} (fixed, no growth)")
        log_print()

        # Create LLM client
        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)

        # Stage 1: coarse (tercet-first) alignment
        blocks, total_lines = align_canto(llm, italian_file, norton_file, max_lines=args.max_lines,
                                          skip_translation=not args.translate,
                                          block_size=args.block_size)

        # Coverage: Italian lines that ended up in an output block. A chunk
        # that failed to align is skipped with no output, so italian_idx
        # reaching the end does not imply every line was covered.
        covered_lines = sum(len(block.italian_lines) for block in blocks)
        coverage_pct = 100.0 * covered_lines / total_lines if total_lines else 0.0

        log_print()
        log_print("=" * 80)
        log_print("STAGE 1 RESULTS (coarse)")
        log_print("=" * 80)
        log_print()
        log_print("--- Detailed (Italian + English) ---")
        log_print(format_aligned_output(blocks))
        log_print()
        log_print(f"Total stage-1 blocks: {len(blocks)}")
        log_print(f"Coverage: {covered_lines}/{total_lines} lines ({coverage_pct:.0f}%)")
        log_print()

        write_tsv([FinalRow(b.italian_lines, b.matched_text) for b in blocks], stage1_tsv_file_path)

        # Stage 2: decompose multi-line blocks into per-line rows
        log_print("=" * 80)
        log_print("STAGE 2 (decomposition)")
        log_print("=" * 80)
        log_print()
        rows = decompose_blocks(llm, blocks)

        merged_rows = sum(1 for r in rows if len(r.italian_lines) > 1)
        split_lines = sum(len(r.italian_lines) for r in rows if len(r.italian_lines) > 1)

        log_print()
        log_print("=" * 80)
        log_print("FINAL RESULTS (per-line)")
        log_print("=" * 80)
        log_print()
        log_print("--- Detailed (Italian + English) ---")
        log_print(format_final_rows(rows))
        log_print()

        log_print("--- Norton English (with line breaks) ---")
        log_print('\n\n'.join(row.text for row in rows))
        log_print()

        log_print(f"Total final rows: {len(rows)}")
        log_print(f"Rows still merged after stage 2 (split failed): {merged_rows} "
                  f"({split_lines} Italian lines)")
        log_print(f"Coverage: {covered_lines}/{total_lines} lines ({coverage_pct:.0f}%)")

        write_tsv(rows, tsv_file_path)

    print(f"✓ Complete: {len(blocks)} stage-1 blocks, {len(rows)} final rows")
    print(f"✓ Coverage: {covered_lines}/{total_lines} lines ({coverage_pct:.0f}%)")
    print(f"✓ Merged (unsplit) rows: {merged_rows}")
    print(f"✓ Log: {log_file_path}")
    print(f"✓ TSV: {tsv_file_path}")
    print(f"✓ Stage-1 TSV: {stage1_tsv_file_path}")


if __name__ == '__main__':
    main()
