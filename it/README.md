# Italian (Original Text)

Dante's Divine Comedy in the original Italian.

## Data Source

Project Gutenberg text:
- Complete Divine Comedy: [pg1000](https://www.gutenberg.org/ebooks/1000)

## Structure

```
it/
├── pg1000.txt         # Source: Complete Divine Comedy
├── inferno/           # 34 cantos (01.txt - 34.txt)
├── purgatorio/        # 33 cantos (01.txt - 33.txt)
├── paradiso/          # 33 cantos (01.txt - 33.txt)
├── split.py           # Script to split cantos
└── Makefile           # Build automation
```

## Usage

Download source file from Project Gutenberg:
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

Each canto file contains the original Italian text.

For tokenization of the Italian text, see [../tokenize/README.md](../tokenize/README.md).
