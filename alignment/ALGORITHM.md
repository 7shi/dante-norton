# Alignment Algorithm

## Overview

This document describes the current, recommended algorithm for aligning
Italian lines from Dante's *Inferno* with Charles Eliot Norton's English
prose translation: a three-stage pipeline that hierarchically decomposes
each Norton paragraph down to one row per Italian line, implemented as a
single script, `align.py`.

```
Stage 1                   Stage 2                    Stage 3
paragraph -> range   ->   paragraph -> tercet   ->   tercet -> line
(whole canto,              (word-rearranging          (word-rearranging
 1 LLM call)                 split, tercet-sized)        split, per-line)
```

For the earlier, superseded single-stage extraction algorithm
(`align_canto.py`) and the extraction-based two-stage `align3.py` it was
replaced by, see [MEMO.md](MEMO.md) - the historical algorithm summary near
the top, and "Two-stage, tercet-first alignment" further down. (An
intermediate, three-script version of the current algorithm -
`align_ranges.py`, `align3.py`, `align1.py`, run as three separate commands
- has since been unified into `align.py`; the stage boundaries and logic
below are unchanged, only the packaging.)

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
paragraph's tercet boundaries are fixed in advance (stage 1), so
each split only has to solve one already-scoped sub-problem, and the model
is prompted with the Italian side *renumbered to match the requested
output* (see "Numbered-line output" below) rather than left to infer the
split points itself.

## Stage 1: paragraph -> Italian line range

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
the former `01-3.txt` (see MEMO.md "Whole-canto paragraph-range
identification").

## Stage 2: paragraph -> tercet-sized group

For each paragraph (using the line range from stage 1), its Italian lines
are chunked into consecutive groups of `--block-size` (default 3, i.e. a
tercet); the final group may be shorter when the paragraph's line count
isn't a multiple of the block size. The whole paragraph's Norton text is
then split into one fragment per group via `split_norton_span` (see below).

A paragraph whose split never validates (`MAX_ATTEMPTS` retries exhausted)
is skipped: its rows stay blank in the stage's output file (the pending
marker) and the stage moves on to the next paragraph, so no merged
(multi-group) row is ever produced and every output line corresponds to
exactly one recomputable group. A rerun re-splits just the blank rows.

## Stage 3: tercet -> per-line

Takes stage 2's group rows and further splits any group spanning more than
one Italian line into one fragment per line, via the *same*
`split_norton_span` function - each Italian line is simply passed as its
own one-line group. A group whose split never validates is skipped (its
rows stay blank for a later run to retry), same convention as stage 2; a
group whose stage-2 row is still blank is skipped the same way, without an
LLM call.

## `split_norton_span`: the shared rearranging-split primitive

Both stage 2 and stage 3 call the same function in `align.py`, generic
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
- **Stage 2** maintains a canto-wide running counter across paragraphs
  (paragraph 2's groups are numbered 1-9, paragraph 3's continue at 10-12,
  etc. for Canto 1), incremented by each paragraph's group count regardless
  of whether a split call was actually made.
- **Stage 3** needs no separate counter: each group is a single Italian
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
caller leaves the rows blank and moves on rather than write a merged row
(content is never dropped - the split is simply retried from scratch on a
rerun, which only touches the blank rows).

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

`align.py` writes to `alignment/<canticle>/`, canto-number-prefixed, plus one
combined log for all three stages (e.g. Inferno Canto 1 ->
`alignment/inferno/01-*`, `alignment/inferno/01.log`). English only - no
Italian side is repeated in the tercet/line output files (see
[README.md](README.md) "Output" for details).

- **`<NN>-ranges.tsv`**: `paragraph<TAB>start_line<TAB>end_line`, one row
  per Norton paragraph (stage 1).
- **`<NN>-3.txt`**: one line of English text per tercet-sized group (stage
  2); the file may have fewer lines than the canto's tercet count when a
  paragraph's line range is not a multiple of the block size.
- **`<NN>-1.txt`**: same shape, one line per Italian line (stage 3).

Each stage is skipped (no LLM calls) if its output file already exists, and
loaded instead - see [README.md](README.md) "Resuming" for the mechanism
and how the per-line output files' loss of the Italian side is handled
(deterministic group recomputation, cross-checked against the loaded row
count). `<NN>.log` is unconditionally overwritten every run.

## Files and running the pipeline

```bash
uv run alignment/align.py inferno -c 1 --model openai:gpt-5.6-terra
```

`--test` processes just the first paragraph/group at each of stages 2 and
3, for a quick local smoke test (stage 1 always makes a single whole-canto
call). It only affects a stage that actually runs - a skipped (already
output-present) stage ignores it, since it loads the existing file rather
than reprocessing.

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
  history); `align.py`'s `--model` flag defaults to
  `ollama:ministral-3:14b` only as a free local fallback.
- **Temperature**: 1.0.
- **Thinking**: disabled by default (`--think` to enable) - a same-prompt
  comparison on Canto 1 found thinking made the lines 41-43 hyperbaton split
  worse, not better (see README.md "Thinking makes the rearranging split
  worse").
- **Max attempts**: 3 per split/range-identification call.
- **Block size** (`align.py --block-size`): 3 Italian lines (a tercet) by
  default.

## Limitations

1. Depends on LLM quality and instruction-following for the rearranging
   split - a weaker local model (`qwen3.6`) struggles with larger group
   counts in a single call (numbering drifts, duplicates/gaps appear) and
   fails validation more often, leaving blank rows behind; each split's
   retry outcomes are visible in the run's log.
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
