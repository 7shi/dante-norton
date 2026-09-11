# Alignment Scripts

Alignment scripts for the Italian original text and Norton's English translation.

## Overview

The current, recommended approach is `align.py`, a single script that runs a
three-stage pipeline hierarchically decomposing each Norton paragraph down
to one row per Italian line, never extracting or verbatim-matching text -
each stage only rearranges words already known to belong to a given span,
validated by word-multiset equality:

1. One LLM call over the whole canto identifies, for each Norton paragraph,
   the range of Italian lines it corresponds to.
2. Each paragraph's Norton text is split into tercet-sized (3-line, by
   default) groups.
3. Any multi-line group is further split into one fragment per Italian
   line.

See [ALGORITHM.md](ALGORITHM.md) for the full algorithm description and
Canto 1 results (near-exact match against both former fixed gold
references).

Two earlier, superseded approaches are documented but no longer present as
scripts:

- **`align_canto.py`** (single-stage, extraction-only, removed): for each
  Norton paragraph, Italian lines are added to a block one at a time and an
  LLM extracts the corresponding Norton span, with island detection via a
  search window to find block boundaries. See [MEMO.md](MEMO.md) (algorithm
  summary near the top, and "Design: island/search-window fix").
- The original **`align3.py`** (two-stage, extraction-based tercet-first,
  now replaced): stage 1 extracted a whole tercet at once with a fixed
  block size, stage 2 split that fixed span into one fragment per Italian
  line. See [MEMO.md](MEMO.md) "Two-stage, tercet-first alignment" for the
  design rationale and gold-comparison results that motivated moving away
  from extraction entirely.

A later, three-script version of the current approach (`align_ranges.py`,
`align3.py`, `align1.py`, run as three separate commands) has since been
unified into the single `align.py`; see git history for the three-script
sources.

[MEMO.md](MEMO.md) has the full experimental history across LLM backends.
`openai:gpt-5.6-terra` is the sole benchmark model going forward (see
MEMO.md's model-selection decision) - `ministral-3:14b`, `qwen3.6`,
`gemma-4-31b-it`, and `gpt-5.6-luna` were all tried and dropped for
documented reasons.

Two fixed gold references for Canto 1, `01-1.txt` (per-line) and `01-3.txt`
(per-tercet), were used during development - see "Reference Data" below.

## Usage

```bash
uv run alignment/align.py inferno -c 1 --model openai:gpt-5.6-terra
```

Positional argument: cantica name (`inferno`, `purgatorio`, or `paradiso`).
Options:

- `-c`/`--canto`: canto number (e.g. `-c 1`). Omit to run every canto of the
  cantica in turn (canto numbers taken from `dante-corpus`), in one process.
- `--model`: LLM model, passed through to `llm7shi`; use an `ollama:`,
  `google:`, or `openai:` prefix to select the backend. Cloud backends need
  the corresponding API key set in the environment.
- `--temperature` (default: 1.0)
- `--think`: enable LLM thinking (disabled by default - see "Thinking makes
  the rearranging split worse" below)
- `--block-size` (default: 3): number of Italian lines per stage-2 group
- `--test`: process only the first paragraph/group at each stage, for a
  quick local smoke test

### Thinking makes the rearranging split worse

`--think` defaults to off. A same-prompt comparison on Canto 1 with
`openai:gpt-5.6-terra` (ranges identical either way) found that enabling
thinking made the stage 2/3 rearranging split *less* faithful to the
Italian line structure at the hyperbaton case (Canto 1 lines 41-43, see
ALGORITHM.md "Design rationale"): with thinking on, the model misattributed
"the hour of the time and the sweet season" to the wrong clause entirely
(as if it were the subject of "set in motion those beautiful things",
which in the Italian is done by `l'amor divino`/Love Divine, not by the
hour and season) and additionally split "But" off into its own line,
detached from the Italian line it was supposed to translate. With thinking
off, the same phrase landed correctly on its own line, matching the
Italian's postposed-subject line exactly. Elsewhere the two runs were a
similar mix of minor word-order variance either way (expected at
temperature 1.0), but this one case was decisive enough to keep `--think`
opt-in rather than default.

### Removed: `align_canto.py` (single-stage, extraction-only)

`align_canto.py` (superseded, single-stage, extraction-only) has been
removed; see MEMO.md's algorithm summary and "Design: island/search-window
fix" for its design and the `--window-words` / `--strict-prefix` /
`--translate` flags it had, and git history for the source itself.

## Reference Data

`01-1.txt` was a fixed, manually-edited gold reference for Canto 1: Norton's
prose rearranged to exactly 136 lines (one per Italian line), preserving
Norton's wording while reordering words to match Dante's line structure.
`01-3.txt` was the intermediate, 46-line tercet-level version from the same
source (line breaks only, mostly no word reordering - two spots were
hand-corrected during this pipeline's development to reflect a genuine
Italian hyperbaton, see ALGORITHM.md "Design rationale"). Both were carried
over from the `dante-la-el` project's Bard experiments
(https://github.com/7shi/dante-la-el/tree/main/Inferno/Bard/en-norton) - see
[PRIOR_WORK.md](../PRIOR_WORK.md) for how they were produced (Bard's own
two-stage process: tercet-level segmentation, then per-line reordering with
no word substitution - the same two stages the current pipeline automates,
this time reliably) and [ALGORITHM.md](ALGORITHM.md) for the current
pipeline's results against both. Both files have since been removed from
this directory; they only informed development-time validation. `align.py`
now writes its own `<NN>-1.txt` / `<NN>-3.txt` in the same per-line /
per-tercet shape, as real pipeline output rather than hand-edited gold data.

## Output

`align.py` writes to `alignment/<cantica>/`, canto-number-prefixed (e.g.
Inferno Canto 1 -> `alignment/inferno/01-*`):

- **`<NN>-ranges.tsv`**: `paragraph<TAB>start_line<TAB>end_line`, one row
  per Norton paragraph (stage 1).
- **`<NN>-3.txt`**: one line of English (Norton) text per tercet-sized
  group (stage 2), comparable 1:1 by position against the former
  `01-3.txt`. English only - the Italian side is not repeated in this file
  (see `<NN>-ranges.tsv`, and `dante-corpus` for the Italian text itself).
- **`<NN>-1.txt`**: same shape, one line per Italian line in the normal
  case (stage 3), comparable against the former `01-1.txt`.
- **`<NN>.log`**: the full processing trace of all three stages
  (per-paragraph/group progress, retries, rejections), a final
  Italian+English listing per stage, and summary counts (total rows/groups,
  coverage). Unconditionally overwritten on every run (gitignored via the
  repo root `.gitignore`'s `*.log`).

A group or paragraph whose split never validates (after `MAX_ATTEMPTS`
retries) aborts the canto rather than writing a merged line - so
`<NN>-3.txt` / `<NN>-1.txt` never contain a row spanning multiple Italian
lines, and their row counts always match the deterministic recomputation
(a stage-2 file may still be shorter than the canto's line count when a
paragraph's range is not a multiple of `--block-size`). Delete the failing
stage's output file and rerun to retry the split.

Progress and errors are also printed to the console as the script runs, via
a live `StatusLine` progress bar - see [STATUSLINE.md](STATUSLINE.md) for
how that's wired.

### Resuming: each stage skips if its output file already exists

`align.py` checks each of the three output files before running that stage:
if `<NN>-ranges.tsv` / `<NN>-3.txt` / `<NN>-1.txt` is already present, that
stage is skipped (no LLM calls) and the file is loaded instead. This lets a
rerun pick up only the stages whose output is missing - e.g. after deleting
just `<NN>-1.txt` to retry stage 3 with a different model, without
re-spending stage 1/2's calls.

Because `<NN>-3.txt` and `<NN>-1.txt` carry no Italian side, a skipped
stage's Italian line groups are recomputed deterministically from the
ranges and `--block-size` and cross-checked against the loaded file's row
count; a mismatch (e.g. the file was produced with a different
`--block-size`, or by an older version that kept merged rows) aborts with
a message rather than silently misaligning - delete the file and rerun to
regenerate it. Delete any of the three files
to force that stage, and every stage after it whose input it feeds, to
rerun; deleting only a later-stage file (e.g. `<NN>-1.txt` while keeping
`<NN>-3.txt`) reruns just that stage.

## Requirements

- Python 3.13+ (see `pyproject.toml`)
- `dante_norton` library (parent directory)
- `llm7shi` (model access and progress display - see
  [STATUSLINE.md](STATUSLINE.md))
- An LLM backend: local (Ollama) or cloud (Gemini, OpenAI-compatible) with
  the relevant API key set

## Troubleshooting

### Splits failing / cantos aborting

Use [debug.py](debug.py) to investigate - its docstring is the step-by-step
playbook (`show` a paragraph against the Italian original, `check` the stage
files without the LLM, `words`-diff a failed response):

    uv run python alignment/debug.py show inferno 16 -p 10
    uv run python alignment/debug.py check inferno 16

The most common cause is a wrong stage-1 line range at a paragraph boundary
(e.g. Inferno 16's paragraph 10/11, see debug.py's docstring); fix
`<NN>-ranges.tsv`, delete the affected stage outputs, and rerun.

Backend choice matters more than any flag here - see [MEMO.md](MEMO.md) for
measured differences between models. Stages 2/3 have no window/island to
tune; a split that never validates aborts the canto (the retries are
visible in the log), so a model that often fails the numbered-split format
leaves cantos unfinishable - which was the deciding factor in dropping
`qwen3.6` from the benchmark set once the pipeline needed 6+ groups in a
single call (see MEMO.md). Delete the failing stage's output file and
rerun (ideally with a different model) to retry.

### `align_canto.py` matching failures (historical, script removed)

See [MEMO.md](MEMO.md) for measured coverage differences between models on
the same canto. If a local/small model is struggling:

- Try a larger or cloud-hosted model
- Enable thinking with `--think` flag (may improve accuracy but is slower)
- Adjust `--temperature`
- Check the logged offsets: many rejections with "Offset N words exceeds
  window" mean `--window-words` is too tight, while many "Block exceeded"
  warnings alongside island events mean it is too loose
