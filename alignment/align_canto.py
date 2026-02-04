"""
Align Italian and Norton English translation using LLM-based word correspondence.

Implements the variable-length block alignment algorithm from PLAN.md.
Simple version without gap detection.
"""

import re
import sys
import json
from pathlib import Path
from typing import List, Tuple, Literal
from pydantic import BaseModel, Field

# Add parent directory to path to import dante_norton
sys.path.insert(0, str(Path(__file__).parent.parent))

from dante_norton import Canto, LLMClient
from llm7shi import create_json_descriptions_prompt


# Structured output schemas
class ExtractionResult(BaseModel):
    """LLM extraction result for Norton English text."""
    extracted_text: str = Field(
        description="The extracted Norton English text, without any quotation marks or formatting"
    )


class ValidationResult(BaseModel):
    """LLM validation result for Italian-English correspondence."""
    reason: str = Field(
        description="Brief explanation (one sentence) analyzing if the English matches the Italian exactly"
    )
    answer: Literal["YES", "NO"] = Field(
        description="YES if English corresponds exactly to Italian (no more, no less), NO otherwise"
    )


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
                               norton_text: str) -> Tuple[str, List[str]] | None:
    """
    Query LLM to extract English text corresponding to Italian block.
    Uses two-stage approach: translate Italian first, then match in Norton text.

    Returns:
        List of English words that correspond to the Italian block
    """
    italian_text = '\n'.join(line.full_text for line in italian_block)
    num_italian_lines = len(italian_block)

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
    modern_translation = translation_response.strip()
    log_print(f"    Modern translation: {modern_translation}")

    # Stage 2: Find matching text in Norton English
    length_hint = "SHORT (likely one phrase or clause)" if num_italian_lines == 1 else f"matching {num_italian_lines} Italian lines"

    # Create field descriptions for extraction
    json_descriptions_extract = create_json_descriptions_prompt(ExtractionResult)

    extract_prompt = f"""Task: Extract the corresponding text from the Norton English translation.

Reference meaning (modern English):
{modern_translation}

Source text (Norton's literary translation - extract FROM this text):
{norton_text[:500]}

INSTRUCTIONS:
1. Read the Norton text carefully
2. Find the portion that means the SAME as the modern translation
3. The wording will be DIFFERENT (literary vs modern)
4. Extract the EXACT text from the Norton passage
5. Start from the very beginning of the Norton text
6. Extract approximately: {length_hint}

LENGTH CONSTRAINT:
- The modern translation above represents ONE line of Italian
- Extract ONLY a SHORT phrase or clause from the Norton text
- Do NOT extract multiple sentences
- When in doubt, extract LESS rather than more

CRITICAL: Output must be the ACTUAL TEXT from the Norton passage above, not a rephrasing.

{json_descriptions_extract}"""

    # Try up to 3 times with validation
    english_text = None
    last_validation = None

    for retry in range(3):
        # Step 1: Extract English text (structured output)
        llm.history = []
        if retry == 0:
            response = llm.call(extract_prompt, schema=ExtractionResult)
        else:
            if last_validation == "NO":
                retry_prompt = f"""Modern English translation:
{modern_translation}

Norton English text (literary translation):
{norton_text[:500]}

CRITICAL: The previous extraction was INCORRECT.

Find the portion of the Norton text that matches the modern translation above.
- Extract ONLY the matching portion
- Do NOT include content from other parts of the Norton text
- The Italian has {num_italian_lines} line(s), so extract a {length_hint} amount

{json_descriptions_extract}"""
                response = llm.call(retry_prompt, schema=ExtractionResult)
            else:
                response = llm.call(extract_prompt, schema=ExtractionResult)

        # Parse structured extraction response
        extraction_data = json.loads(response)
        extracted = extraction_data["extracted_text"].strip()

        # Restore punctuation from original Norton text if missing
        # Find the position of extracted text in norton_text
        idx = norton_text.lower().find(extracted.lower())
        if idx != -1:
            end_pos = idx + len(extracted)
            # Check if there's punctuation immediately after in the original
            if end_pos < len(norton_text):
                next_char = norton_text[end_pos]
                if next_char in ',.;:!?' and not extracted.endswith(next_char):
                    # Add the punctuation if missing
                    extracted = extracted + next_char

        # Step 2: Validate extraction
        llm.history = []  # Clear history for validation

        # Create field descriptions for Ollama
        json_descriptions = create_json_descriptions_prompt(ValidationResult)

        validation_prompt = f"""Modern English (meaning reference):
{modern_translation}

Extracted Norton English:
{extracted}

Original Italian ({num_italian_lines} line(s)):
{italian_text}

Question: Does the extracted Norton English convey the same meaning as the modern translation (and thus the original Italian)?

VALIDATION CRITERIA:
- The Norton text is a literary translation, so different wording is EXPECTED
- Focus on SEMANTIC EQUIVALENCE: does it convey the same basic meaning?
- The extracted text should NOT include content from other Italian lines
- Length should roughly match ({length_hint})

Answer YES if:
- The extracted Norton English conveys the same meaning as the modern translation
- It does NOT include content from other parts of the text

Answer NO if:
- The extraction clearly includes content from OTHER parts of the text
- The extraction is MUCH LONGER than the reference suggests

{json_descriptions}"""

        validation_response = llm.call(
            validation_prompt,
            schema=ValidationResult
        )

        # Parse JSON response
        validation_data = json.loads(validation_response)
        answer = validation_data["answer"]
        reason = validation_data.get("reason", "")

        log_print(f"    Extracted: {extracted[:100]}{'...' if len(extracted) > 100 else ''}")
        log_print(f"    LLM validation: {answer}" + (f" - {reason}" if reason else ""))

        # Step 3: Process validation result
        if answer == "YES":
            # Length ratio check (hard constraint)
            italian_word_count = len(italian_text.split())
            extracted_word_count = len(extracted.split())
            ratio = extracted_word_count / italian_word_count if italian_word_count > 0 else 0

            if ratio > 1.8:
                log_print(f"    ✗ Length ratio {ratio:.2f} exceeds 1.8 (IT:{italian_word_count} EN:{extracted_word_count})")
                last_validation = "NO"
                continue

            # Verify it's actually at the beginning
            if norton_text.startswith(extracted):
                english_text = extracted
                log_print(f"    ✓ Validated (ratio: {ratio:.2f})")
                break
            else:
                # LLM says correct but not at beginning - try to find best match
                extracted_normalized = extracted.lower().strip('.,;:!?\'" ')
                norton_normalized = norton_text.lower()

                if norton_normalized.startswith(extracted_normalized):
                    # Minor punctuation difference
                    english_text = extracted
                    log_print(f"    ✓ Validated (normalized, ratio: {ratio:.2f})")
                    break
                else:
                    log_print(f"      Not at beginning of Norton text")
                    last_validation = "NO"
        else:  # answer == "NO"
            log_print(f"      Validation failed: {reason}")
            last_validation = "NO"

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
                    norton_paragraph: str, start_idx: int) -> Tuple[AlignmentBlock, int, str]:
    """
    Align Italian lines to a Norton paragraph, finding the block boundary.

    Args:
        llm: LLM client for word correspondence queries
        italian_lines: Full list of Italian lines
        norton_paragraph: Norton English paragraph text
        start_idx: Starting index in italian_lines

    Returns:
        Tuple of (AlignmentBlock, next_start_idx, remaining_paragraph_text)
    """
    italian_block = []
    idx = start_idx

    while idx < len(italian_lines):
        # Add next Italian line to block
        italian_block.append(italian_lines[idx])
        idx += 1

        log_print(f"  Line {italian_block[-1].line_num}: {italian_block[-1].full_text}")

        # Query LLM for word correspondences
        result = query_word_correspondences(llm, italian_block, norton_paragraph)

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

            block = AlignmentBlock(italian_block, norton_paragraph, matched_text)
            remaining_text = norton_paragraph[len(matched_text):].lstrip(" ,;.!?")

            return block, idx, remaining_text

        log_print(f"    → Continue")

    # Reached end
    block = AlignmentBlock(italian_block, norton_paragraph)
    return block, idx, ""


def align_canto(llm: LLMClient, italian_filepath: str, norton_filepath: str,
                max_lines: int | None = None, log_file = None) -> List[AlignmentBlock]:
    """
    Align a full canto (Italian and Norton translation).

    Args:
        llm: LLM client for word correspondence queries
        italian_filepath: Path to tokenize/inferno/*.txt file
        norton_filepath: Path to en-norton/inferno/*.txt file
        max_lines: Maximum number of Italian lines to process (for testing)

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

        while remaining_text.strip() and italian_idx < len(italian_lines):
            # Align this block
            block, italian_idx, remaining_text = align_paragraph(
                llm, italian_lines, remaining_text, italian_idx
            )
            blocks.append(block)
            block_num += 1
            log_print()

            if italian_idx >= len(italian_lines):
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
    parser.add_argument('--max-lines', type=int, default=20, help='Max Italian lines to process (default: 20)')
    parser.add_argument('--temperature', type=float, default=0.3, help='LLM temperature (default: 0.3)')
    parser.add_argument('--think', action='store_true', help='Enable LLM thinking (disabled by default)')

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
        log_print(f"Model: {args.model}, Temperature: {args.temperature}, Think: {args.think}")
        log_print()

        # Create LLM client
        llm = LLMClient(model=args.model, think=args.think, temperature=args.temperature)

        # Align the canto
        blocks = align_canto(llm, italian_file, norton_file, max_lines=args.max_lines)

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
