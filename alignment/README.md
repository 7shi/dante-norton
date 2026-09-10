# Alignment Scripts

Alignment scripts for the Italian original text and Norton's English translation.

## Overview

Implements variable-length block alignment between Dante's Italian lines and
Norton's English prose, using an LLM to extract the corresponding Norton span
for each Italian line (or group of lines, for enjambment) and island detection
to find block boundaries. See [ALGORITHM.md](ALGORITHM.md) for full algorithm
details, [MEMO.md](MEMO.md) for a comparison of results across different
LLM backends, and [ISLAND_FIX.md](ISLAND_FIX.md) for the requirements behind
the current island/search-window design.

## Usage

### Basic Execution

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

## Output

Results are written to `alignment/output/canto_XX.log`, containing:

- The full processing log (per-line progress, retries, rejections)
- Detailed Italian + English block listing
- Norton English text with line breaks at block boundaries
- Total block count
- Coverage: Italian lines that ended up in an output block, out of the canto
  total. Lines in a block that failed after `MAX_BLOCK_LINES` produce no
  output, so this - not how far the run got - is the completion metric.

Progress and errors are also printed to the console as the script runs.

## Algorithm

Summary: for each Norton paragraph, Italian lines are added to a block one at
a time; the LLM is asked to extract the corresponding Norton span (as
structured JSON), which is validated with hard, mechanical checks (non-empty,
must appear verbatim in the Norton text, length ratio, within the search
window) rather than a separate LLM judgment call. The span's word offset in
the remaining text then decides the block boundary: offset 0 completes the
block, a larger offset within the window is an island and extends the block by
one line. See [ALGORITHM.md](ALGORITHM.md) for the full description, including
failure handling and configuration constants.

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
