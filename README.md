# Dante Norton

A project to reconstruct Charles Eliot Norton's prose translation of Dante's Divine Comedy, aligning it line-by-line with the original Italian text.

## Overview

**Current Status**: An LLM-based line alignment algorithm is implemented and under active testing (see [alignment/](alignment/)). Full-canto reconstruction quality still varies significantly by LLM backend; see [alignment/MEMO.md](alignment/MEMO.md) for current results.

Available preparation materials:

1. **Text Corpus**: Dante's Divine Comedy in both Italian and English (Norton translation)
2. **Parser Library**: Python library for parsing cantos with annotation support

## Data Sources

- **Italian original**: Complete Divine Comedy, [pg1000](https://www.gutenberg.org/ebooks/1000), read through `dante-corpus` (see below).

- **English (Norton translation)**, sourced from Project Gutenberg:
  - Inferno: [pg1995](https://www.gutenberg.org/ebooks/1995)
  - Purgatorio: [pg1996](https://www.gutenberg.org/ebooks/1996)
  - Paradiso: [pg1997](https://www.gutenberg.org/ebooks/1997)

### Dependency Projects

This project depends on the following companion repository:

- [dante-corpus](https://github.com/7shi/dante-corpus) - The shared corpus library and thin CLI. Serves the normalized Italian source text as a queryable "DB" through its `dante_corpus` API. **Required** — this project reads canto text from it via an editable path dependency, used directly in [`alignment/align.py`](alignment/align.py).

### Preparation

Because `dante-norton` consumes `dante-corpus` via an editable path dependency (`../dante-corpus`), both repositories must share one parent directory. Ensure you have `uv` installed, then clone both into the same directory:

```bash
git clone https://github.com/7shi/dante-corpus.git
git clone https://github.com/7shi/dante-norton.git
make -C dante-corpus
cd dante-norton
uv sync
```

The resulting layout:

```
your-workspace/
├── dante-corpus/    # source text (read via the dante_corpus API)
└── dante-norton/    # this repo (alignment)
```

## License

The text files are sourced from Project Gutenberg and are in the public domain in the United States.

This project is licensed under [CC0 1.0 Universal](LICENSE) (Public Domain).

## Project Structure

- en-norton/: English (Norton translation). See [en-norton/README.md](en-norton/README.md)
- dante_norton/: Python package for parsing Dante's Divine Comedy cantos. See [dante_norton/README.md](dante_norton/README.md) for API reference.
- alignment/: Italian-Norton line alignment scripts. See [alignment/README.md](alignment/README.md) for usage and for the algorithm, checking and backend-comparison documents it indexes.

## Future Work

- **Human review interface**: flag uncertain alignments for manual review.
- **Cross-canto patterns**: statistics on alignment/translation choices across cantos.
- **Alternative export formats** for the aligned output beyond the current `<NN>-1.txt`/`<NN>-3.txt` pair.

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
