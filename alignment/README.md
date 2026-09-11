# Alignment Scripts

Alignment scripts for the Italian original text and Norton's English translation.

## Overview

The current, recommended approach is a three-stage pipeline that
hierarchically decomposes each Norton paragraph down to one row per Italian
line, never extracting or verbatim-matching text - each stage only
rearranges words already known to belong to a given span, validated by
word-multiset equality:

- **`align_ranges.py`**: one LLM call over the whole canto identifies, for
  each Norton paragraph, the range of Italian lines it corresponds to.
- **`align3.py`**: reads that ranges TSV and splits each paragraph's Norton
  text into tercet-sized (3-line, by default) groups.
- **`align1.py`**: reads `align3.py`'s output and further splits any
  multi-line group into one fragment per Italian line.

See [ALGORITHM.md](ALGORITHM.md) for the full algorithm description,
numbered-line output format, and Canto 1 results (near-exact match against
both fixed gold references).

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

[MEMO.md](MEMO.md) has the full experimental history across LLM backends.
`openai:gpt-5.6-terra` is the sole benchmark model going forward (see
MEMO.md's model-selection decision) - `ministral-3:14b`, `qwen3.6`,
`gemma-4-31b-it`, and `gpt-5.6-luna` were all tried and dropped for
documented reasons.

Two fixed gold references for Canto 1, `01-1.txt` (per-line) and `01-3.txt`
(per-tercet), were used during development - see "Reference Data" below.

## Usage

### The current pipeline

```bash
# Stage 1: identify paragraph -> Italian line ranges (once per canto; its
# output is treated as a fixed input to the rest of the pipeline, not
# regenerated on every run - see ALGORITHM.md)
uv run alignment/align_ranges.py 1 \
    -o alignment/output/inferno-01-ranges.log --model openai:gpt-5.6-terra

# Stage 2 (align3.py): -i (ranges TSV) and -o (log path) are both required
uv run alignment/align3.py 1 -i alignment/output/inferno-01-ranges.tsv \
    -o alignment/output/inferno-01-3.log --model openai:gpt-5.6-terra

# Stage 3 (align1.py): -i (align3.py's group TSV) and -o are both required
uv run alignment/align1.py 1 -i alignment/output/inferno-01-3.tsv \
    -o alignment/output/inferno-01-1.log --model openai:gpt-5.6-terra

# Both accept: --model, --temperature, --think, --test (process only the
# first paragraph/group, for a quick local smoke test)
# align3.py additionally accepts --block-size (default: 3)
```

The `--model` value is passed through to `llm7shi`; use an `ollama:`,
`google:`, or `openai:` prefix to select the backend. Cloud backends need
the corresponding API key set in the environment.

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
this directory; they only informed development-time validation.

## Output

Every script in the current pipeline writes a log (`-o/--output`) and a
companion TSV alongside it - same base name, `.tsv` extension (e.g.
`inferno-01-3.log` / `inferno-01-3.tsv`):

- **`align_ranges.py`**: `paragraph<TAB>start_line<TAB>end_line`, one row
  per Norton paragraph.
- **`align3.py`**: `italian_lines<TAB>norton_text`, one row per tercet-sized
  group (`italian_lines` is `|`-joined when the group spans more than one
  line). Comparable 1:1 by position against `01-3.txt`.
- **`align1.py`**: same shape, one row per Italian line in the normal case
  (a `|`-joined multi-line row only where a group's split failed and was
  kept merged). Comparable against `01-1.txt`.

Each log contains the full processing trace (per-paragraph/group progress,
retries, rejections), a final detailed Italian+English listing, and summary
counts (total rows/groups, how many were kept merged, coverage).

`align_canto.py` (removed) wrote to `alignment/output/canto_XX.log` instead -
see MEMO.md for its log format.

Progress and errors are also printed to the console as any script runs.

## Requirements

- Python 3.13+ (see `pyproject.toml`)
- `dante_norton` library (parent directory)
- `llm7shi` (dependency of `LLMClient`)
- An LLM backend: local (Ollama) or cloud (Gemini, OpenAI-compatible) with
  the relevant API key set

## Troubleshooting

### Splits failing / falling back to merged rows

Backend choice matters more than any flag here - see [MEMO.md](MEMO.md) for
measured differences between models. `align3.py`/`align1.py` have no
window/island to tune; check "Paragraphs/Groups kept merged" in the log -
a high count means the model is struggling to produce a valid numbered
split for that canto, which was the deciding factor in dropping `qwen3.6`
from the benchmark set once the pipeline needed 6+ groups in a single call
(see MEMO.md).

### `align_canto.py` matching failures (historical, script removed)

See [MEMO.md](MEMO.md) for measured coverage differences between models on
the same canto. If a local/small model is struggling:

- Try a larger or cloud-hosted model
- Enable thinking with `--think` flag (may improve accuracy but is slower)
- Adjust `--temperature`
- Check the logged offsets: many rejections with "Offset N words exceeds
  window" mean `--window-words` is too tight, while many "Block exceeded"
  warnings alongside island events mean it is too loose
