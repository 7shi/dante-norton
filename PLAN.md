# Implementation Plan

This document contains the implementation plan for the Dante Norton project, based on analysis of prior work and iterative discussion.

## Background

See [PRIOR_WORK.md](PRIOR_WORK.md) for detailed analysis of previous attempts (Bard, Claude, Copilot) to create line-based versions of Norton's translation.

## Evolution of Approach

### Prior Work: Fixed Tercet Blocks

Previous attempts (especially Bard) used a fixed approach:
- Process exactly 3 lines (one tercet) at a time
- Split Norton's prose to match each tercet
- Sometimes resulted in unnatural breaks

### New Approach: Variable-Length Blocks

**Key Insight:** Not all tercets map cleanly to English prose segments. Sometimes word order differences require multiple Italian lines to be treated as a single block.

**Example of word order inversion (lines 41-43):**

Italian order (lines 41→42→43):
```
41 sì ch'a bene sperar m'era cagione     (were occasion of good hope to me)
42 di quella fiera a la gaetta pelle      (concerning that wild beast)
43 l'ora del tempo e la dolce stagione    (the hour and the sweet season)
```

Norton's order (43→41→42):
```
so that the hour of the time and the sweet season
were occasion of good hope to me
concerning that wild beast with the dappled skin.
```

**Solution:** Treat lines 41-43 as a single block. Insert line breaks only at block boundaries, not within blocks where word order differs.

## Implementation Strategy

### Core Principle: "Line Breaks Only" Alignment

**Goal:** Divide Norton's prose into blocks that correspond to variable-length groups of Italian lines, inserting line breaks at block boundaries only. No word reordering within blocks.

**Why Variable Length:**
- Some Italian lines map directly to prose segments (1 line = 1 block)
- Other groups of lines have internal word order differences and must stay together (N lines = 1 block)
- Block size determined by word order correspondence, not fixed tercet structure

### Algorithm Overview

**Input Data:**
1. `tokenize/inferno/01.txt` - Italian lines with tokenized words (pipe-separated)
   - Format: `original line|token1|token2|token3|...`
   - No need to read `it/inferno/01.txt` separately
2. `en-norton/inferno/01.txt` - Norton prose, parsed with `dante_norton.Canto`
   - Separates text from annotations
   - Provides sections (paragraphs)

**Key Assumption (to verify experimentally):**
- Word order inversions occur within Norton paragraphs, not across them
- Therefore, process one Norton paragraph at a time for efficiency

**Processing Flow:**
```
For each Norton paragraph:
    Italian_block = []

    For each Italian line (until paragraph exhausted):
        Add line to Italian_block

        For each token in current line:
            Query LLM: "What English word corresponds to this Italian token?"
            Collect English word

        At line end:
            Perform matching check (see below)

            If match succeeds:
                Output Italian_block with corresponding Norton text
                Start new block
            Else:
                Continue to next Italian line (expand block)
```

## Technical Approach

### Detailed Matching Algorithm

**Objective:** Determine if Italian lines processed so far have been fully matched from the start of the current Norton paragraph.

**Method: Sequential Word Replacement with "#" Markers**

```python
# Given:
italian_current_lines = ["Nel mezzo del cammin di nostra vita", ...]
norton_current_paragraph = "Midway upon the road of our life I found..."
matched_words = ["Midway", "upon", "the", "road", "of", "our", "life"]  # from LLM

# Step 1: Create a test copy of Norton text
test_text = norton_current_paragraph

# Step 2: Replace each matched word with "#" characters (same length)
#         Use replace(word, replacement, 1) to replace only first occurrence
for word in matched_words:
    test_text = test_text.replace(word, "#" * len(word), 1)

# Example result:
# Original: "Midway upon the road of our life I found myself..."
# After:    "####### #### ### #### ## ### #### I found myself..."

# Step 3: Find the last "#" position
last_hash_index = test_text.rfind("#")
if last_hash_index == -1:
    # No matches found - error condition
    continue

# Step 4: Extract prefix (from start to last "#")
prefix = test_text[:last_hash_index + 1]

# Step 5: Check if prefix contains any alphabetic characters
import re
if not re.search(r'[a-zA-Z]', prefix):
    # SUCCESS: All words from start have been matched
    # Block is complete, insert line break
    output_block(italian_current_lines, norton_matched_portion)
    start_new_block()
else:
    # CONTINUE: Alphabetic characters remain in prefix
    # This means gaps in matching (words not yet seen)
    # Add next Italian line to block and continue
    continue_block()
```

### Visual Example: Successful Match

```
Italian line 1: "Nel mezzo del cammin di nostra vita"
Tokens: Nel, mezzo, del, cammin, di, nostra, vita
Matched: Midway, upon, the, road, of, our, life

Norton original: "Midway upon the road of our life I found myself..."
After replace:   "####### #### ### #### ## ### #### I found myself..."
Prefix:          "####### #### ### #### ## ### #### "
Has alphabet?    NO
Result:          ✓ Block complete - insert line break here
```

### Visual Example: Block Continuation (Lines 41-43)

```
After line 41:
Norton original: "so that the hour of the time and the sweet season were occasion of good hope to me..."
Matched 41:      ["occasion", "good", "hope", "me"]
After replace:   "so that the hour of the time and the sweet season were ######## of #### #### to ##..."
Prefix:          "so that the hour of the time and the sweet season were ######## of #### #### to "
Has alphabet?    YES ("so that", "the hour", etc. remain)
Result:          ✗ Continue - add line 42

After line 42:
Matched 41+42:   ["occasion", "good", "hope", "me", "wild", "beast", "dappled", "skin"]
After replace:   "so that the hour of the time and the sweet season were ######## of #### #### to ## concerning that #### ##### with the ####### ####..."
Has alphabet?    YES ("so that the hour..." remain)
Result:          ✗ Continue - add line 43

After line 43:
Matched 41+42+43: ["occasion", "good", "hope", "me", "wild", "beast", "dappled", "skin", "hour", "time", "sweet", "season"]
After replace:   "so that the #### of the #### and the ##### ###### were ######## of #### #### to ## concerning that #### ##### with the ####### ####..."
Prefix:          "## #### ### #### ## ### #### ### ### ##### ###### #### ######## ## #### #### ## ## ########## #### #### ##### #### ### ####### ####"
Has alphabet?    NO
Result:          ✓ Block complete - lines 41-43 form one block
```

## LLM Integration

### Context Management

**Per-token query to LLM:**
```
Prompt:
Italian line(s): {current_italian_block}
English translation: {current_norton_paragraph}

What English word(s) in the translation correspond to the Italian token "{token}"?
Return only the word(s), no explanation.
```

**Why only current paragraph:**
- Keeps context lightweight
- Prevents LLM errors from distant text
- Assumption: word order inversions don't cross paragraph boundaries

**LLM Choice:**
- Local LLM (Ollama recommended)
- Models to try: Llama 3.1/3.2, Qwen 2.5, Mistral
- Start simple, optimize later based on results

## Known Limitations & Future Adjustments

### 1:1 Word Correspondence Assumption

**Current simplification:**
- Assume each Italian token maps to one English word

**Known issues (to handle later):**
- Articles: `del` → `of the` (1→2)
- Contractions: `della` → `of the` (1→2)
- Relative pronouns: `che` → `who/which/that` or omitted (1→1 or 1→0)
- Apostrophes: `l'acqua` → `the water` (token structure differences)

**Approach:** Start with 1:1 assumption, observe failures, adjust algorithm based on real data patterns.

## Experimental Workflow

### Phase 1: Proof of Concept
1. Implement minimal script for Canto 1
2. Process first ~20 Italian lines
3. Observe:
   - LLM accuracy in word matching
   - Block boundary detection correctness
   - Cases where algorithm fails
4. Document findings

### Phase 2: Refinement
Based on Phase 1 results:
- Adjust prompt if LLM extraction is poor
- Handle common multi-word mappings if needed
- Verify assumption about paragraph boundaries
- Decide whether to continue to full Canto 1

### Phase 3: Scale (If Successful)
- Process remaining cantos of Inferno
- Consider Purgatorio and Paradiso
- Build automation pipeline

## Success Criteria

**Phase 1 Success:**
- ✓ Algorithm correctly identifies block boundaries for majority of lines
- ✓ Visual inspection confirms sensible line breaks
- ✓ No catastrophic failures (infinite loops, crashes)

**Phase 1 Acceptable Issues:**
- ○ Some word matches require manual correction
- ○ Edge cases with articles/pronouns
- ○ Need to adjust prompt or matching logic

**Phase 1 Failure (requires rethink):**
- ✗ LLM cannot reliably extract word correspondences
- ✗ Block detection produces nonsensical results
- ✗ Assumption about paragraph boundaries is wrong

## Next Steps

1. Write minimal Python script implementing the algorithm
2. Test on Canto 1, lines 1-20
3. Review output and document observations
4. Iterate or pivot based on results

---

**Note:** This is an experimental, iterative approach. The plan will evolve based on actual implementation results.
