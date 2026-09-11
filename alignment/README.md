# Alignment Scripts

Alignment scripts for the Italian original text and Norton's English translation.

## Overview

Two scripts align Dante's Italian lines to Norton's English prose:

- **`align_canto.py`** (single-stage, extraction-only): for each Norton
  paragraph, Italian lines are added to a block one at a time and an LLM
  extracts the corresponding Norton span, with island detection via a search
  window to find block boundaries. See [ALGORITHM.md](ALGORITHM.md) for full
  algorithm details and [ISLAND_FIX.md](ISLAND_FIX.md) for the requirements
  behind the island/search-window design.
- **`align3.py`** (two-stage, tercet-first, current recommendation): stage 1
  extracts a whole tercet (3 Italian lines) at once, with a fixed block size
  and no growth mechanism; stage 2 then splits that fixed Norton span into
  one fragment per Italian line, rearranging words but never substituting
  them. See [MEMO.md](MEMO.md) "Two-stage, tercet-first alignment" for the
  design rationale and gold-reference comparison results that motivated it.

[MEMO.md](MEMO.md) has the full comparison of results across LLM backends
and between the two scripts. As of that comparison, `openai:gpt-5.6-terra`
is the sole benchmark model going forward (see MEMO.md's model-selection
decision) — `ministral-3:14b`, `qwen3.6`, `gemma-4-31b-it`, and
`gpt-5.6-luna` were all tried and dropped for documented reasons.

Two fixed gold references for Canto 1, `01-1.txt` (per-line) and `01-3.txt`
(per-tercet), are also present in this directory — see "Reference Data"
below.

## Usage

### Basic Execution (align_canto.py)

```bash
# Process all of Canto I
uv run alignment/align_canto.py 1

# Process another Canto
uv run alignment/align_canto.py 2
```

### Options

```bash
# Limit the number of Italian lines processed (default: unlimited)
uv run alignment/align_canto.py 1 --max-lines 50

# Specify LLM model (default: ollama:ministral-3:14b)
uv run alignment/align_canto.py 1 --model google:gemini-2.5-flash

# Adjust temperature (default: 1.0)
uv run alignment/align_canto.py 1 --temperature 0.3

# Enable thinking (disabled by default)
uv run alignment/align_canto.py 1 --think

# Set the search window: how many words of the remaining Norton text may
# precede an accepted span before it is rejected (default: 20)
uv run alignment/align_canto.py 1 --window-words 30

# Baseline mode: require each span to start at the beginning of the
# remaining text (equivalent to --window-words 0). Reproduces the behavior
# measured in MEMO.md, for A/B comparison.
uv run alignment/align_canto.py 1 --strict-prefix

# Translate Italian to modern English before matching, instead of
# comparing the Italian text directly (default: direct comparison)
# NOT recommended: benchmarked worse than direct comparison with
# every model tested (see MEMO.md)
uv run alignment/align_canto.py 1 --translate
```

The `--model` value is passed through to `llm7shi`; use an `ollama:`, `google:`,
or `openai:` prefix to select the backend. Cloud backends need the
corresponding API key set in the environment (e.g. `GEMINI_API_KEY`).

Note on `--window-words` / `--strict-prefix`: a span found within the window
but not at the very beginning is an *island* — the text in front of it belongs
to a later Italian line, so the current block is extended by one line and
re-queried rather than the extraction being thrown away. Setting the window to
0 disables this and restores the strict prefix-only behavior that the MEMO.md
numbers were measured with. Widening the window too far converts rejections
into oversized blocks that get skipped, so check the reported offsets in the
log before raising it.

Note on `--translate`: it adds an LLM translation call per query, and on
Inferno Canto 1 it reduced coverage for the weaker models (e.g. 47% → 19%
for `ministral-3:14b`) while giving the stronger models no benefit. Kept as
an experiment switch; use the default direct comparison. See
[MEMO.md](MEMO.md) for the measured numbers.

### Two-stage Execution (align3.py)

```bash
# Process Canto I; -o is required (no default log path)
uv run alignment/align3.py 1 -o alignment/output/canto_01-terra-align3.log --model openai:gpt-5.6-terra

# Number of Italian lines per fixed-size stage-1 chunk (default: 3, i.e. a tercet)
uv run alignment/align3.py 1 -o out.log --block-size 3

# --model, --max-lines, --temperature, --think, --translate work the same as align_canto.py
```

Unlike `align_canto.py`, there is no `--window-words` / `--strict-prefix`:
stage 1's chunk size is fixed (`--block-size`) with no growth mechanism, so a
chunk that fails to match at the exact start of the remaining text is
skipped outright rather than extended (see MEMO.md for why). `-o` sets the
log path; two companion TSVs are written alongside it automatically (see
Output below).

### Batch runs across models (run_models.sh)

```bash
# Run align3.py over Canto 1 with every model in run_models.sh's MODELS list
# (currently openai:gpt-5.6-terra only - see MEMO.md's model-selection
# decision), skipping any model whose non-empty .log.tsv already exists,
# then print a summary table
alignment/run_models.sh 1
```

Logs land at `alignment/output/canto_NN-<short-name>-align3.log`. Edit the
`MODELS` array in the script to benchmark additional models.

## Reference Data

`01-1.txt` is a fixed, manually-edited gold reference for Canto 1: Norton's
prose rearranged to exactly 136 lines (one per Italian line), preserving
Norton's wording while reordering words to match Dante's line structure.
`01-3.txt` is the intermediate, 46-line tercet-level version from the same
source (line breaks only, no word reordering) — used to validate `align3.py`
stage 1's coarse extraction against a fixed reference at the same
granularity. Both are carried over from the `dante-la-el` project's Bard
experiments
(https://github.com/7shi/dante-la-el/tree/main/Inferno/Bard/en-norton) — see
[PRIOR_WORK.md](../PRIOR_WORK.md) for how they were produced (Bard's own
two-stage process: tercet-level segmentation, then per-line reordering with
no word substitution — the same two stages `align3.py` automates) and
[ALGORITHM.md](ALGORITHM.md) "Design rationale" for why `align_canto.py`
does not attempt that reordering step itself.

## Output

`align_canto.py` writes to `alignment/output/canto_XX.log`, containing:

- The full processing log (per-line progress, retries, rejections)
- Detailed Italian + English block listing
- Norton English text with line breaks at block boundaries
- Total block count
- Coverage: Italian lines that ended up in an output block, out of the canto
  total. Lines in a block that failed after `MAX_BLOCK_LINES` produce no
  output, so this - not how far the run got - is the completion metric.

`align3.py` writes to the log path given by `-o` (same kind of processing
log, covering both stages), plus two companion TSVs (Italian text, tab,
Norton fragment; a row whose Italian column is `|`-joined spans multiple
Italian lines):

- `<output>.stage1.tsv` — one row per stage-1 (coarse, tercet-sized) block,
  before decomposition. Comparable 1:1 by position against `01-3.txt`.
- `<output>.tsv` — the final per-line rows after stage 2. A row still spans
  multiple Italian lines only where stage 2's split never validated (see
  "Rows still merged after stage 2" in the log); otherwise it is one row per
  Italian line, comparable against `01-1.txt`.

Progress and errors are also printed to the console as either script runs.

## Algorithm

**align_canto.py**: for each Norton paragraph, Italian lines are added to a
block one at a time; the LLM is asked to extract the corresponding Norton
span (as structured JSON), which is validated with hard, mechanical checks
(non-empty, must appear verbatim in the Norton text, length ratio, within
the search window) rather than a separate LLM judgment call. The span's word
offset in the remaining text then decides the block boundary: offset 0
completes the block, a larger offset within the window is an island and
extends the block by one line. See [ALGORITHM.md](ALGORITHM.md) for the full
description, including failure handling and configuration constants.

**align3.py**: stage 1 asks for a whole tercet's Norton span at once (a
fixed `--block-size`-line chunk, no growth on failure - a failing chunk is
skipped, not extended, unlike align_canto.py's island mechanism). Stage 2
then asks a second LLM call to split that fixed span into one fragment per
Italian line, validated mechanically (fragment count, no empty fragment, and
the concatenated fragments' word multiset must equal the input span's
exactly); a block whose split never validates is kept as one merged row
rather than dropping text. See [MEMO.md](MEMO.md) "Two-stage, tercet-first
alignment" for the full rationale and results; `ALGORITHM.md` documents
align_canto.py's algorithm only, not yet align3.py's.

## Requirements

- Python 3.13+ (see `pyproject.toml`)
- `dante_norton` library (parent directory)
- `llm7shi` (dependency of `LLMClient`)
- An LLM backend: local (Ollama) or cloud (Gemini, OpenAI-compatible) with
  the relevant API key set

## Troubleshooting

### Matching failures

Backend choice matters more than any flag here — see
[MEMO.md](MEMO.md) for measured coverage differences between models on the
same canto. If a local/small model is struggling:

- Try a larger or cloud-hosted model
- Enable thinking with `--think` flag (may improve accuracy but is slower)
- Adjust `--temperature`
- Check the logged offsets: many rejections with "Offset N words exceeds
  window" mean `--window-words` is too tight, while many "Block exceeded"
  warnings alongside island events mean it is too loose

For `align3.py`, there is no window/island to tune; a failing stage-1 chunk
is simply skipped. Check "Rows still merged after stage 2" in the log
instead - a high count means stage 2's word-level splitting is struggling
for that model, which was the deciding factor in dropping `qwen3.6` from
the benchmark set (see MEMO.md).
