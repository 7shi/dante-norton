# dante_norton API Reference

## Classes

### `Canto`

Parser for a canto (section) of Dante's Divine Comedy.

Parses text with the following structure:
- Sections are separated by empty lines
- Text sections contain annotation references like `[1]`, `[2]`
- Annotation sections start with `[number]` and explain the references
- Within a section, lines without empty lines between them are joined with spaces (Markdown-style)
- Annotations are local to the preceding text section(s)

#### Methods

##### `__init__(text: str)`

Initialize and parse the canto text.

**Parameters:**
- `text` (str): The full text of the canto

##### `get_text_without_annotations(line_idx: int) -> str`

Get text at line_idx with annotation markers removed.

**Parameters:**
- `line_idx` (int): Index of the line

**Returns:**
- str: Text without `[n]` markers

**Raises:**
- `IndexError`: If line_idx is out of range

##### `get_full_text_without_annotations() -> str`

Get all text with annotation markers removed.

**Returns:**
- str: Full text without `[n]` markers, sections joined with newlines

#### Attributes

##### `lines: List[Tuple[str, List[str]]]`

Parsed lines as a list of tuples containing:
- `str`: The text content (with annotation markers)
- `List[str]`: Associated annotations for this text

## Functions

### `roman_number(r: str) -> int`

Parse roman number (1-39).

**Parameters:**
- `r` (str): Roman numeral string (e.g., "I", "IV", "X", "XXXIV")

**Returns:**
- int: Integer value of the roman numeral

**Raises:**
- `ValueError`: If the input is not a valid roman numeral

## Example Usage

```python
from dante_norton import Canto, roman_number

# Parse a canto
text = open('inferno/01.txt').read()
canto = Canto(text)

# Access parsed lines
for text, annotations in canto.lines:
    print(f"Text: {text}")
    for anno in annotations:
        print(f"  {anno}")

# Get text without markers
clean = canto.get_text_without_annotations(0)
full_clean = canto.get_full_text_without_annotations()

# Convert roman numerals
num = roman_number("XXXIV")  # 34
```
