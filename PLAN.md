# Implementation Plan

Based on analysis of [prior work](PRIOR_WORK.md) and manual simulation.

## Core Approach

### Variable-Length Blocks

**Key Insight:** Italian and English word order differs. Insert line breaks only at block boundaries where word order allows natural alignment.

**Example (lines 41-43):**
- Italian order: 41→42→43 (hope/me, beast, hour/season)
- Norton order: 43→41→42 (hour/season, hope/me, beast)
- **Solution:** Treat as single 3-line block

### Data Sources

1. `tokenize/inferno/01.txt` - Italian with tokens: `line|token1|token2|...`
2. `en-norton/inferno/01.txt` - Norton prose (parsed via `dante_norton.Canto`)

## Algorithm

### Processing Flow

```
For each Norton paragraph:
    italian_block = []

    For each Italian line:
        Add line to italian_block
        Query LLM for word correspondences

        Check if block complete:
            - Replace matched words with "#" in Norton text
            - If prefix (start to last #) has no letters → complete
            - Else → continue to next line
```

### Matching Check (Core Logic)

```python
# Step 1: Replace matched words with "#"
test_text = norton_paragraph
for word in matched_words:
    test_text = test_text.replace(word, "#" * len(word), 1)

# Step 2: Check prefix
last_hash = test_text.rfind("#")
prefix = test_text[:last_hash + 1]

# Step 3: Block complete if no alphabetic chars in prefix
if not re.search(r'[a-zA-Z]', prefix):
    # Block complete - insert line break
else:
    # Continue - add next Italian line
```

## Manual Simulation Results

### Test: Line 1

**Italian:** `Nel mezzo del cammin di nostra vita`

**Norton:** `Midway upon the road of our life I found myself within a dark wood...`

**Critical Discovery - Multi-word mappings:**
- `Nel mezzo` (2 tokens) → `Midway` (1 word)
- `del` (1 token) → `upon the` (2 words) - Norton's interpretation, not literal "of the"

### Gap Detection Problem

With incorrect literal matching, we get "islands":

```
"####### #### ### #### ## ### #### I found myself ###### a dark wood..."
 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^    ^^^^^^^^^^^^^^ ^^^^^^
 Matched at start                  Gap            Island (error!)
```

**Gap = error signal.** Matched words should be continuous from the start.

**Gap detection function:**
```python
def has_gap(text):
    # Find first alphabetic character
    first_alpha = next((i for i, c in enumerate(text) if c.isalpha()), None)
    if first_alpha is None:
        return False
    # Check if "#" appears after it (= island)
    return '#' in text[first_alpha:]
```

### With Correct Matching

Using correct correspondences (Nel mezzo→Midway, del→upon the):
```
Matched: ["Midway", "upon", "the", "road", "of", "our", "life"]
Result:  "####### #### ### #### ## ### #### I found myself..."
Prefix:  "####### #### ### #### ## ### ####"
Alphabetic? NO
✓ Block complete after line 1
```

### Key Insight

**Goal: Correct line breaks, not perfect word matching.**

Implications:
- Imperfect word matching tolerable if block boundaries correct
- Can fallback to block-level validation when word-level fails

## Implementation Strategy

### Hybrid Approach with Fallback

**Primary: Word-level matching**
```
For each token, ask LLM:
"Italian: {block} / English: {paragraph}
 What English word(s) correspond to '{token}'? (may be multiple words)"
```

**Fallback: Block-level validation**

If gap detected or word-level unreliable:
```
Ask LLM:
"Italian lines: {lines}
 English text: {paragraph}
 Does English contain COMPLETE translation of Italian from paragraph start?
 Answer: YES or NO"
```

**Strategy:**
1. Try word-level matching
2. Detect gaps → retry with multi-word prompt
3. Still has gaps → fallback to block validation
4. Log which method succeeded

### LLM Configuration

- Local LLM via Ollama (Llama 3.1/3.2, Qwen 2.5, Mistral)
- Process one paragraph at a time (keep context light)
- Assumption: Word order inversions don't cross paragraphs

## Success Criteria

**Phase 1 (20 lines):**
- ✓ Block boundaries identified (either method)
- ✓ Sensible line breaks on visual inspection
- ✓ No crashes/infinite loops

**Acceptable:**
- Some blocks need fallback validation
- Gaps detected frequently (improve prompts later)

**Concerns requiring adjustment:**
- Block validation also fails
- Paragraph assumption breaks

## Implementation Results

### Current Implementation (alignment/align_canto.py)

**Approach:** Line-based extraction with structured output and LLM self-validation

**Core Components:**

1. **Structured Output Schemas (Pydantic):**
   - `ExtractionResult`: Forces LLM to output clean English text (no extra quotes/formatting)
   - `ValidationResult`: Forces structured validation (status: CORRECT/EXTRA_IN_ENGLISH/EXTRA_IN_ITALIAN, reason)
   - Uses `llm7shi.create_json_descriptions_prompt` for Ollama compatibility

2. **Extraction Phase:**
   - Input: Italian lines + Norton English paragraph
   - LLM extracts corresponding English text
   - Structured output ensures clean result without extra formatting

3. **Validation Phase (LLM Self-Validation):**
   - Separate LLM call validates Italian-English correspondence
   - Question: "Is the English a valid translation of the Italian?"
   - Emphasizes: Literary translation (meaning equivalence, not word-for-word)
   - Evaluation criteria:
     - `CORRECT`: English matches exactly (no more, no less)
     - `EXTRA_IN_ENGLISH`: English is TOO LONG (includes content from other Italian lines)
     - `EXTRA_IN_ITALIAN`: English is TOO SHORT (missing Italian content)

4. **Retry Logic (max 3 attempts):**
   - `EXTRA_IN_ENGLISH`: Clear history, re-extract with emphasis on SHORTER text
   - `EXTRA_IN_ITALIAN`: Clear history, retry with original prompt
   - Always re-extract from original Norton text (not from LLM's previous output)

5. **Position Verification:**
   - Check if extracted text appears at beginning of remaining Norton text
   - Allows minor punctuation differences (normalized comparison)

6. **Gap Detection & Block Completion:**
   - Replace matched words with "#"
   - Check prefix for alphabetic characters
   - Gap detected → Continue to next Italian line
   - No gap → Block complete

### Test Results

#### Test 1: Lines 1-3 (Initial Success)

**Input:** 3 Italian lines
**Output:** 2 blocks ✓

```
Block 1 (1 line):
  Italian: Nel mezzo del cammin di nostra vita
  English: Midway upon the road of our life

Block 2 (2 lines):
  Italian: mi ritrovai per una selva oscura,
          ché la diritta via era smarrita.
  English: I found myself within a dark wood, for the right way had been missed
```

**Key Success:**
- Variable-length blocks working correctly
- Line 2-3 correctly merged (Italian 2 lines = English 1 sentence)
- Validation successfully detected and corrected over-extraction in Line 2

#### Test 2: Lines 1-9 (Challenges)

**Issue:** Line 1 over-extraction persists across retries
- Extraction: `Midway upon the road of our life I found myself within a dark wood`
- Validation correctly detects: `EXTRA_IN_ENGLISH`
- Retry attempts produce same over-extraction
- Fails after 3 attempts

**Root Cause:** Small model (ministral-3:14b) has difficulty with fine-grained adjustments

### Current Status

**Working:**
- ✓ Structured output eliminates formatting issues (no extra quotes)
- ✓ LLM self-validation accurately detects over/under-extraction
- ✓ Variable-length blocks (multiple Italian lines → one English sentence)
- ✓ Gap detection prevents premature block completion
- ✓ Clean JSON-based validation logs

**Challenges:**
- ✗ Small model (14B) struggles with retry refinement
- ✗ When over-extraction occurs, retries often produce same result
- ✗ Line 1 over-extraction prevents processing of subsequent lines

**Model Limitations (ministral-3:14b):**
- Good at: Detecting problems (validation)
- Weak at: Refining extractions based on feedback
- Temperature: 0.3 (further reduction may help)

### Lessons Learned

1. **Structured output is essential:**
   - Eliminates parsing errors from free-form text
   - Forces consistent JSON format
   - Works well with Ollama via `create_json_descriptions_prompt`

2. **LLM self-validation works:**
   - Can accurately judge over/under-extraction
   - Concrete examples in prompts are critical
   - Simple rules ("TOO LONG" / "TOO SHORT") more effective than abstract concepts

3. **Small models have limits:**
   - Can validate well but struggle with fine corrections
   - May need larger model (70B+) for reliable retry success
   - Or need different retry strategy (not just "make it shorter")

4. **Re-extraction must use original source:**
   - Always extract from original Norton text
   - Never extract from LLM's previous output
   - Clear history on each retry to avoid confusion

### Next Steps

1. **Test with larger model:**
   - Try Qwen 2.5 70B or similar
   - May provide better retry refinement

2. **Alternative retry strategies:**
   - Provide specific word count limits
   - Show negative examples in retry prompt
   - Use different temperature for retries

3. **Fallback mechanism:**
   - If extraction fails after 3 attempts, try block-level validation
   - Or allow partial success and continue with warnings

4. **Test success rate:**
   - Run full canto with current approach
   - Measure: % of lines that succeed on first try, % that need retries, % that fail

---

## Improved Implementation (2026-02-05)

### Key Changes

**1. Hybrid Approach: Plain Text Extraction + Structured Validation**
- **Extraction:** Removed structured output (JSON schema) → Plain text output
  - Rationale: Structured output reduces LLM flexibility for extraction tasks
  - LLM outputs text directly, parsed as-is
- **Validation:** Kept structured output (JSON schema) with simplified YES/NO
  - `reason` field first → Chain of Thought (CoT)
  - `answer` field second → Final judgment
  - Rationale: Validation needs structured decision-making

**2. Fixed Island Detection Algorithm**
- Previous implementation had inverted logic
- Correct implementation (per PLAN.md):
  ```python
  # Find first alphabetic character
  first_alpha_idx = next((i for i, c in enumerate(test_text) if c.isalpha()), None)
  if first_alpha_idx is None:
      return True  # No unmatched text, block complete
  # Check if "#" appears after first alpha (= island)
  has_island = '#' in test_text[first_alpha_idx:]
  return not has_island  # Complete if no island
  ```

**3. Enhanced Prompts**
- **Extraction:** Added length hints based on Italian line count
- **Validation:** Emphasized literary translation tolerance
  - Minor nuance differences acceptable
  - Focus on content equivalence, not word-for-word matching

### Test Results (Lines 1-9)

**Success (Lines 1-3):**
- Line 1: "Nel mezzo del cammin di nostra vita" → "Midway upon the road of our life" ✓
- Line 2: "mi ritrovai per una selva oscura," → "I found myself within a dark wood" ✓
- Line 3: "ché la diritta via era smarrita." → "for the right way had been missed" ✓
- All succeeded on first attempt, no retries needed

**Partial Success (Lines 4-5):**
- Challenge: Lines 4-5 form a single English sentence
  - Italian: "Ahi quanto a dir qual era è cosa dura / esta selva selvaggia e aspra e forte"
  - English: "Ah! how hard a thing it is to tell what this wild and rough and dense wood was"
- Issue: Line 4 extraction includes Line 5 content
  - Extracted (Line 4 only): "Ah! how hard a thing it is to tell what this wild and rough and dense wood was"
  - Validation: YES (incorrect - should detect Line 5 content)
  - Length: IT=9 words, EN=18 words (2x ratio)
  - Island detection: False → Block marked complete (incorrect)
- Line 5 then fails (remaining text doesn't match)

### Current Challenge

**Validation Accuracy:**
- LLM validation accepts extractions that are too long
- Example: 18-word English for 9-word Italian
- LLM reasoning: "acceptable in literary translation"
- Need: More strict length validation or explicit multi-line detection

**Root Cause:**
- Italian poetry uses enjambment (lines break mid-sentence)
- Line 4 alone: "Ahi quanto a dir qual era è cosa dura" (how hard to say what it was)
- Line 4 includes "what it was" which naturally expands to "what this wild... wood was"
- LLM sees this as semantically valid, not detecting that the expansion comes from Line 5

### Possible Solutions

**1. Length Ratio Check:**
- Add hard constraint: English/Italian word ratio > 2.0 → reject
- Risk: May reject legitimate literary expansions

**2. Stricter Validation Prompt:**
- Explicitly ask: "Does the English include descriptive details not in this Italian line?"
- Emphasize: "Line 4 alone" vs "Lines 4-5 together"

**3. Multi-line Lookahead:**
- When extraction seems too long, check if next Italian line is needed
- Compare extraction against Lines 4-5 together

**4. Accept Current Behavior:**
- Lines 1-3 work perfectly
- Lines 4-5 represent edge case (enjambment with content expansion)
- May need human review for such cases

---

## Implementation Results (Updated)

### Current Implementation (alignment/align_canto.py)

**Approach:** Two-stage extraction with modern translation as semantic bridge

**Core Components:**

1. **Stage 1: Modern Translation**
   - Translate Italian to simple, modern English
   - Provides clear semantic reference independent of Norton's literary style
   - Single lines translated individually; multi-line blocks translated together

2. **Stage 2: Norton Text Extraction**
   - Use modern translation as meaning reference
   - Extract semantically equivalent text from Norton's literary translation
   - LLM matches meaning, not word-for-word

3. **Validation (Structured Output):**
   - Validates semantic equivalence between modern translation and extracted Norton text
   - Chain of Thought reasoning + YES/NO decision
   - Hard length ratio constraint (>1.8x rejects extraction)

4. **Enjambment Handling:**
   - When extraction fails with 1 line, automatically retry with N+1 lines
   - Successfully handles cases like Lines 4-5 where Italian poetry uses enjambment
   - Block expands until meaningful extraction succeeds

5. **Quote Stripping:**
   - Removes leading/trailing quotation marks from LLM output
   - Ensures clean text matching against Norton source

### Test Results (Lines 1-9)

**Success: 8 blocks from 9 lines**

```
Block 1 (1 line): Nel mezzo del cammin di nostra vita
                  → Midway upon the road of our life

Block 2 (1 line): mi ritrovai per una selva oscura
                  → I found myself within a dark wood

Block 3 (1 line): ché la diritta via era smarrita
                  → for the right way had been missed

Block 4 (2 lines): Ahi quanto a dir qual era è cosa dura / esta selva selvaggia e aspra e forte
                   → Ah! how hard a thing it is to tell what this wild and rough and dense wood was
                   [Enjambment detected: 2 Italian lines → 1 English sentence]

Block 5 (1 line): che nel pensier rinova la paura
                  → which in thought renews the fear

Block 6 (1 line): Tant' è amara che poco è più morte
                  → So bitter is it that death is little more

Block 7 (1 line): ma per trattar del ben ch'i' vi trovai
                  → But in order to treat of the good that there I found

Block 8 (1 line): dirò de l'altre cose ch'i' v'ho scorte
                  → I will tell of the other things that I have seen there
```

**Key Success:**
- Lines 1-3: Perfect individual line alignment
- Lines 4-5: Automatic enjambment detection and merging
- Lines 6-9: Correct individual alignments

### Current Status

**Working:**
- ✓ Two-stage approach (modern translation → Norton extraction)
- ✓ Semantic matching handles literary translation differences
- ✓ Automatic enjambment detection via retry mechanism
- ✓ Structured validation with length ratio guards
- ✓ Clean extraction without quotation mark artifacts

**Next Steps:**
1. Test with full canto (136 lines)
2. Measure success rate and identify edge cases
3. Optimize for speed (caching translations, parallel processing)
4. Add human review interface for uncertain alignments
