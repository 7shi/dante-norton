# Model Comparison for Alignment

Notes from testing `align_canto.py` against Inferno Canto 1 (136 Italian lines,
6 Norton paragraphs after the summary paragraph is skipped) with different LLM
backends, using the default direct-comparison mode (no `--translate`) and
temperature 1.0.

**These results are all from the current code**, which includes three fixes
made during this work: tolerant JSON parsing (`parse_json_object`, handling
Markdown code fences), rejecting empty extractions, and — on a block failure
— retrying against the same Norton paragraph instead of discarding its
remaining text and jumping to the next one (`find_matching_italian_line` /
resync-to-next-paragraph was removed as it's no longer reachable). See "Bug
history" below for what each fix addressed and which model exposed it.

## Results

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

## Observations

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
- Decide how to handle silently-skipped lines: leave gaps, retry them with a
  stronger model, or flag them explicitly in the output.
- Investigate why `gemma-4-31b-it` returns an empty `english` field so often
  — a prompt-wording issue, or a genuine model limitation.
