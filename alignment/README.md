# Alignment Scripts

Alignment scripts for Italian original text and Norton English translation.

## Overview

Implementation of the variable-length block alignment algorithm based on PLAN.md.
Uses LLM to obtain word correspondences and automatically detect block boundaries.

## Usage

### Basic Execution

```bash
# Process first 20 lines of Canto I
uv run alignment/align_canto.py 1

# Process another Canto
uv run alignment/align_canto.py 2
```

### Options

```bash
# Specify number of lines to process
uv run alignment/align_canto.py 1 --max-lines 50

# Specify LLM model (default: ollama:ministral-3:14b)
uv run alignment/align_canto.py 1 --model ollama:qwen2.5:32b

# Adjust temperature (default: 0.3, lower for better accuracy)
uv run alignment/align_canto.py 1 --temperature 0.1

# Enable thinking (disabled by default)
uv run alignment/align_canto.py 1 --think
```

### Examples

```bash
# Phase 1 test: Process first 20 lines (default settings)
# Model: ollama:ministral-3:14b, Think: False, Temperature: 0.3
uv run alignment/align_canto.py 1

# Process more lines
uv run alignment/align_canto.py 1 --max-lines 100

# Use a different model
uv run alignment/align_canto.py 1 --model ollama:qwen2.5:32b --temperature 0.2
```

## Output

Results are saved to `alignment/output/canto_XX.txt`.

- Each block is separated by a blank line
- Italian lines within a block are displayed consecutively

## Algorithm (Simple Version)

1. For each Norton English paragraph:
2. Add Italian lines one by one
3. Query LLM for word correspondences
4. Replace matched words with `#` markers
5. If prefix (up to last `#`) has no alphabetic characters, block is complete
6. Move to next paragraph when block is complete

*Note: This version does not implement gap detection or fallback mechanisms*

## Requirements

- Python 3.7+
- dante_norton library (parent directory)
- llm7shi (dependency of LLMClient)
- Local LLM (Ollama, etc.)
  - Default: ollama:ministral-3:14b

## Troubleshooting

### Matching failures

- Lower `--temperature` (0.1-0.3 recommended)
- Use a larger model (70B or higher)
- Enable thinking with `--think` flag (may improve accuracy but slower)
