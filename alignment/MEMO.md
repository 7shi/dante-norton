# Model Comparison for Alignment

Notes from testing `align_canto.py` against Inferno Canto 1 (136 Italian lines,
6 Norton paragraphs after the summary paragraph is skipped) with different LLM
backends and temperature 1.0, in both the default direct-comparison mode and
`--translate` mode.

**These results are all from the current code**, which includes three fixes
made during this work: tolerant JSON parsing (`parse_json_object`, handling
Markdown code fences), rejecting empty extractions, and — on a block failure
— retrying against the same Norton paragraph instead of discarding its
remaining text and jumping to the next one (`find_matching_italian_line` /
resync-to-next-paragraph was removed as it's no longer reachable). See "Bug
history" below for what each fix addressed and which model exposed it.

## Results: direct comparison (default)

| Model | Coverage (lines in an output block) | Blocks | Failed after 3 attempts | Hallucination rejects | Empty-extraction rejects | Block exceeded (lines skipped) |
|---|---|---|---|---|---|---|
| `ollama:ministral-3:14b` | **64 / 136 (47%)** | 40 | 86 | 132 | 91 | 10 |
| `google:gemma-4-31b-it` | **92 / 136 (68%)** | 79 | 51 | 6 | 68 | 6 |
| `openai:gpt-5.6-luna` | **136 / 136 (100%)** | 129 | 7 | 8 | 0 | 0 |
| `openai:gpt-5.6-terra` | **136 / 136 (100%)** | 133 | 3 | 7 | 1 | 0 |

Coverage = total Italian lines that ended up in an output block, out of 136.
`italian_idx` reaches 136 in every run above (the tool no longer gets stuck
partway through), but for the two weaker models many lines along the way
produced no block at all ("Block exceeded" → skipped with no output). So
coverage, not `italian_idx`, is the meaningful completion metric now.

## Observations (direct comparison)

- The `gpt-5.6` models fully cover the canto with very few rejects.
  `gpt-5.6-terra` (reportedly the higher tier of the two) has fewer retries
  and hallucination rejects than `gpt-5.6-luna` in these single runs each,
  consistent with it being a step up, though the sample size is small.
- `google:gemma-4-31b-it` covers 68% of lines. Its dominant failure mode is
  empty extractions (68 rejects) rather than hallucination (6) — it often
  returns an empty `english` field rather than fabricating text.
- `ollama:ministral-3:14b` (local, 14B) is by far the weakest: only 47%
  coverage, with high rates of both hallucination (132) and empty
  extractions (91). It looks like a genuine capability gap for this task,
  not a remaining code issue.

## Results: `--translate` mode

Same runs with `--translate`: each query first asks the LLM to translate the
Italian into simple modern English, then extracts the matching Norton text
guided by that translation (the extraction prompt still includes the Italian
line as well). This adds one LLM call per query. Same conditions otherwise
(temperature 1.0, full canto).

| Model | Coverage (lines in an output block) | Blocks | Failed after 3 attempts | Hallucination rejects | Empty-extraction rejects | Block exceeded (lines skipped) |
|---|---|---|---|---|---|---|
| `ollama:ministral-3:14b` | **26 / 136 (19%)** | 23 | 98 | 217 | 85 | 15 |
| `google:gemma-4-31b-it` | **57 / 136 (42%)** | 48 | 77 | 34 | 153 | 11 |
| `openai:gpt-5.6-luna` | **136 / 136 (100%)** | 126 | 10 | 9 | 4 | 0 |
| `openai:gpt-5.6-terra` | **136 / 136 (100%)** | 131 | 5 | 8 | 2 | 0 |

Metrics counted the same way as the direct-comparison table (log line
pattern: "Failed after 3 attempts", "Hallucination", "Empty extraction",
"Block exceeded").

## Observations (`--translate` vs direct)

- Translate mode is worse for every model tested. Coverage drops for the
  two weaker models (ministral 47% → 19%, gemma 68% → 42%), and the
  `gpt-5.6` models, while still covering 100%, need more retries (luna
  7 → 10 failed blocks, terra 3 → 5) and now produce some empty extractions
  (luna 0 → 4, terra 1 → 2).
- Hallucination rejects increase substantially for the weaker models
  (gemma 6 → 34, ministral 132 → 217). Plausible cause: the modern-English
  paraphrase in the prompt pulls the model away from copying Norton
  verbatim, so it fabricates Norton-like text more often.
- Conclusion: direct comparison is strictly better for all four models.
  The extra translation step costs one more LLM call per query and provides
  no measurable benefit.

## Results: search-window mode (island fix, commit `82100e5`)

Finding 1 below (island detection is dead code) was fixed by replacing the
"must start at offset 0" prefix check with a search window: an extraction is
accepted if it starts within `--window-words` (default 20) words of the
current position, and a match starting past offset 0 now genuinely extends
the block ("island") instead of being silently impossible. Re-run of Canto 1
in this mode, via `alignment/run_models.sh 1 window`:

| Model | Coverage | Blocks | Islands | Failed | Halluc | Empty | Window | Exceeded |
|---|---|---|---|---|---|---|---|---|
| `ollama:ministral-3:14b` | 31 / 136 (23%) | 16 | 6 | 99 | 169 | 127 | 8 | 15 |
| `google:gemma-4-31b-it` | **136 / 136 (100%)** | 131 | 2 | 3 | 1 | 1 | 5 | 0 |
| `openai:gpt-5.6-luna` | 136 / 136 (100%) | 131 | 0 | 5 | 10 | 3 | 0 | 0 |
| `openai:gpt-5.6-terra` | 136 / 136 (100%) | 131 | 0 | 5 | 9 | 0 | 3 | 0 |

Islands = span accepted past offset 0 (block extended by one line). Window =
span rejected for starting too far in (`--window-words`). Exceeded = block
skipped after `MAX_BLOCK_LINES` with no output. Compare against the direct
comparison baseline above (same metrics, "Not at beginning" is this table's
predecessor of Islands/Window).

Accepted-offset distribution across all four logs (`grep -ho 'offset: [0-9]*
words' alignment/output/canto_01-*-window.log | sort -n -k2 | uniq -c`):
409 at 0 words, 2 at 1, 5 at 7, 1 at 11 — nearly all accepted spans start
within a few words of the current position, so the default window of 20 is
comfortably wide enough and was not tightened.

Per-model breakdown of the non-zero offsets: gemma4 1 at 1 word, 1 at 11
(its 2 islands); ministral 1 at 1, 5 at 7 (its 6 islands); luna/terra none.
The largest accepted offset (11, gemma4) leaves ~45% headroom under the
20-word cap, so the window is not admitting borderline-wide matches.
Narrowing the window would not have prevented ministral's regression below
either — all 5 of its problem islands sit at offset 7, well inside even an
8-10 word window, while tightening that far would also cut gemma4's
offset-11 island (the case this change was built to fix). The window width
is not the lever for the ministral problem; see the hallucination-driven
block-growth explanation above.

### Observations (search-window)

- **`gemma-4-31b-it` hits the target this change was built for**: coverage
  92/136 (68%) → 136/136 (100%), and the Window column (5) is far below the
  old "Not at beginning" count (77) it replaces. Its 77 old rejects were
  genuinely unsatisfiable-prefix cases (Finding 2), and the search window
  resolves almost all of them as islands or small in-window matches instead
  of rejects.
- **`gpt-5.6-luna` / `gpt-5.6-terra`** hold 100% coverage. luna's Failed
  count improves (7 → 5). terra's rises slightly (3 → 5) — a small,
  unexplained regression, not yet investigated.
- **`ministral-3:14b` regresses badly**: coverage 64/136 (47%) → 31/136
  (23%), and Exceeded rises 10 → 15 — the one metric the design explicitly
  treats as a red flag (rejects converted into oversized, fully-skipped
  blocks rather than genuinely resolved). Log inspection shows why: once an
  island extends a block for this model, the extended, longer span is more
  often hallucinated than reproduced verbatim, so the block keeps growing
  and re-failing until `MAX_BLOCK_LINES` wipes out several contiguous lines
  at once — a failure mode islands make *worse* for a model whose dominant
  problem is hallucination, not word order. Judged a ministral capability
  ceiling rather than a tunable parameter (see [ISLAND_FIX.md](ISLAND_FIX.md)
  section 6 item 3); `ministral-3:14b` is dropped from `run_models.sh` and
  future benchmark runs.

## Structural analysis: why even top-tier models fail

The direct-comparison results hide a structural problem: even the strongest
model (terra, 100% coverage) needed retries, and mid-tier models fail in
ways that do not depend on model quality. Log analysis across all 8 runs
shows the root cause is the task formulation, not the models.

### Finding 1: island detection never fires (dead code)

`Island: True` occurs **0 times in all 8 full-canto logs**. Reason: an
accepted extraction is always a contiguous *prefix* of the remaining Norton
text (`norton_text.startswith(extracted)`, enforced at the end of
validation). Replacing the words of a contiguous prefix in order always
produces one contiguous `#` region at the start of the text, so the first
unmatched alphabetic character sits immediately after that region and no `#`
can appear after it — `is_block_complete()` therefore returns True on
*every* successful extraction. Block completion is decided entirely by
extraction acceptance; the island mechanism, designed to handle
Italian/English word-order divergence, is unreachable. The algorithm
degenerates to greedy line-by-line prefix consumption.

**Update:** fixed by the search-window change (commit `82100e5`); see
"Results: search-window mode" above.

### Finding 2: "Not at beginning" rejects are structural, not model failures

Reject reasons per attempt, direct mode:

| Model | Hallucination | Not at beginning | Length ratio |
|---|---|---|---|
| `google:gemma-4-31b-it` | 6 | **77** | 3 |
| `openai:gpt-5.6-luna` | 8 | **11** | 4 |
| `openai:gpt-5.6-terra` | 7 | **0** | 3 |
| `ollama:ministral-3:14b` | 132 | 39 | 12 |

gemma's dominant failure is extracting verbatim Norton text that is *correct
in content but not the next contiguous chunk of English*: Norton's prose
word order cannot in general be partitioned into per-Italian-line contiguous
prefixes, so for any model there are queries whose correct answer is
unrepresentable under the current validation. These show up as model
rejections but are actually unsatisfiable-task failures. Strong models
compensate because translations are roughly monotonic at line granularity
(terra: 0 such rejects); weaker models cannot, and burn their retries there.

### Finding 3: "variable-length blocks" barely exist

Block size distribution (direct mode): terra 130/133 blocks are single-line,
luna 123/129, gemma 71/79, ministral 25/40. Multi-line blocks arise almost
only from the failure → add-a-line retry path, not from genuine enjambment
handling.

### Conclusion

The bottleneck is the formulation "each Italian line = a contiguous verbatim
prefix of the remaining Norton text". Model quality only changes how often
the model can compensate for it. Redesign directions, in rough order of
promise:

1. **LLM-free monotonic alignment**: align Italian lines to Norton sentence
   pieces with dynamic programming over cross-lingual sentence embeddings
   (e.g. LaBSE / multilingual-e5), Gale-Church style. Deterministic, cheap,
   and word-order divergence is handled by the similarity function instead
   of a prefix constraint.
2. **LLM as aligner, not extractor**: ask for word/phrase-level
   correspondence pairs (Italian tokens → Norton spans) for a whole
   paragraph at once, then validate monotonicity and span existence
   mechanically. The correct answer is always representable.
3. **Anchor-based hybrid**: first fix high-confidence anchors (proper nouns,
   numbers, embedding-nearest sentence pairs), then fill the gaps with
   approach 1 or 2.

## Comparison against the fixed gold reference (`01-1.txt`)

Finding 3 above shows the tool's block boundaries rarely match genuine
enjambment; this section checks whether that boundary ambiguity produces
*wrong* line attributions or just a different, equally-valid split, by
comparing the window-mode output (gemma4/luna/terra — ministral excluded,
see above) against `01-1.txt`, the independent 136-line gold reference (see
README.md "Reference Data"). Method: each output block's Italian column
gives the gold line range it should cover (all three runs have 100%
coverage, so ranges are contiguous and can be read off directly); the
block's extracted Norton text is then compared word-for-word against the
concatenation of the corresponding gold lines.

| Model | Blocks | Exact word-sequence match | Mismatched gold-line clusters |
|---|---|---|---|
| `google:gemma-4-31b-it` | 131 | 122/131 | 4 |
| `openai:gpt-5.6-luna` | 131 | 121/131 | 4 |
| `openai:gpt-5.6-terra` | 131 | 119/131 | 5 |

Grouping each model's mismatches into contiguous clusters and comparing the
merged word sequence against the same gold-line span shows every cluster has
an identical word multiset (nothing added, dropped, or substituted) — the
only difference is *where* the block boundary falls between two adjacent
gold lines. In all but one cluster per model, the word order is also
identical (`exact-order match=True`), meaning the mismatch is purely a
boundary shift: a phrase at the end of one gold line moved to the start of
the next block, or vice versa. Example (gemma4, gold lines 4-6):

```
gold:   line4="Ah! how hard a thing it is to tell what"
        line5-6="this wild and rough and dense wood was, which in thought renews the fear!"
output: block4="Ah! how hard a thing it is to tell what this wild and rough
         and dense wood was,"
        block5-6="which in thought renews the fear!"
```

Same words, same order, different split point — both are valid line
attributions for this enjambment.

**One cluster per model does not preserve word order**: gold lines 41-46
(gemma4/luna), 41-48 (terra). Inspecting the gold text there:

```
41 so that were occasion of good hope to me
42 concerning that wild beast with the dappled skin
43 the hour of the time and the sweet season.
44 But not so that did not give me fear
45 the sight which appeared to me of a lion.
```

`01-1.txt` was hand-built by *reordering* Norton's words per Italian line
(README.md "Reference Data") — here "the hour of the time and the sweet
season" (line 43) and "the sight which appeared to me of a lion" (line 45)
are moved out of Norton's original word order to align with the Italian's
hyperbaton. The tool never reorders words (by design — see ALGORITHM.md
"Design rationale"), so its output follows Norton's actual, unreordered word
sequence and necessarily diverges from gold exactly where gold's manual
reordering diverges from Norton's prose. This is the expected boundary
between what extraction-only alignment can and cannot reproduce, not a bug.

**Conclusion**: for these three models, none of the measured boundary
mismatches are wrong attributions in the sense of lost, duplicated, or
substituted content — every mismatch is either (a) an equally-valid
alternative split point (the common case), or (b) the known, designed-around
limit where gold's word-level reordering can't be matched by an extraction-
only algorithm (one localized region per model, all sharing the same
Italian hyperbaton). Finding 3's "block-boundary ambiguity" is confirmed to
be benign for this canto and these models.

## Two-stage, tercet-first alignment (`align3.py`)

The single mismatch cluster found in the gold comparison above (Italian
hyperbaton, gold lines 41-48) matches a structural gap identified in
"Structural analysis" #6: the extraction-only algorithm has no unit larger
than one Italian line, but the gold reference (`01-1.txt`) was itself built
in two stages (PRIOR_WORK.md): first segment Norton's prose at tercet (3-line)
granularity — `01-3.txt`, no word reordering — then, only within that fixed
span, rearrange words to match each individual Italian line. `align3.py`
implements the same two stages:

- **Stage 1 (coarse)**: extract a whole tercet's Norton span at once
  (`--block-size`, default 3), instead of growing from a single line.
  **No block-growth mechanism**: align_canto.py's island/window extension
  existed only to recover when one line was too little context; a 3-line
  chunk is assumed to already give enough, so a chunk that fails to match
  at the exact start of the remaining text is skipped outright, never
  extended, and window/island logic was removed entirely along with it.
- **Stage 2 (decomposition)**: for a multi-line block, a second LLM call
  splits the now-fixed Norton span into one fragment per Italian line,
  rearranging words but never substituting them (mirrors the Bard prompt in
  PRIOR_WORK.md). Validated mechanically: fragment count must match, no
  fragment may be empty, and the concatenated fragments' word multiset must
  equal the input span's exactly. A block whose split never validates
  (`MAX_ATTEMPTS` tries) is kept merged (one row spanning all its Italian
  lines) rather than dropping text.

Run via `alignment/run_models.sh` (rewritten for this experiment): Canto 1,
four models — `qwen3.6` (local, via `ollama:qwen3.6`) added alongside
gemma4/luna/terra to test a second free local model against the three
already benchmarked.

### Results: stage 1 vs. `01-3.txt` (tercet-level gold, 46 rows, 1:1 by position)

| Model | Exact text match | Word-sequence match (punctuation-insensitive) |
|---|---|---|
| `ollama:qwen3.6` | 34-37/46 | 42-44/46 |
| `google:gemma-4-31b-it` | 34-35/46 | 44/46 |
| `openai:gpt-5.6-luna` | 32-33/46 | 44/46 |
| `openai:gpt-5.6-terra` | 32/46 | 44/46 |

(Ranges reflect two comparison runs before/after the stage-2 bugfix below;
stage 1 itself is unaffected by that fix, so the small variation is normal
run-to-run LLM variance, not a code change.) Every mismatch checked (merging
the two mismatched rows and comparing word multisets) preserves full word
content — boundary shifts only, same as the earlier extraction-only
comparison. gemma4/luna/terra's only mismatch is lines 13-14 (a *different*
boundary ambiguity from the 41-45 hyperbaton: "first set in motion those
beautiful things" — the tail of a relative clause — is attached to the
previous tercet by all three models but to the next one by gold, both
defensible splits of the Italian's line 39/40 boundary). qwen3.6 additionally
misattaches "—she caused me so much heaviness," across lines 17-18 the same
way. **Coarse, tercet-first extraction reproduces `01-3.txt` almost exactly**
— the 41-48 hyperbaton region that was the sole non-boundary-shift mismatch
in the fine-grained (extraction-only) comparison is resolved at this
granularity for every model.

### Bug found and fixed: empty stage-2 fragments accepted as valid

Initial stage-2 validation checked only that the concatenated fragments'
word multiset matched the input span — the same class of bug as the
empty-extraction bug already fixed in `align_canto.py` (see Bug history
below), just not ported to `align3.py`. An empty fragment contributes zero
words, so a split like `['', 'But not so that', 'the sight ... did not give
me fear.']` passed validation silently, producing a blank line-43 output for
gemma4/luna/terra instead of a real (if imperfect) attempt or an honest
merged fallback. Fixed by rejecting any attempt containing an empty (or
whitespace-only) fragment, forcing a real split attempt or, on repeated
failure, the existing merged-row fallback.

### Results: stage 2 vs. `01-1.txt` (fine-grained gold, after the fix)

| Model | Final rows | Exact word-sequence match |
|---|---|---|
| `ollama:qwen3.6` | 130/136 (6 rows still merged) | 107/130 |
| `google:gemma-4-31b-it` | 134/136 (2 rows still merged) | **132/134** |
| `openai:gpt-5.6-luna` | 134/136 (2 rows still merged) | 130/134 |
| `openai:gpt-5.6-terra` | 134/136 (2 rows still merged) | **132/134** |

For gemma4/luna/terra, the empty-fragment fix reduces the mismatch set to
**exactly the one hyperbaton cluster** (gold lines 41-45, still merged/
misattributed — the stage-1 boundary itself absorbs "the hour of the time
and the sweet season" into the wrong tercet, so no stage-2 split can recover
it; see "Comparison against the fixed gold reference" above for why). No
other mismatches remain for these three models — a marked improvement over
the extraction-only run's four mismatch clusters per model.

**qwen3.6 is a clear step down**: 107/130, with several mismatches that are
not simple boundary shifts — genuine word-order errors within a line (e.g.
"set first those beautiful things in motion" for gold's "first set in
motion those beautiful things") and misplaced fragments (e.g. "He answered
me:” relocated to the end of a later line instead of the start of its own).
Stage 1 (coarse extraction) was comparably good for qwen3.6, so the
weakness is specifically in stage 2's word-level reordering task, not in
finding the right Norton span. Not yet judged a hard capability ceiling
(unlike ministral in the extraction-only comparison) since coverage is
still high and most errors are local, but qwen3.6 needs more scrutiny before
being treated as equivalent to the three cloud models for this task.

### Conclusion

The two-stage, tercet-first, no-growth redesign (ALGORITHM.md "Future
Improvements" #6, prototyped here) resolves the one substantive block-
boundary problem the extraction-only algorithm had (the 41-48 hyperbaton
region) for every model except qwen3.6's stage-2 weakness, at the cost of
one still-open, equally-benign boundary ambiguity (lines 13-14, content-
preserving) and occasional merged (unsplit) rows where stage 2 cannot find
a valid split. This is a stronger result than the extraction-only window-mode
comparison and a reasonable basis for adopting `align3.py`'s approach going
forward, pending a decision on how to handle the remaining merged rows and
whether to keep qwen3.6 in the benchmark set.

**Decision: benchmark set narrowed to `terra` alone.** `qwen3.6` is dropped
from `run_models.sh` (its stage-2 word-order errors above, not just
boundary shifts, are judged the same kind of capability gap that excluded
`ministral-3:14b` earlier — see MEMO.md history and HANDOFF.md). `luna` is
dropped in favor of `terra`: across every table above the two `gpt-5.6`
tiers track each other closely, so running both adds little. `gemma4` is
also dropped: `terra` clearly outperforms it, and the goal from here is the
best achievable result, not a survey of local/weaker-model options — so
`terra` alone is kept as the benchmark model going forward.

## Bug history

Three bugs were found and fixed while testing the models above:

1. **Code-fence JSON parsing.** `gemma-4-31b-it` often wrapped its JSON
   response in a Markdown code fence, sometimes with only a closing ` ``` `
   and no opening one, which broke `json.loads` ("Extra data" error). Fixed
   by `parse_json_object()`, which strips a leading fence if present and
   uses `json.JSONDecoder().raw_decode` to parse just the JSON prefix,
   ignoring trailing text.
2. **Empty-extraction accepted as valid.** An empty extracted string always
   passed the length-ratio check (0 / N = 0.0) and `norton_text.startswith("")`
   (always `True` in Python), so it was wrongly accepted. First seen with
   `ministral-3:14b`. Fixed by explicitly rejecting an empty (or
   whitespace-only) extraction before the other checks.
3. **Paragraph abandoned on block failure.** When a block failed after
   accumulating `MAX_BLOCK_LINES` (6) lines, the code discarded the
   paragraph's remaining unmatched text entirely and jumped straight to the
   *next* Norton paragraph, silently dropping most of the current
   paragraph's content — this is what caused `gemma-4-31b-it` to reach only
   100/136 lines in an earlier run. The `find_matching_italian_line()`
   re-sync mechanism meant to recover from this almost never succeeded (0
   successes observed). Fixed by preserving the paragraph's remaining text
   on failure and retrying with the next Italian line(s) against it, instead
   of jumping ahead; the now-unreachable re-sync mechanism was removed.

   This fix's trade-off: a block that still fails after `MAX_BLOCK_LINES`
   lines is skipped with no output for those line(s) (as before), but the
   paragraph is no longer abandoned — so `italian_idx` can now reach 136
   even when many individual lines produced no block. This is why "coverage"
   (see Results) is now tracked separately from how far `italian_idx`
   advanced.

## Next steps

- Add per-line coverage as a first-class metric in the tool's own output,
  instead of computing it by hand from the log each time.
- Pick a redesign direction for the core algorithm (see "Structural
  analysis" above): embedding-based DP alignment, paragraph-level LLM
  correspondence extraction, or an anchor-based hybrid.
- Decide how to handle silently-skipped lines: leave gaps, retry them with a
  stronger model, or flag them explicitly in the output.
- Investigate why `gemma-4-31b-it` returns an empty `english` field so often
  — a prompt-wording issue, or a genuine model limitation.
- Decide what to do with `--translate`: it underperformed direct comparison
  with every model tested (see above). Candidates are removing the flag or
  keeping it as an experiment switch.
