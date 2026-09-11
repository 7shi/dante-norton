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
