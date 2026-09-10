"""
Align Italian and Norton English translation using LLM-based span extraction.

For each Norton paragraph, Italian lines are accumulated into a block and the
LLM is asked to extract the Norton span corresponding to the block. A span that
is verbatim and within the search window but does not start at the beginning of
the remaining text is an "island": text in front of it belongs to a later
Italian line, so the block is incomplete, the next Italian line is added, and
the block is re-queried. A block completes when its span starts at the
beginning of the remaining text.

Supports two modes: direct Italian comparison (default) or translation-based
(--translate).
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


# Maximum Italian lines to accumulate in one block before giving up
MAX_BLOCK_LINES = 6

# Default search window: how many words of the remaining Norton text may
# precede an accepted span. 0 restores the strict prefix-only behavior.
DEFAULT_WINDOW_WORDS = 20

# Maximum word-count ratio of extracted English to source Italian
MAX_LENGTH_RATIO = 2.0

# Extraction attempts per query before giving up and asking for more context
MAX_ATTEMPTS = 3


# Structured output for extraction
class LineResult(BaseModel):
    """Extract line."""
    italian: str
    english: str


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
    """Represents a block of Italian lines aligned to Norton English text"""

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


def extract_norton_span(llm: LLMClient, italian_block: List[ItalianLine],
                        norton_text: str, skip_translation: bool = False,
                        window_words: int = DEFAULT_WINDOW_WORDS) -> Extraction | None:
    """
    Ask the LLM for the Norton span corresponding to the current Italian block.

    In --translate mode the Italian is first rendered into simple modern
    English, which is then used as the meaning reference; otherwise the Italian
    text is the reference directly.

    The answer is validated mechanically: it must be non-empty, appear verbatim
    in `norton_text` (anti-hallucination), stay within MAX_LENGTH_RATIO of the
    Italian word count, and start no more than `window_words` words into
    `norton_text`. A span at offset 0 completes the block; a span within the
    window but past offset 0 is an island, which the caller resolves by adding
    another Italian line.

    Args:
        skip_translation: If True, use the Italian text directly as reference
        window_words: Max words of `norton_text` allowed to precede the span;
                      0 means the span must be a strict prefix

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
        # Stage 1: Translate Italian to modern English
        # For single lines, translate just that line
        # For multiple lines (enjambment), translate all lines together
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

    # Stage 2: Find matching text in Norton English
    if window_words == 0:
        position_rule = "- Start from the very beginning of the Norton text"
    else:
        position_rule = ("- The text may start at the beginning of the Norton text or shortly after it;\n"
                         "  extract it where it actually appears, do not shift it to the beginning")

    for attempt in range(MAX_ATTEMPTS):
        # Extract Norton text corresponding to Italian line
        llm.history = []

        extraction_prompt = f"""Extract the Norton English text corresponding to the Italian line.

Italian line: {italian_text}
Meaning: {reference_text}

Norton English text (extract FROM this):
{norton_text[:500]}

INSTRUCTIONS:
- Find the Norton text that matches the Italian line's meaning: "{reference_text}"
{position_rule}
- Extract ONLY the portion corresponding to this single Italian line
- Copy EXACTLY from Norton (verbatim, no paraphrasing)
- Do NOT include content from other Italian lines

Output in JSON format:
- italian: the Italian line text (copy exactly: {italian_text})
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

        # Position check: how far into the remaining Norton text does it start?
        offset_words = count_words(norton_text[:idx])
        if offset_words > window_words:
            log_print(f"    ✗ Offset {offset_words} words exceeds window {window_words}")
            continue

        log_print(f"    ✓ Accepted (ratio: {ratio:.2f}, offset: {offset_words} words / {idx} chars)")
        return Extraction(extracted, offset_words, idx, end_pos)

    # No attempt produced a valid span - signal the caller to add more context
    notify(f"    ✗ Failed after {MAX_ATTEMPTS} attempts - need more context")
    return None


def align_paragraph(llm: LLMClient, italian_lines: List[ItalianLine],
                    norton_paragraph: str, start_idx: int,
                    skip_translation: bool = False,
                    window_words: int = DEFAULT_WINDOW_WORDS) -> Tuple[List[AlignmentBlock], int, str, bool]:
    """
    Align Italian lines to a Norton paragraph, finding the block boundary.

    Italian lines are added to the block one at a time. A block is complete
    when its extracted span starts at the beginning of the remaining paragraph
    text (offset 0). If the span starts further in but within the search
    window, the intervening text belongs to a later Italian line - an island -
    so the next line is added and the block is re-queried.

    Args:
        llm: LLM client for extraction queries
        italian_lines: Full list of Italian lines
        norton_paragraph: Remaining Norton paragraph text
        start_idx: Starting index in italian_lines
        skip_translation: If True, use Italian directly instead of translating to English
        window_words: Max words that may precede an accepted span

    Returns:
        Tuple of (List[AlignmentBlock], next_start_idx, remaining_paragraph_text, skipped)
        skipped is True if the block failed to align after MAX_BLOCK_LINES lines;
        the unconsumed norton_paragraph text is preserved (not discarded) so the
        caller can retry with the next Italian line(s) against the same paragraph.
    """
    italian_block = []
    idx = start_idx

    while idx < len(italian_lines):
        # Add next Italian line to block
        italian_block.append(italian_lines[idx])
        idx += 1

        print()
        notify(f"  Line {italian_block[-1].line_num}/{len(italian_lines)}: {italian_block[-1].full_text}")

        # Safety: if block gets too large, give up on these lines and let the
        # caller retry with the next Italian line(s) against this same paragraph
        if len(italian_block) > MAX_BLOCK_LINES:
            notify(f"    ⚠ Block exceeded {MAX_BLOCK_LINES} lines, skipping these line(s)")
            break

        # Query LLM for the corresponding Norton span
        result = extract_norton_span(llm, italian_block, norton_paragraph,
                                     skip_translation, window_words)

        # If extraction failed, try with more lines (enjambment case)
        if result is None:
            log_print(f"    → Failed with {len(italian_block)} line(s), trying with more context")
            continue

        # Island: the span is valid but text in front of it is still unmatched,
        # so it belongs to a later Italian line. The block is incomplete.
        # This costs a line, not a retry attempt.
        if result.offset_words > 0:
            notify(f"    ⟲ Island: span starts {result.offset_words} word(s) in, adding next line")
            continue

        # Block complete
        matched_text = result.text
        notify(f"    ✓ Complete: {matched_text}")

        remaining_text = norton_paragraph[result.end_pos:].lstrip(" ,;.!?")

        # Create single block (even for multiple Italian lines)
        block = AlignmentBlock(italian_block, norton_paragraph, matched_text)
        return [block], idx, remaining_text, False

    # Failed to align these lines: preserve norton_paragraph untouched so the
    # remaining Italian lines can still be matched against it
    return [], idx, norton_paragraph, True


def align_canto(llm: LLMClient, italian_filepath: str, norton_filepath: str,
                max_lines: int | None = None, log_file = None,
                skip_translation: bool = False,
                window_words: int = DEFAULT_WINDOW_WORDS) -> Tuple[List[AlignmentBlock], int]:
    """
    Align a full canto (Italian and Norton translation).

    Args:
        llm: LLM client for word correspondence queries
        italian_filepath: Path to tokenize/inferno/*.txt file
        norton_filepath: Path to en-norton/inferno/*.txt file
        max_lines: Maximum number of Italian lines to process (for testing)
        skip_translation: If True, use Italian directly instead of translating
        window_words: Max words that may precede an accepted span

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
                window_words
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


def format_aligned_output(blocks: List[AlignmentBlock]) -> str:
    """
    Format alignment blocks as bilingual output with line breaks at block boundaries.

    Args:
        blocks: List of AlignmentBlocks

    Returns:
        Formatted text with Italian and English side by side
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


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Align Italian and Norton English translation')
    parser.add_argument('canto_num', type=int, help='Canto number (e.g., 1 for Canto I)')
    parser.add_argument('--model', default='ollama:ministral-3:14b', help='LLM model to use (default: ollama:ministral-3:14b)')
    parser.add_argument('--max-lines', type=int, default=None, help='Max Italian lines to process (default: unlimited)')
    parser.add_argument('--temperature', type=float, default=1.0, help='LLM temperature (default: 1.0)')
    parser.add_argument('--think', action='store_true', help='Enable LLM thinking (disabled by default)')
    parser.add_argument('--translate', action='store_true', help='Translate Italian to English before matching (default: use Italian directly)')
    parser.add_argument('--window-words', type=int, default=DEFAULT_WINDOW_WORDS,
                        help=f'Max words that may precede an accepted span; 0 requires a strict prefix (default: {DEFAULT_WINDOW_WORDS})')
    parser.add_argument('--strict-prefix', action='store_true',
                        help='Baseline mode: require spans to start at the beginning (equivalent to --window-words 0)')

    args = parser.parse_args()

    window_words = 0 if args.strict_prefix else args.window_words

    # Format paths
    italian_file = f"tokenize/inferno/{args.canto_num:02d}.txt"
    norton_file = f"en-norton/inferno/{args.canto_num:02d}.txt"
    log_file_path = f"alignment/output/canto_{args.canto_num:02d}.log"

    print(f"Aligning Canto {args.canto_num}...")

    # Open log file
    global _log_file
    with open(log_file_path, 'w', encoding='utf-8') as log_f:
        _log_file = log_f

        log_print(f"=== Canto {args.canto_num} Alignment ===")
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}, "
                  f"Translate: {args.translate}, Window: {window_words} words")
        log_print()

        # Create LLM client
        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)

        # Align the canto
        blocks, total_lines = align_canto(llm, italian_file, norton_file, max_lines=args.max_lines,
                                          skip_translation=not args.translate,
                                          window_words=window_words)

        # Coverage: Italian lines that ended up in an output block. A block that
        # fails after MAX_BLOCK_LINES is skipped with no output, so italian_idx
        # reaching the end does not imply every line was covered.
        covered_lines = sum(len(block.italian_lines) for block in blocks)
        coverage_pct = 100.0 * covered_lines / total_lines if total_lines else 0.0

        # Write results to log
        log_print()
        log_print("=" * 80)
        log_print("RESULTS")
        log_print("=" * 80)
        log_print()

        # Write detailed output to log
        log_print("--- Detailed (Italian + English) ---")
        log_print(format_aligned_output(blocks))
        log_print()

        # Write Norton-only output to log
        log_print("--- Norton English (with line breaks) ---")
        norton_lines = []
        for block in blocks:
            matched = block.matched_text if block.matched_text else block.english_text
            norton_lines.append(matched)
        log_print('\n\n'.join(norton_lines))

        log_print()
        log_print(f"Total blocks: {len(blocks)}")
        log_print(f"Coverage: {covered_lines}/{total_lines} lines ({coverage_pct:.0f}%)")

    print(f"✓ Complete: {len(blocks)} blocks")
    print(f"✓ Coverage: {covered_lines}/{total_lines} lines ({coverage_pct:.0f}%)")
    print(f"✓ Log: {log_file_path}")


if __name__ == '__main__':
    main()
