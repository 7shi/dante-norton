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

## Next Steps

1. Implement with gap detection + fallback
2. Test on lines 1-20
3. Log success method per block
4. Determine if word-level viable or use block-level as primary
5. Iterate

---

**Note:** Experimental approach. Plan evolves based on results.
