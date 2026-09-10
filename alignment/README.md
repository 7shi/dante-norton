# Alignment Scripts

Alignment scripts for the Italian original text and Norton's English translation.

## Overview

Implements variable-length block alignment between Dante's Italian lines and
Norton's English prose, using an LLM to extract the corresponding Norton text
for each Italian line (or group of lines, for enjambment) and island detection
to find block boundaries. See [ALGORITHM.md](ALGORITHM.md) for full algorithm
details, and [MEMO.md](MEMO.md) for a comparison of results across different
LLM backends.

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

# Translate Italian to modern English before matching, instead of
# comparing the Italian text directly (default: direct comparison)
uv run alignment/align_canto.py 1 --translate
```

The `--model` value is passed through to `llm7shi`; use an `ollama:`, `google:`,
or `openai:` prefix to select the backend. Cloud backends need the
corresponding API key set in the environment (e.g. `GEMINI_API_KEY`).

## Output

Results are written to `alignment/output/canto_XX.log`, containing:

- The full processing log (per-line progress, retries, rejections)
- Detailed Italian + English block listing
- Norton English text with line breaks at block boundaries
- Total block count

Progress and errors are also printed to the console as the script runs.

## Algorithm

Summary: for each Norton paragraph, Italian lines are added to a block one at
a time; the LLM is asked to extract the corresponding Norton text (as
structured JSON), which is validated with hard, mechanical checks (must
appear verbatim in the Norton text, length ratio, position) rather than a
separate LLM judgment call. Island detection determines when a block is
complete. See [ALGORITHM.md](ALGORITHM.md) for the full description,
including failure handling and configuration constants.

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
