# English (Norton Translation)

Dante's Divine Comedy in English, translated by Charles Eliot Norton, with annotations.

## Data Sources

Project Gutenberg texts:
- Inferno: [pg1995](https://www.gutenberg.org/ebooks/1995)
- Purgatorio: [pg1996](https://www.gutenberg.org/ebooks/1996)
- Paradiso: [pg1997](https://www.gutenberg.org/ebooks/1997)

## Structure

```
en-norton/
├── pg1995.txt         # Source: Inferno
├── pg1996.txt         # Source: Purgatorio
├── pg1997.txt         # Source: Paradiso
├── inferno/           # 34 cantos (01.txt - 34.txt)
├── purgatorio/        # 33 cantos (01.txt - 33.txt)
├── paradiso/          # 33 cantos (01.txt - 33.txt)
├── split.py           # Script to split cantos
└── Makefile           # Build automation
```

## Usage

Download source files from Project Gutenberg:
```bash
make
```

Split into individual canto files:
```bash
make split
```

Clean generated directories:
```bash
make clean
```

## Text Format

Each canto file contains:
- English translation with annotation references (`[1]`, `[2]`, etc.)
- Annotation sections explaining the references
- Sections separated by empty lines

See the main project [README.md](../README.md) for parser usage.
