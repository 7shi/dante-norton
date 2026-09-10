"""
Align Italian and Norton English translation using LLM-based word correspondence.

Implements the variable-length block alignment algorithm with island detection.
Supports two modes: direct Italian comparison (default) or translation-based (--translate).
"""

import re
import sys
import json
from pathlib import Path
from typing import List, Tuple
from pydantic import BaseModel, Field

# Add parent directory to path to import dante_norton
sys.path.insert(0, str(Path(__file__).parent.parent))

from dante_norton import Canto, LLMClient


# Structured output for extraction
class LineResult(BaseModel):
    """Extract line."""
    italian: str
    english: str


# Global log file handle
_log_file = None


def log_print(*args, **kwargs):
    """Print to log file only"""
    if _log_file:
        print(*args, **kwargs, file=_log_file)
        _log_file.flush()


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


def is_block_complete(norton_text: str, matched_words: List[str]) -> bool:
    """
    Check if block is complete by detecting "islands" (gaps in matching).

    An island occurs when there are unmatched words followed by matched words,
    indicating that word order differs between Italian and English.

    Returns:
        True if no islands detected (block is complete)
    """
    # Replace matched words with "#"
    test_text = norton_text
    for word in matched_words:
        test_text = test_text.replace(word, "#" * len(word), 1)

    # Log the replaced text
    log_print(f"    Replaced: {test_text[:100]}{'...' if len(test_text) > 100 else ''}")

    # Find first alphabetic character
    first_alpha_idx = next((i for i, c in enumerate(test_text) if c.isalpha()), None)

    if first_alpha_idx is None:
        # No unmatched letters remaining - block complete
        log_print(f"    Island: False (no unmatched text)")
        return True

    # Check if "#" appears after first alphabetic character (= island)
    has_island = '#' in test_text[first_alpha_idx:]

    log_print(f"    Island: {has_island}")

    # Block complete if no island detected
    return not has_island


def query_word_correspondences(llm: LLMClient, italian_block: List[ItalianLine],
                               norton_text: str, skip_translation: bool = False) -> Tuple[str, List[str]] | None:
    """
    Query LLM to extract English text corresponding to Italian block.
    Uses two-stage approach: translate Italian first, then match in Norton text.

    Args:
        skip_translation: If True, use Italian text directly as reference instead of translating

    Returns:
        Tuple of (extracted text, word list) or None if extraction failed
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
    length_hint = "SHORT (likely one phrase or clause)" if num_italian_lines == 1 else f"matching {num_italian_lines} Italian lines"

    # Try up to 3 times with validation
    english_text = None
    last_validation = None

    for retry in range(3):
        # Extract Norton text corresponding to Italian line
        llm.history = []

        extraction_prompt = f"""Extract the Norton English text corresponding to the Italian line.

Italian line: {italian_text}
Meaning: {reference_text}

Norton English text (extract FROM this):
{norton_text[:500]}

INSTRUCTIONS:
- Find the Norton text that matches the Italian line's meaning: "{reference_text}"
- Start from the very beginning of the Norton text
- Extract ONLY the portion corresponding to this single Italian line
- Copy EXACTLY from Norton (verbatim, no paraphrasing)
- Do NOT include content from other Italian lines

Output in JSON format:
- italian: the Italian line text (copy exactly: {italian_text})
- english: the corresponding Norton English text (verbatim extraction)"""

        try:
            response = llm.call(extraction_prompt, schema=LineResult)
            result = json.loads(response)
            extracted = result.get("english", "").strip()

        except Exception as e:
            log_print(f"    ✗ Failed to parse structured output: {e}")
            last_validation = "NO"
            continue

        # Strip quotes only if text is fully enclosed in matching quotes
        if (extracted.startswith('"') and extracted.endswith('"')) or \
           (extracted.startswith("'") and extracted.endswith("'")):
            extracted = extracted[1:-1]

        # Check for hallucination: extracted text must exist in Norton text
        # Strip leading/trailing quotes and whitespace for matching
        extracted_for_search = extracted.strip().strip('"\'""''').strip()
        idx = norton_text.lower().find(extracted_for_search.lower())
        if idx == -1:
            log_print(f"    Extracted: {extracted[:100]}{'...' if len(extracted) > 100 else ''}")
            log_print(f"    ✗ Hallucination: text not found in Norton")
            last_validation = "NO"
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

        # Validate extraction using word count ratio only
        log_print(f"    Extracted: {extracted[:100]}{'...' if len(extracted) > 100 else ''}")

        # Length ratio check (hard constraint)
        italian_word_count = len(italian_text.split())
        extracted_word_count = len(extracted.split())
        ratio = extracted_word_count / italian_word_count if italian_word_count > 0 else 0

        if ratio > 2.0:
            log_print(f"    ✗ Length ratio {ratio:.2f} exceeds 2.0 (IT:{italian_word_count} EN:{extracted_word_count})")
            last_validation = "NO"
            continue

        # Verify it's actually at the beginning
        if norton_text.startswith(extracted):
            english_text = extracted
            log_print(f"    ✓ Accepted (ratio: {ratio:.2f})")
            break
        else:
            # Try normalized match
            extracted_normalized = extracted.lower().strip('.,;:!?\'" ')
            norton_normalized = norton_text.lower()

            if norton_normalized.startswith(extracted_normalized):
                # Minor punctuation difference
                english_text = extracted
                log_print(f"    ✓ Accepted (normalized, ratio: {ratio:.2f})")
                break
            else:
                log_print(f"    ✗ Not at beginning of Norton text")
                last_validation = "NO"
                continue

    # Check if we succeeded
    if english_text:
        return (english_text, english_text.split())
    else:
        # Failed after 3 attempts - return None to signal caller to try with more lines
        log_print(f"    ✗ Failed after 3 attempts - need more context")
        return None


def consume_matched_text(text: str, matched_words: List[str]) -> str:
    """
    Remove continuously matched words from the beginning of text.
    Stop at first gap.
    """
    result = text

    for word in matched_words:
        result = result.lstrip(" ,;.!?")
        if result.startswith(word):
            result = result[len(word):]
        else:
            break

    return result.lstrip(" ,;.!?")


def align_paragraph(llm: LLMClient, italian_lines: List[ItalianLine],
                    norton_paragraph: str, start_idx: int,
                    skip_translation: bool = False) -> Tuple[List[AlignmentBlock], int, str, bool]:
    """
    Align Italian lines to a Norton paragraph, finding the block boundary.

    Args:
        llm: LLM client for word correspondence queries
        italian_lines: Full list of Italian lines
        norton_paragraph: Norton English paragraph text
        start_idx: Starting index in italian_lines
        skip_translation: If True, use Italian directly instead of translating to English

    Returns:
        Tuple of (List[AlignmentBlock], next_start_idx, remaining_paragraph_text, skipped)
        skipped is True if alignment failed and paragraph was skipped
    """
    italian_block = []
    idx = start_idx

    # Maximum lines to accumulate before giving up on current paragraph
    MAX_BLOCK_LINES = 6

    while idx < len(italian_lines):
        # Add next Italian line to block
        italian_block.append(italian_lines[idx])
        idx += 1

        log_print(f"  Line {italian_block[-1].line_num}: {italian_block[-1].full_text}")

        # Safety: if block gets too large, skip to next paragraph
        if len(italian_block) > MAX_BLOCK_LINES:
            log_print(f"    ⚠ Block exceeded {MAX_BLOCK_LINES} lines, skipping to next paragraph")
            break

        # Query LLM for word correspondences
        result = query_word_correspondences(llm, italian_block, norton_paragraph, skip_translation)

        # If extraction failed, try with more lines (enjambment case)
        if result is None:
            log_print(f"    → Failed with {len(italian_block)} line(s), trying with more context")
            continue

        extracted_text, matched_words = result
        log_print(f"    → {matched_words}")

        # Check if block is complete
        if is_block_complete(norton_paragraph, matched_words):
            # Use the extracted text directly (preserves punctuation)
            matched_text = extracted_text
            log_print(f"    ✓ Complete: {matched_text}")

            remaining_text = norton_paragraph[len(matched_text):].lstrip(" ,;.!?")

            # Create single block (even for multiple Italian lines)
            block = AlignmentBlock(italian_block, norton_paragraph, matched_text)
            return [block], idx, remaining_text, False

        log_print(f"    → Continue")

    # Reached end (failed to align)
    block = AlignmentBlock(italian_block, norton_paragraph)
    return [block], idx, "", True  # True indicates skip occurred


def find_matching_italian_line(llm: LLMClient, norton_text: str,
                                italian_lines: List[ItalianLine],
                                start_idx: int, search_range: int = 20) -> int:
    """
    Find which Italian line corresponds to the start of a Norton paragraph.

    Args:
        llm: LLM client
        norton_text: Start of Norton paragraph
        italian_lines: List of all Italian lines
        start_idx: Current index in italian_lines
        search_range: How many lines ahead to search

    Returns:
        Index of the matching Italian line, or start_idx if not found
    """
    # Get first ~100 chars of Norton text for matching
    norton_start = norton_text[:150].strip()

    # Build candidates from nearby Italian lines
    end_idx = min(start_idx + search_range, len(italian_lines))
    candidates = []
    for i in range(start_idx, end_idx):
        candidates.append(f"{i+1}. (Line {italian_lines[i].line_num}) {italian_lines[i].full_text}")

    if not candidates:
        return start_idx

    prompt = f"""Which Italian line corresponds to the START of this Norton English text?

Norton English (beginning):
{norton_start}

Italian line candidates:
{chr(10).join(candidates)}

Reply with ONLY the number (1, 2, 3, etc.) of the best matching Italian line.
If the Norton text starts in the middle of a line's meaning, choose that line.
If unsure, reply with 1."""

    llm.history = []
    response = llm.call(prompt).strip()

    # Parse response
    try:
        # Extract first number from response
        import re
        match = re.search(r'\d+', response)
        if match:
            choice = int(match.group())
            if 1 <= choice <= len(candidates):
                new_idx = start_idx + choice - 1
                log_print(f"    ↻ Re-sync: Norton start matches Italian line {italian_lines[new_idx].line_num}")
                return new_idx
    except:
        pass

    return start_idx


def align_canto(llm: LLMClient, italian_filepath: str, norton_filepath: str,
                max_lines: int | None = None, log_file = None,
                skip_translation: bool = False) -> List[AlignmentBlock]:
    """
    Align a full canto (Italian and Norton translation).

    Args:
        llm: LLM client for word correspondence queries
        italian_filepath: Path to tokenize/inferno/*.txt file
        norton_filepath: Path to en-norton/inferno/*.txt file
        max_lines: Maximum number of Italian lines to process (for testing)
        skip_translation: If True, use Italian directly instead of translating

    Returns:
        List of AlignmentBlocks
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

        log_print(f"Paragraph {para_num}: {norton_paragraph[:60]}...")

        # Remove annotation markers
        clean_paragraph = re.sub(r'\[\d+\]', '', norton_paragraph)

        # Process multiple blocks within this paragraph
        remaining_text = clean_paragraph
        block_num = 1
        need_resync = False

        # Minimum remaining text length to continue processing
        MIN_REMAINING_LENGTH = 10

        while remaining_text.strip() and len(remaining_text.strip()) >= MIN_REMAINING_LENGTH and italian_idx < len(italian_lines):
            # Align this block (may return multiple blocks if split)
            new_blocks, italian_idx, remaining_text, skipped = align_paragraph(
                llm, italian_lines, remaining_text, italian_idx, skip_translation
            )

            if skipped:
                # Alignment failed, need to re-sync at next paragraph
                need_resync = True
                log_print(f"    ⚠ Alignment failed, will re-sync at next paragraph")
                break

            blocks.extend(new_blocks)
            block_num += len(new_blocks)
            log_print()

            if italian_idx >= len(italian_lines):
                break

        # If we need to re-sync, find the matching Italian line for the next paragraph
        if need_resync and italian_idx < len(italian_lines):
            # Peek at next paragraph to find matching Italian line
            next_para_idx = para_num
            for next_para_num, (next_para, _) in enumerate(norton_canto.lines, 1):
                if next_para_num > para_num and next_para.strip():
                    next_para_clean = re.sub(r'\[\d+\]', '', next_para)
                    if len(next_para_clean.strip()) >= MIN_REMAINING_LENGTH:
                        italian_idx = find_matching_italian_line(
                            llm, next_para_clean, italian_lines, italian_idx
                        )
                        break

        if italian_idx >= len(italian_lines):
            break

    return blocks


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

    args = parser.parse_args()

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
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}, Translate: {args.translate}")
        log_print()

        # Create LLM client
        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)

        # Align the canto
        blocks = align_canto(llm, italian_file, norton_file, max_lines=args.max_lines, skip_translation=not args.translate)

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

    print(f"✓ Complete: {len(blocks)} blocks")
    print(f"✓ Log: {log_file_path}")


if __name__ == '__main__':
    main()
