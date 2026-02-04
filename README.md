# Dante Norton

A project to reconstruct Charles Eliot Norton's prose translation of Dante's Divine Comedy, aligning it line-by-line with the original Italian text.

## Overview

**Current Status**: Preparation phase completed. The actual reconstruction work has not yet begun.

Available preparation materials:

1. **Text Corpus**: Dante's Divine Comedy in both Italian and English (Norton translation)
2. **Parser Library**: Python library for parsing cantos with annotation support
3. **Italian Tokenizer**: Advanced tokenizer for Italian text with proper apostrophe handling

## Data Sources

All text files are sourced from Project Gutenberg:

- **Italian original**:
  - Complete Divine Comedy: [pg1000](https://www.gutenberg.org/ebooks/1000)

- **English (Norton translation)**:
  - Inferno: [pg1995](https://www.gutenberg.org/ebooks/1995)
  - Purgatorio: [pg1996](https://www.gutenberg.org/ebooks/1996)
  - Paradiso: [pg1997](https://www.gutenberg.org/ebooks/1997)

## License

The text files are sourced from Project Gutenberg and are in the public domain in the United States.

This project is licensed under [CC0 1.0 Universal](LICENSE) (Public Domain).

## Project Structure

- it/: Italian (original). See [it/README.md](it/README.md)
- en-norton/: English (Norton translation). See [en-norton/README.md](en-norton/README.md)
- tokenize/: Italian tokenizer with advanced apostrophe handling. See [tokenize/README.md](tokenize/README.md) for details.
- dante_norton/: Python package for parsing Dante's Divine Comedy cantos. See [dante_norton/README.md](dante_norton/README.md) for API reference.

## Installation

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```
