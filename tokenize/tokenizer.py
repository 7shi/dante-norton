"""
Italian tokenizer for Dante's Divine Comedy.
See analysis.md for tokenization rules and examples.
"""
import sys
from typing import List
from pathlib import Path

def split_on_apostrophes(text: str) -> List[str]:
    """
    Split text on apostrophes based on context.

    Rule: If the character before apostrophe is alpha, split after apostrophe.
    Otherwise, split before apostrophe.

    Args:
        text: Input text to split on apostrophes.

    Returns:
        List of text parts split at apostrophe boundaries.

    Examples:
        "ch'i'" -> ["ch'", "i'"]
        "l'altre" -> ["l'", "altre"]
        "'nferno" -> ["'nferno"]
    """
    # Return empty list for empty input
    if not text:
        return []

    parts = []
    s = ""  # Current segment being built
    prev_is_alpha = False

    # Process each character in the text
    for ch in text:
        if ch == "'":
            if prev_is_alpha:
                # Alpha before apostrophe: split after apostrophe (e.g., "l'")
                parts.append(s + ch)
                s = ""
            else:
                # Non-alpha before apostrophe: split before apostrophe (e.g., "'nferno")
                if s:
                    parts.append(s)
                s = ch
        else:
            s += ch
        # Track whether current character is alphabetic for next iteration
        prev_is_alpha = ch.isalpha()
    
    # Append any remaining segment
    if s:
        parts.append(s)

    return parts

def tokenize_part(text: str) -> List[str]:
    """
    Tokenize a text part (without spaces) into tokens.

    Args:
        text: Input text part to tokenize.

    Returns:
        List of tokens.
    """
    tokens = []
    s = ""  # Current token being built
    prev_is_alpha = False

    # Process each character to separate alphabetic from non-alphabetic tokens
    for ch in text:
        if ch.isalpha() or ch == "'":
            # Start new token if transitioning from non-alpha to alpha
            if not prev_is_alpha and s:
                tokens.append(s)
                s = ""
            s += ch
            prev_is_alpha = True
        else:
            # Save current alphabetic token before starting non-alpha token
            if s:
                tokens.append(s)
            s = ch
            prev_is_alpha = False
    
    # Append the final token
    if s:
        tokens.append(s)

    return tokens

def tokenize(text: str) -> List[str]:
    """
    Tokenize Italian text into a list of tokens.

    Args:
        text: Input text to tokenize.

    Returns:
        List of tokens. Concatenating all tokens should reconstruct the original text.
    """
    tokens = []
    # First split on apostrophes, then tokenize each part
    parts = split_on_apostrophes(text)
    for part in parts:
        tokens += tokenize_part(part)
    return tokens

convert_dict = {}

def convert_apostrophe(text: str) -> str:
    """
    Convert quotation marks in text based on pre-analyzed cases.

    This function handles the ambiguity between U+2019 (') used as:
    - Closing quotation mark (paired with U+2018 opening quote)
    - Apostrophe for elision (e.g., l'altra, ch'i')

    The conversion is based on manually verified cases stored in:
    - quote_cases.txt: Original lines containing both U+2018 and U+2019
    - quote_cases_converted.txt: LLM-analyzed versions where closing quotes
      are marked with double quotes (")

    Args:
        text: Input text line to convert.

    Returns:
        Text with elision apostrophes (U+2019) converted to single quotes ('),
        while quotation marks (U+2019 paired with U+2018) remain unchanged.

    Examples:
        Input:  "Com\u2019 io voleva dicer \u2018Tu m\u2019appaghe\u2019,"
        Output: "Com' io voleva dicer \u2018Tu m'appaghe\u2019,"
                (elision Com' and m' use single quotes ('), quotation marks 'Tu...appaghe' remain U+2018/U+2019)
    """
    # Lazy initialization of conversion dictionary
    if not convert_dict:
        script_dir = Path(__file__).parent
        # Load original lines with U+2018/U+2019
        with open(script_dir / "quote_cases.txt", "r", encoding="utf-8") as f:
            quote_cases = [line.rstrip() for line in f]
        # Load LLM-converted lines where closing quotes are marked as "
        with open(script_dir / "quote_cases_converted.txt", "r", encoding="utf-8") as f:
            quote_cases_converted = [line.rstrip() for line in f]
        # Build conversion dictionary
        for i in range(len(quote_cases)):
            x = quote_cases[i]  # Original text
            y = quote_cases_converted[i]  # LLM-converted text
            z = ""
            for j in range(len(x)):
                if y[j] == '"':
                    # Keep original character (quotation mark)
                    z += x[j]
                else:
                    # Use converted character (apostrophe -> ')
                    z += y[j]
            convert_dict[x] = z

    # Apply conversion if available
    if converted := convert_dict.get(text):
        return converted

    # Default: replace U+2019 with apostrophe
    return text.replace("\u2019", "'")

def has_alpha(text):
    """
    Check if the text contains any alphabetic characters.
    """
    return any(c.isalpha() for c in text)

def main():
    """
    Tokenize all Italian text files and write tokenized output.

    Reads all cantos from the Divine Comedy, converts apostrophes,
    tokenizes each line, and writes pipe-separated tokens to output files.
    Only tokens containing alphabetic characters are included.

    Output structure:
        tokenize/inferno/{01..34}.txt,
        tokenize/purgatorio/{01..33}.txt,
        tokenize/paradiso/{01..33}.txt

    Each line in output contains tokens separated by '|'.
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("filenames", nargs="+", help="file paths")
    args = parser.parse_args()

    # Process each cantica and canto
    script_dir = Path(__file__).resolve().parent
    it_dir = script_dir.parent / "it"
    for filename in args.filenames:
        txt = filename + ".txt"
        it_txt = it_dir / txt
        out_txt = script_dir / txt
        out_dir = out_txt.parent

        # Read and strip lines from input text
        canto = [
            convert_apostrophe(l)
            for line in it_txt.read_text(encoding="utf-8").splitlines()
            if (l := line.strip())
        ]

        # Create output directory for this cantica
        out_dir.mkdir(exist_ok=True)

        with open(out_txt, "w", encoding="utf-8") as f:
            for line in canto:
                # Tokenize and filter to only alphabetic tokens
                tokens = [token for token in tokenize(line) if has_alpha(token)]
                # Write pipe-separated tokens
                print(line, *tokens, sep="|", file=f)
        print(f"Wrote: {filename}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
