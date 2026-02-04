# Dante Norton

A project to reconstruct Charles Eliot Norton's prose translation of Dante's Divine Comedy, aligning it line-by-line with the original Italian text.

## Overview

**Current Status**: Preparation phase completed. The actual reconstruction work has not yet begun.

For the implementation plan, see [PLAN.md](PLAN.md).

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

## Prior Work

For a detailed analysis of previous experiments, see [PRIOR_WORK.md](PRIOR_WORK.md).

This project builds upon initial experiments to create line-by-line versions of Norton's prose translation:

- [dante-la-el/Inferno](https://github.com/7shi/dante-la-el/tree/main/Inferno) - Comparative study of multiple English translations (Norton, Longfellow, Carlyle) with Latin and Greek versions

### AI-Assisted Line Splitting Experiments

Multiple AI systems were tested for splitting Norton's prose into line-based versions, each processing different cantos:

- **[Bard/en-norton](https://github.com/7shi/dante-la-el/tree/main/Inferno/Bard/en-norton)** - Cantos 1-2 (both 3-line and 1-line versions)
- **[Claude/en-norton](https://github.com/7shi/dante-la-el/tree/main/Inferno/Claude/en-norton)** - Canto 3 (3-line version)
- **[Copilot/en-norton](https://github.com/7shi/dante-la-el/tree/main/Inferno/Copilot/en-norton)** - Cantos 4-5 (3-line version)

Two version types were produced:
- **3-line version** (`xx-3.txt`): Norton's prose with added line breaks to match Dante's tercets
- **1-line version** (`xx-1.txt`): Text rearranged to align word order with Dante's original lines, while preserving Norton's wording

Each AI-assisted conversion includes detailed process logs (both full and summary versions). The current project aims to systematize and extend this approach to all three parts of the Divine Comedy using a more robust methodology.
