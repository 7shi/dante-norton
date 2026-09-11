# Alignment Algorithm

## Overview

This document describes the current, recommended algorithm for aligning
Italian lines from Dante's *Inferno* with Charles Eliot Norton's English
prose translation: a three-stage pipeline that hierarchically decomposes
each Norton paragraph down to one row per Italian line.

```
align_ranges.py          align3.py                  align1.py
paragraph -> range   ->   paragraph -> tercet   ->   tercet -> line
(whole canto,              (word-rearranging          (word-rearranging
 1 LLM call)                 split, tercet-sized)        split, per-line)
```

For the earlier, superseded single-stage extraction algorithm
(`align_canto.py`) and the extraction-based two-stage `align3.py` it was
replaced by, see [MEMO.md](MEMO.md) - the historical algorithm summary near
the top, and "Two-stage, tercet-first alignment" further down.

## Core Challenge

Italian terza rima poetry and English prose have different structures:
- Italian: line-based, with fixed meter and rhyme scheme.
- English (Norton): paragraph-based prose with natural sentence flow, whose
  word order does not always follow the Italian line-by-line - Norton
  sometimes reorders content relative to an Italian hyperbaton (e.g. moving
  a delayed subject earlier for readability).

The goal is one Norton fragment per Italian line, using Norton's own words
throughout (no re-translation).

### Design rationale: rearrange, not extract

`align_canto.py` and the original `align3.py` both worked by *extraction*:
find the Norton span whose meaning matches a block of Italian lines, and
require it to be a verbatim, contiguous prefix of the remaining text. This
makes correctness mechanically checkable, but it structurally cannot
reproduce a genuine Italian hyperbaton: if Norton's own word order doesn't
follow the Italian line order, no contiguous span can be attributed
correctly to individual lines. Canto 1 lines 41-43 is the standing example
(see MEMO.md "Comparison against the fixed gold reference"): the Italian
subject (`l'ora del tempo e la dolce stagione`, line 43) is *postposed*
relative to its verb clause (lines 41-42), but Norton's English states the
subject first ("so that **the hour of the time and the sweet season**
were occasion of good hope to me concerning that wild beast..."). Under
extraction, this content can only be attributed as one merged span - it
cannot be split at Italian line boundaries without moving words.

This pipeline instead never extracts or verbatim-matches: each stage takes
a Norton text already known (by construction) to correspond to N Italian
lines, and asks the LLM to *split it into exactly N fragments, rearranging
words freely but never adding, removing, or substituting any of them*.
Validation is the case-insensitive word multiset of the concatenated
fragments equaling that of the input - order-independent, so a genuine
reordering (moving "the hour of the time and the sweet season" to a
different fragment) validates just as well as a simple cut. This mirrors
the original `dante-la-el` project's Bard-based process (see
[PRIOR_WORK.md](../PRIOR_WORK.md)) - the same idea previous experiments
tried - but where that project's ad hoc reordering "required heavy manual
correction and differed per AI system used" (MEMO.md), this pipeline adds
two things that made it work reliably with `openai:gpt-5.6-terra`: the
paragraph's tercet boundaries are fixed in advance (`align_ranges.py`), so
each split only has to solve one already-scoped sub-problem, and the model
is prompted with the Italian side *renumbered to match the requested
output* (see "Numbered-line output" below) rather than left to infer the
split points itself.

## Stage 1: `align_ranges.py` (paragraph -> Italian line range)

One LLM call over the *entire* canto: given the full numbered Italian text
and the full set of Norton paragraphs (summary paragraph excluded, numbered
the same way throughout the pipeline), the model returns one
`{paragraph, start_line, end_line}` entry per paragraph - no text
extraction, just a line-range correspondence.

**Validation** (`validate_ranges`, purely mechanical): paragraph numbers
must match the expected sequence; ranges must be contiguous and in order;
the first must start at line 1; the last must end at the canto's total line
count. Retried up to `MAX_ATTEMPTS` (3) on failure.

**Result (Canto 1, `openai:gpt-5.6-terra`):** all 6 paragraph ranges exact
on the first attempt, matching a ground truth derived independently from
`01-3.txt` (see MEMO.md "Whole-canto paragraph-range identification").
Because this has been verified reliable, `run_models.sh` treats its output
as a fixed input rather than regenerating it on every run - see "Files and
running the pipeline" below.

## Stage 2: `align3.py` (paragraph -> tercet-sized group)

For each paragraph (using the line range from stage 1), its Italian lines
are chunked into consecutive groups of `--block-size` (default 3, i.e. a
tercet); the final group may be shorter when the paragraph's line count
isn't a multiple of the block size. The whole paragraph's Norton text is
then split into one fragment per group via `split_norton_span` (see below).

A paragraph whose split never validates (`MAX_ATTEMPTS` retries exhausted)
is kept as a single merged row spanning its whole line range, rather than
dropping text.

## Stage 3: `align1.py` (tercet -> per-line)

Reads stage 2's group TSV and further splits any group spanning more than
one Italian line into one fragment per line, via the *same*
`split_norton_span` function - each Italian line is simply passed as its
own one-line group. A group whose split never validates is kept merged
(multiple Italian lines joined by `|`), same convention as stage 2.

## `split_norton_span`: the shared rearranging-split primitive

Both stage 2 and stage 3 call the same function in `align3.py`, generic
over the group size (one Italian line, or several):

```python
def split_norton_span(llm, italian_groups: List[List[ItalianLine]],
                      matched_text: str, start_num: int = 1) -> List[str] | None
```

### Numbered-line output, not structured JSON

Earlier versions of this pipeline requested structured JSON output
(`{"lines": [...]}`). This was replaced with plain numbered-line text: the
Italian side is shown renumbered by group (not by original per-paragraph
line number), and the model is asked to return exactly that many lines,
each starting with its group's number:

```
Italian line groups:
1 Nel mezzo del cammin di nostra vita|mi ritrovai per una selva oscura,|ché la diritta via era smarrita.
2 Ahi quanto a dir qual era è cosa dura|esta selva selvaggia e aspra e forte|che nel pensier rinova la paura!
3 Tant' è amara che poco è più morte;|ma per trattar del ben ch'i' vi trovai,|dirò de l'altre cose ch'i' v'ho scorte.
```

The requested output mirrors this 1:1 (`"1 <fragment>"`, `"2 <fragment>"`,
...). Making the target grouping explicit through matching numbers - rather
than leaving the model to infer split points from prose instructions alone
- was the change that got a real word move to happen reliably: with the
old JSON-schema prompt, `openai:gpt-5.6-terra` repeatedly failed to move
"the hour of the time and the sweet season" out of its original position
for the lines 41-43 hyperbaton (a "different but still contiguous" cut
point instead); with numbered-line prompting, it produced the fully correct
split on the first real run - see "Results" below.

`parse_numbered_lines` parses the response back: it requires the numbers
found to form exactly `start_num..start_num+n-1`, in order, with no gaps,
duplicates, or extra lines - any deviation returns `None` and the whole
attempt is retried, rather than trying to recover a partial match.

### Serial numbering (`start_num`)

Numbering does not reset to 1 for every call - callers thread a running
serial number through so cross-call log output stays traceable and,
qualitatively, so the model always sees a number matching the item's actual
position rather than a repeating 1..3:
- **`align3.py`** maintains a canto-wide running counter across paragraphs
  (paragraph 2's groups are numbered 1-9, paragraph 3's continue at 10-12,
  etc. for Canto 1), incremented by each paragraph's group count regardless
  of whether a split call was actually made.
- **`align1.py`** needs no separate counter: each group is a single Italian
  line, so its own real (canto-wide) `line_num` is used directly as
  `start_num`.

### Validation

1. Response parses into exactly `n` numbered lines (`parse_numbered_lines`).
2. No fragment is empty or whitespace-only (would otherwise contribute zero
   words to the check below and pass vacuously).
3. The case-insensitive word multiset (`\w+` tokens) of the concatenated
   fragments equals that of the input text exactly - nothing added,
   dropped, or substituted; only reordered and re-split.

Up to `MAX_ATTEMPTS` (3) retries on any failure; if none validates, the
caller keeps the whole span as one merged row (never drops content).

### No automatic punctuation restoration

An earlier version added a mechanical post-split punctuation-restoration
pass (reattaching sentence punctuation dropped at a fragment boundary,
based on searching the source text). It was removed: after investigation
(comparing several boundary "mismatches" against the actual Italian and
Norton source text - see conversation history / commit log around this
change), most apparent punctuation problems turned out not to be pipeline
bugs at all (one was gold itself carrying an edited period absent from
Norton's actual source; em-dash placement across two words fused with no
surrounding space is inherently ambiguous either way), and the mechanism's
own duplicate-detection logic was a source of a real bug during
development. Punctuation is left exactly as the model produces it; the
word-multiset check remains the only mechanical guarantee.

## Output Format

Every stage writes a log (`-o/--output`) and a companion TSV alongside it -
same base name, `.tsv` extension (e.g. `inferno-01-3.log` /
`inferno-01-3.tsv`), not `<name>.log.tsv`.

- **`align_ranges.py`**: `paragraph<TAB>start_line<TAB>end_line`, one row
  per Norton paragraph.
- **`align3.py`**: `italian_lines<TAB>norton_text`, one row per tercet-sized
  group; `italian_lines` is `|`-joined when the group spans more than one
  Italian line (always for a merge fallback; possible for the final partial
  group of a paragraph).
- **`align1.py`**: same shape, but one row per Italian line in the normal
  case (a `|`-joined multi-line row only where a group's split failed and
  was kept merged).

## Files and running the pipeline

```bash
# Stage 1 (once per canto - its ranges TSV is a fixed input to the rest)
uv run alignment/align_ranges.py 1 \
    -o alignment/output/inferno-01-ranges.log --model openai:gpt-5.6-terra

# Stages 2-3 together
alignment/run_models.sh 1
# equivalent to:
uv run alignment/align3.py 1 -i alignment/output/inferno-01-ranges.tsv \
    -o alignment/output/inferno-01-3.log --model openai:gpt-5.6-terra
uv run alignment/align1.py 1 -i alignment/output/inferno-01-3.tsv \
    -o alignment/output/inferno-01-1.log --model openai:gpt-5.6-terra
```

`run_models.sh` does not run stage 1 itself (see its header comment): the
ranges TSV is treated as a fixed, already-verified input, not something to
regenerate on every run. `--test` (stages 2/3 only; stage 1 makes a single
whole-canto call already) processes just the first paragraph/group, for a
quick local smoke test.

## Results (Canto 1, `openai:gpt-5.6-terra`)

- **Stage 1**: 6/6 paragraph ranges exact (see above).
- **Stage 2 vs. `01-3.txt`** (46-row tercet-level gold): 45/46 rows exact.
  The one remaining difference (rows 14-15, the lines 41-43 hyperbaton) has
  fully matching content and word order - the model *did* move "the hour of
  the time and the sweet season" into its own group, exactly as gold does;
  the only remaining difference is a punctuation/capitalization style
  choice at that internal boundary (comma+lowercase vs. period+capital),
  judged not worth normalizing away (see "No automatic punctuation
  restoration" above).
- **Stage 3**: 136/136 rows, 0 merged (every group split successfully on
  this run) - up from the previous run's 134/136 with 1 merged group.
  Spot-checked against `01-1.txt` (per-line gold): the same hyperbaton
  region (lines 42-44) matches content and word order exactly; remaining
  differences elsewhere are mostly quote-mark style (`"..."` vs `"…"`,
  unrelated to content) plus a few alternative but content-preserving
  word-order choices in dialogue-heavy passages, not evaluated further.

Only Canto 1 has been tested to date. Longer cantos, and stage 1's
robustness at larger scale, remain open (see MEMO.md "Open questions").

## Configuration

- **LLM model**: no default reflects a recommendation - benchmark model is
  `openai:gpt-5.6-terra` throughout (see MEMO.md's model-selection
  history); each script's `--model` flag defaults to `ollama:ministral-3:14b`
  only as a free local fallback.
- **Temperature**: 1.0.
- **Max attempts**: 3 per split/range-identification call.
- **Block size** (`align3.py --block-size`): 3 Italian lines (a tercet) by
  default.

## Limitations

1. Depends on LLM quality and instruction-following for the rearranging
   split - a weaker local model (`qwen3.6`) struggles with larger group
   counts in a single call (numbering drifts, duplicates/gaps appear) and
   falls back to merged rows more often; see stage 2/3's merge-fallback
   coverage in a given run's log.
2. Word-multiset validation cannot catch a split that is grammatically
   broken or attributes words to a plausible-but-wrong fragment while still
   using the same words in the same relative order - only content
   preservation is checked, not correctness of the split itself.
3. No punctuation-correctness guarantee (see "No automatic punctuation
   restoration" above) - a fragment boundary may carry punctuation that
   reads oddly, though content is never lost.
4. Stage 1's whole-canto single call is untested on cantos much longer than
   Canto 1's 136 lines; the original concern that motivated a possible
   segment-based fallback (see MEMO.md) has not been re-examined since.

## Future Improvements

1. Test the pipeline on more cantos, especially longer ones, to validate
   stage 1's whole-canto call at scale.
2. Consider a mechanical or LLM-based grammaticality check for split
   fragments, to catch cases like a fragment ending mid-clause on a
   conjunction (an actual failure mode observed once during development,
   from a weaker/unlucky sample - see conversation history).
3. Re-evaluate whether normalizing punctuation/capitalization at a
   rearranged boundary (comma+lowercase vs. period+capital, e.g.) is worth
   revisiting with a narrower, better-tested mechanism than the one removed
   during development.

## References

- [MEMO.md](MEMO.md) - full experimental history: model comparisons,
  the superseded `align_canto.py` algorithm (condensed), the two-stage
  extraction-based `align3.py` and its gold-comparison results, whole-canto
  range identification testing, and this pipeline's development notes.
- [PRIOR_WORK.md](../PRIOR_WORK.md) - the original `dante-la-el` /
  Bard-based tercet-then-line reordering process this pipeline mirrors.
- [ISLAND_FIX.md](ISLAND_FIX.md) - island/search-window design notes for
  the superseded `align_canto.py`.
