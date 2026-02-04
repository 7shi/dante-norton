# Tokenize - Italian Tokenizer

This directory is sourced from the following URL. Please refer there for details:

- https://github.com/7shi/dante-llm/tree/main/tokenize

## Purpose

This tool provides an Italian tokenizer designed for accurate validation of word tables. It specifically addresses issues with simple string splitting, such as ambiguous apostrophe handling and word boundaries.

## Features

- **Advanced Apostrophe Handling**: Correctly handles apostrophes in various positions:
  - End of word (e.g., `Tant’`)
  - Middle of word (e.g., `ch’i’`)
  - Beginning of word (e.g., `l’altre`, `’l`)
- **Punctuation Separation**: Treats punctuation marks as separate tokens.
- **Space Preservation**: Keeps whitespace as tokens to allow for exact text reconstruction.
- **Quote Disambiguation**: Distinguishes between apostrophes (elision) and closing quotation marks (U+2019) using context.

## Usage

To generate the tokenized files:

```bash
make
```

This runs `tokenizer.py` and populates the `inferno/`, `purgatorio/`, and `paradiso/` directories.

## File Structure

- `tokenizer.py`: The main tokenization script.
- `quote_cases.txt`: Input examples used for determining apostrophe vs. closing quote context.
- `quote_cases_converted.txt`: Normalized data used by the tokenizer for apostrophe handling.
- `inferno/`, `purgatorio/`, `paradiso/`: Directories containing the generated token lists.
- `Makefile`: Automation for running the tokenizer.
