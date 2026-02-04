# Alignment Algorithm

## Overview

This document describes the algorithm used to align Italian lines from Dante's *Inferno* with Charles Eliot Norton's English prose translation.

## Core Challenge

Italian terza rima poetry and English prose have different structures:
- Italian: Line-based with fixed meter and rhyme scheme
- English (Norton): Paragraph-based prose with natural sentence flow

The goal is to determine which Norton sentences correspond to which Italian lines, handling cases where word order differs or multiple Italian lines map to a single English sentence.

## Two-Stage Extraction Algorithm

### Stage 1: Modern Translation

**Purpose:** Create a semantic reference point independent of Norton's literary style.

**Process:**
1. Take Italian line(s) as input
2. Translate to simple, modern English using LLM
3. For single lines: translate individually
4. For multi-line blocks: translate together (for enjambment cases)

**Example:**
- Italian: "Nel mezzo del cammin di nostra vita"
- Modern: "In the middle of our life's journey"

### Stage 2: Norton Text Extraction

**Purpose:** Find the semantically equivalent text in Norton's translation.

**Process:**
1. Use modern translation as the meaning reference
2. Search Norton's text for equivalent meaning (not word-for-word)
3. Extract the exact text from Norton's prose
4. Start from the beginning of the current paragraph

**Key Points:**
- Uses plain text LLM output (structured output caused over-extraction)
- Applies symmetric quote stripping (only removes quotes when text is fully enclosed)
- Restores trailing punctuation from original Norton text

## Block Boundary Detection

### Island Detection Algorithm

Determines when a block of Italian lines is complete.

```python
def is_block_complete(norton_text, matched_words):
    # Replace matched words with markers
    test_text = norton_text
    for word in matched_words:
        test_text = test_text.replace(word, "#" * len(word), 1)
    
    # Find first unmatched character
    first_alpha_idx = next((i for i, c in enumerate(test_text) if c.isalpha()), None)
    
    if first_alpha_idx is None:
        return True  # All text matched
    
    # Check if markers appear after unmatched text (island)
    has_island = '#' in test_text[first_alpha_idx:]
    
    return not has_island  # Complete if no islands
```

**Logic:**
- An "island" is matched text after unmatched text
- Indicates word order differences between Italian and English
- Block is complete when all text from start is matched continuously

### Enjambment Handling

When extraction fails for a single line:
1. Return `None` to signal more context needed
2. Automatically retry with N+1 lines
3. Continue until successful extraction or maximum attempts

**Example:**
- Line 4 alone: "Ahi quanto a dir qual era è cosa dura" (extraction fails)
- Lines 4-5: "Ahi quanto a dir... / esta selva selvaggia..." (extraction succeeds)
- Result: 2 Italian lines → 1 English sentence

## Validation

### Semantic Validation

Validates that extracted Norton text matches the modern translation:
- **Input:** Modern translation + Extracted Norton text
- **Output:** YES/NO decision via structured LLM output
- **Focus:** Semantic equivalence, not word-for-word matching

### Hard Constraints

1. **Length Ratio Check:**
   - If extracted word count > 1.8 × Italian word count → reject
   - Prevents over-extraction of adjacent lines

2. **Position Check:**
   - Extracted text must appear at the beginning of remaining Norton text
   - Ensures sequential processing

## Quote Stripping

**Symmetric Quote Removal:**
```python
extracted = response.strip()
if (extracted.startswith('"') and extracted.endswith('"')) or \
   (extracted.startswith("'") and extracted.endswith("'")):
    extracted = extracted[1:-1]
```

**Rules:**
- Only removes quotes when text is fully enclosed
- Preserves internal apostrophes (don't, it's)
- Preserves exclamation marks (Ah!)

## Punctuation Restoration

Restores trailing punctuation from original Norton text:

```python
idx = norton_text.lower().find(extracted.lower())
if idx != -1:
    end_pos = idx + len(extracted)
    if end_pos < len(norton_text):
        next_char = norton_text[end_pos]
        if next_char in ',.;:!?' and not extracted.endswith(next_char):
            extracted = extracted + next_char
```

This ensures output matches original formatting (e.g., "dark wood," not "dark wood").

## Processing Flow

```
For each Norton paragraph:
    For each Italian line:
        Add line to current block
        
        Stage 1: Translate block to modern English
        Stage 2: Extract corresponding Norton text
        
        Validate extraction:
            - Semantic match? (LLM validation)
            - Length ratio < 1.8?
            - Position correct?
        
        If validation fails:
            Return None → Retry with more lines
        
        Check block completion (island detection):
            If complete → Finalize block
            If islands → Continue to next Italian line
```

## Success Metrics

From test on Inferno Canto 1, Lines 1-9:
- **9 Italian lines → 8 blocks**
- 6 individual lines mapped correctly
- 1 enjambment case detected (Lines 4-5 merged)
- 100% success rate on test set

## Configuration

- **LLM Model:** Ollama (ministral-3:14b)
- **Temperature:** 0.3
- **Max Retries:** 3 per extraction attempt
- **Length Ratio Threshold:** 1.8

## Limitations

1. Depends on LLM quality for translation and extraction
2. May struggle with highly compressed or expanded translations
3. Requires sufficient context (paragraph-level alignment)
4. Computational cost: Multiple LLM calls per line

## Future Improvements

1. **Caching:** Cache modern translations to avoid repeated translations
2. **Parallel Processing:** Process multiple paragraphs simultaneously
3. **Human Review Interface:** Flag uncertain alignments for manual review
4. **Alternative Models:** Test with larger models (70B+) for better accuracy
5. **Full Canto Test:** Validate on all 136 lines of Canto 1

## Files

- `alignment/align_canto.py` - Main implementation
- `alignment/output/` - Log files and results
- `tokenize/inferno/` - Tokenized Italian text
- `en-norton/inferno/` - Norton English translation

## References

- PLAN.md - Original implementation planning document
- PRIOR_WORK.md - Analysis of previous alignment attempts
