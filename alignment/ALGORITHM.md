# Alignment Algorithm

## Overview

This document describes the algorithm used to align Italian lines from Dante's *Inferno* with Charles Eliot Norton's English prose translation.

## Core Challenge

Italian terza rima poetry and English prose have different structures:
- Italian: Line-based with fixed meter and rhyme scheme
- English (Norton): Paragraph-based prose with natural sentence flow

The goal is to determine which Norton sentences correspond to which Italian lines, handling cases where word order differs or multiple Italian lines map to a single English sentence.

## Two-Stage Extraction Algorithm

The algorithm supports two modes:
- **Direct Comparison (default):** Uses Italian text directly as reference
- **Translation-Based (`--translate`):** Translates Italian to modern English first

### Mode 1: Direct Comparison (Default)

**Purpose:** Align using Italian text directly without intermediate translation.

**Process:**
1. Take Italian line(s) as input
2. Use Italian text directly as semantic reference
3. Search Norton's text for equivalent meaning
4. Extract the exact text from Norton's prose

**Advantages:**
- Faster (one fewer LLM call per block)
- Avoids potential translation errors
- Works well when LLM understands both Italian and English

### Mode 2: Translation-Based (`--translate` flag)

**Measured:** worse than direct comparison with every model tested (see
[MEMO.md](MEMO.md)); kept as an experiment switch, not recommended.

**Purpose:** Create a semantic reference point independent of Norton's literary style.

**Process:**

**Stage 1: Modern Translation**
1. Take Italian line(s) as input
2. Translate to simple, modern English using LLM
3. For single lines: translate individually
4. For multi-line blocks: translate together (for enjambment cases)

**Example:**
- Italian: "Nel mezzo del cammin di nostra vita"
- Modern: "In the middle of our life's journey"

**Stage 2: Norton Text Extraction**
1. Use modern translation as the meaning reference
2. Search Norton's text for equivalent meaning (not word-for-word)
3. Extract the exact text from Norton's prose
4. Start from the beginning of the current paragraph

**Key Points (Both Modes):**
- Uses structured LLM output (JSON with `italian`/`english` fields) for extraction
- Rejects extractions whose text cannot be found verbatim in the Norton paragraph (hallucination check)
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

**Known issue (measured):** in practice `has_island` is always False. An
accepted extraction is always a contiguous prefix of the remaining Norton
text (position check below), so the `#` markers always form one contiguous
region at the start, and no `#` can appear after the first unmatched
character. `is_block_complete()` therefore returns True on every successful
extraction — `Island: True` occurred 0 times across all 8 full-canto test
runs. Block completion is decided by extraction acceptance alone;
multi-line blocks arise from the failure-retry path, not from island
detection. See [MEMO.md](MEMO.md) "Structural analysis" for details and
redesign implications.

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

Validation relies on mechanical checks against the Norton source text rather than a separate LLM judgment call.

### Hard Constraints

1. **Existence Check (anti-hallucination):**
   - Extracted text must be found verbatim (case-insensitive) within the Norton paragraph
   - Rejects fabricated or paraphrased extractions

2. **Length Ratio Check:**
   - If extracted word count > 2.0 × Italian word count → reject
   - Prevents over-extraction of adjacent lines

3. **Position Check:**
   - Extracted text must appear at the beginning of remaining Norton text (exact or punctuation-normalized match)
   - Ensures sequential processing

Each of the up to 3 retries re-runs the extraction prompt from scratch; there is no separate semantic-equivalence validation step.

## Quote Stripping

**Symmetric Quote Removal:**
```python
extracted = result.get("english", "").strip()
if (extracted.startswith('"') and extracted.endswith('"')) or \
   (extracted.startswith("'") and extracted.endswith("'")):
    extracted = extracted[1:-1]
```

**Rules:**
- Only removes quotes when text is fully enclosed
- Preserves internal apostrophes (don't, it's)
- Preserves exclamation marks (Ah!)

## Punctuation Restoration

Restores trailing punctuation from original Norton text. By this point `idx`
is already known (the existence check above found `extracted` in
`norton_text`), so no `-1` guard is needed:

```python
end_pos = idx + len(extracted)
if end_pos < len(norton_text):
    next_char = norton_text[end_pos]
    if next_char in ',.;:!?' and not extracted.endswith(next_char):
        extracted = extracted + next_char
```

This ensures output matches original formatting (e.g., "dark wood," not "dark wood").

## Failure Recovery

If a block accumulates more than 6 Italian lines (`MAX_BLOCK_LINES`) without
a successful extraction, those line(s) are skipped with no output
("Block exceeded" in the log), and the paragraph's remaining text is
preserved: alignment continues with the next Italian line(s) against the
same paragraph. `italian_idx` therefore always advances to the canto's end,
but lines consumed by skipped blocks produce no output — hence "coverage"
(line-based completion) is tracked separately in [MEMO.md](MEMO.md).

Note: an earlier re-sync mechanism (`find_matching_italian_line`, which
jumped to the next Norton paragraph and re-found the Italian position) was
removed as unreachable — see "Bug history" in [MEMO.md](MEMO.md).

## Processing Flow

```
For each Norton paragraph:
    For each Italian line:
        Add line to current block

        (--translate only) Stage 1: Translate block to modern English
        Stage 2: Extract corresponding Norton text (structured JSON output)

        Validate extraction:
            - Found verbatim in Norton text? (existence check)
            - Length ratio < 2.0?
            - Position correct?

        If validation fails:
            Retry (up to 3 attempts), then return None → try with more lines

        If block exceeds 6 lines with no success:
            Skip these line(s) (no output); retry next line(s)
            against the same paragraph

        A successful extraction always finalizes the block
        (island detection never fires in practice — see known issue above)
```

## Success Metrics

Full-canto results across models and modes are tracked in
[MEMO.md](MEMO.md). Key findings from Inferno Canto 1 (136 lines):

- Direct comparison (default) outperforms `--translate` with every model
  tested.
- Coverage ranges from 19% (local 14B model) to 100% (top-tier models), but
  even top-tier models need retries — the prefix-based task formulation is
  the bottleneck (see MEMO.md "Structural analysis").

## Configuration

- **LLM Model:** Ollama (ministral-3:14b) by default
- **Temperature:** 1.0
- **Max Retries:** 3 per extraction attempt
- **Length Ratio Threshold:** 2.0
- **Max Block Lines:** 6 (beyond this the block's line(s) are skipped with
  no output; the paragraph text is preserved for the next line(s))
- **Default Mode:** Direct comparison (Italian text used directly)
- **Translation Mode:** Optional `--translate` flag; measured worse than
  direct comparison with all models tested (see MEMO.md), kept as an
  experiment switch

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
