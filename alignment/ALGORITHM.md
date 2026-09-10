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
4. Extract the span where it appears (only `--strict-prefix` asks the model
   to start from the beginning of the paragraph)

**Key Points (Both Modes):**
- Uses structured LLM output (JSON with `italian`/`english` fields) for extraction
- Rejects extractions whose text cannot be found verbatim in the Norton paragraph (hallucination check)
- Applies symmetric quote stripping (only removes quotes when text is fully enclosed)
- Restores trailing punctuation from original Norton text

## Block Boundary Detection

### Island Detection

Determines when a block of Italian lines is complete.

An accepted extraction is located in the remaining Norton paragraph text, and
its **word offset** — how many words precede it — decides the outcome:

```python
offset_words = count_words(norton_text[:idx])   # idx = position of the span

if offset_words > window_words:
    reject                  # too far in to be plausible; costs a retry attempt
elif offset_words > 0:
    island                  # block incomplete: add the next Italian line
else:
    complete                # span starts the remaining text: finalize the block
```

**Logic:**

- An "island" is a valid span with unmatched text still in front of it.
- That leading text has to belong to a *later* Italian line, which is exactly
  the Italian/English word-order divergence the mechanism exists to absorb.
  Adding the next line and re-querying lets the model return a span that
  covers both.
- An island costs a line, not a retry attempt: the extraction succeeded, only
  the block is unfinished.

**Search window:** `window_words` (default 20, `--window-words`) bounds how far
in a span may start. Without the bound, a span matching incidentally at the far
end of the paragraph would be read as an island and grow the block until
`MAX_BLOCK_LINES`, turning rejections into skipped lines. `--strict-prefix`
sets the window to 0, which requires the span to be a strict prefix and
reproduces the pre-fix behavior for A/B comparison.

**History:** island detection was previously unreachable. Validation accepted a
span only if `norton_text.startswith(extracted)`, so every input reaching the
detector was already a contiguous prefix and `Island: True` occurred 0 times
across all 8 full-canto test runs. The `#`-marker implementation that computed
this indirectly (and mis-matched short words as substrings) was removed in
favor of the offset comparison above. See [MEMO.md](MEMO.md) "Structural
analysis" and [ISLAND_FIX.md](ISLAND_FIX.md) for the requirements this change
implements.

### Enjambment Handling

A block grows to N+1 lines by either of two paths:

**Extraction failure** — no attempt produced a valid span:
1. Return `None` to signal more context needed
2. Automatically retry with N+1 lines
3. Continue until successful extraction or `MAX_BLOCK_LINES`

**Island** — a valid span was found, but text precedes it:
1. Accept the span but do not finalize the block
2. Add the next Italian line and re-query against the same paragraph text
3. Continue until a span starts at offset 0 or `MAX_BLOCK_LINES`

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

3. **Position Check (search window):**
   - The span must start no more than `window_words` words into the remaining
     Norton text; beyond that it is rejected
   - Within the window, the offset is not a pass/fail signal but a block
     boundary signal (see Island Detection above)
   - Punctuation-only text preceding a span does not count as an offset, since
     `count_words()` counts word tokens only

Each of the up to 3 retries re-runs the extraction prompt from scratch; there is no separate semantic-equivalence validation step. An island does not consume a retry attempt.

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
            - Non-empty?
            - Found verbatim in Norton text? (existence check)
            - Length ratio < 2.0?
            - Word offset <= window_words?

        If validation fails:
            Retry (up to 3 attempts), then return None → try with more lines

        If accepted with offset > 0:
            Island → add the next Italian line and re-query (no retry consumed)

        If accepted with offset == 0:
            Finalize the block; consume the span from the paragraph text

        If block exceeds 6 lines with no success:
            Skip these line(s) (no output); retry next line(s)
            against the same paragraph
```

## Success Metrics

Coverage — Italian lines that ended up in an output block, out of the canto
total — is reported by the tool itself at the end of a run and written to the
log. It is the meaningful completion metric: a block that fails after
`MAX_BLOCK_LINES` is skipped with no output, so reaching the last Italian line
does not imply every line was covered.

Full-canto results across models and modes are tracked in
[MEMO.md](MEMO.md). Key findings from Inferno Canto 1 (136 lines):

- Direct comparison (default) outperforms `--translate` with every model
  tested.
- Coverage ranges from 19% (local 14B model) to 100% (top-tier models).

**Note:** the MEMO.md numbers predate the search-window change and describe
strict-prefix behavior; they are reproducible with `--strict-prefix` and serve
as the A/B baseline. They have not yet been re-measured with the default
window.

## Configuration

- **LLM Model:** Ollama (ministral-3:14b) by default
- **Temperature:** 1.0
- **Max Retries:** 3 per extraction attempt
- **Length Ratio Threshold:** 2.0
- **Search Window:** 20 words (`--window-words`); 0 (`--strict-prefix`)
  requires the span to be a strict prefix, reproducing the pre-fix baseline
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
5. Only the first 500 characters of the remaining paragraph are shown to the
   model, so a correct span past that cutoff is unreachable
6. A block still maps N Italian lines to one contiguous Norton span; the
   correspondence *inside* a multi-line block is not resolved (see MEMO.md
   Finding 3)

## Future Improvements

1. **Caching:** Cache modern translations to avoid repeated translations
2. **Parallel Processing:** Process multiple paragraphs simultaneously
3. **Human Review Interface:** Flag uncertain alignments for manual review
4. **Alternative Models:** Test with larger models (70B+) for better accuracy
5. **Re-measure the baseline:** Re-run MEMO.md's model comparison with the
   search window enabled and compare against `--strict-prefix`
6. **Line-level correspondence:** Resolve alignment inside multi-line blocks
   (embedding-based DP or paragraph-level correspondence extraction — see
   MEMO.md "Redesign directions")

## Files

- `alignment/align_canto.py` - Main implementation
- `alignment/output/` - Log files and results
- `tokenize/inferno/` - Tokenized Italian text
- `en-norton/inferno/` - Norton English translation

## References

- ISLAND_FIX.md - Requirements for the search-window / island change
- PLAN.md - Original implementation planning document
- PRIOR_WORK.md - Analysis of previous alignment attempts
