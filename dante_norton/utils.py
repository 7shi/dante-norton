"""
Utility functions for Dante Norton
"""

import re


def roman_number(r: str) -> int:
    """
    Parse roman number (1-39).
    
    Args:
        r: Roman numeral string (e.g., "I", "IV", "X", "XXXIV")
        
    Returns:
        Integer value of the roman numeral
        
    Raises:
        ValueError: If the input is not a valid roman numeral
    """
    if m := re.match("(|X|XX|XXX)(|I(?=X$|V$))(X?)(V?)(|I|II|III)$", r.upper()):
        x1 = len(m.group(1))
        i1 = len(m.group(2))
        x2 = len(m.group(3))
        v1 = len(m.group(4))
        i2 = len(m.group(5))
        return x1 * 10 - i1 + x2 * 10 + v1 * 5 + i2
    raise ValueError(f"invalid roman number: {r}")
