"""
Canto parser for Dante's Divine Comedy text files.
"""

import re
import json
from typing import List, Tuple


class Canto:
    """
    Parser for a canto (section) of Dante's Divine Comedy.
    
    Parses text with the following structure:
    - Sections are separated by empty lines
    - Text sections contain annotation references like [1], [2]
    - Annotation sections start with [number] and explain the references
    - Within a section, lines without empty lines between them are joined with spaces (Markdown-style)
    - Annotations are local to the preceding text section(s)
    """
    
    def __init__(self, text: str):
        """
        Initialize and parse the canto text.
        
        Args:
            text: The full text of the canto
        """
        self.lines: List[Tuple[str, List[str]]] = []
        self._parse(text)
    
    def _parse(self, text: str) -> None:
        """Parse the text into lines with annotations."""
        # Split into sections (separated by empty lines)
        sections = self._split_into_sections(text)
        
        # Process sections in order
        # Pattern: text section(s) followed by annotation section(s)
        i = 0
        while i < len(sections):
            section = sections[i]
            
            # Check if this is an annotation section
            if self._is_annotation_section(section):
                # Find the annotations in this and following sections
                local_annotations: dict[int, str] = {}
                while i < len(sections) and self._is_annotation_section(sections[i]):
                    anno_section = sections[i]
                    anno_text = ' '.join(anno_section)
                    # 注釈全体を [1] 付きで保持
                    match = re.match(r'^\[(\d+)\]', anno_text)
                    if match:
                        num = int(match.group(1))
                        local_annotations[num] = anno_text
                    i += 1
                
                # Associate annotations with previous text section(s)
                if local_annotations and self.lines:
                    # Update the last text section with these annotations
                    last_text, last_annos = self.lines[-1]
                    # Find references in the last text section
                    ref_pattern = re.compile(r'\[(\d+)\]')
                    refs = ref_pattern.findall(last_text)
                    
                    # Add annotations for found references
                    for ref in refs:
                        num = int(ref)
                        if num in local_annotations and local_annotations[num] not in last_annos:
                            last_annos.append(local_annotations[num])
            else:
                # This is a text section
                full_text = ' '.join(section)
                self.lines.append((full_text, []))
                i += 1
    
    def _is_annotation_section(self, section: List[str]) -> bool:
        """Check if a section is an annotation section (starts with [number])."""
        if not section:
            return False
        first_line = section[0].strip()
        return bool(re.match(r'^\[\d+\]', first_line))
    
    def _split_into_sections(self, text: str) -> List[List[str]]:
        """Split text into sections separated by empty lines."""
        lines = text.split('\n')
        sections: List[List[str]] = []
        current_section: List[str] = []
        
        for line in lines:
            stripped = line.strip()
            if stripped == '':
                # Empty line - end current section if it has content
                if current_section:
                    sections.append(current_section)
                    current_section = []
            else:
                current_section.append(stripped)
        
        # Don't forget the last section
        if current_section:
            sections.append(current_section)
        
        return sections
    
    def get_text_without_annotations(self, line_idx: int) -> str:
        """
        Get text at line_idx with annotation markers removed.
        
        Args:
            line_idx: Index of the line
            
        Returns:
            Text without [n] markers
        """
        if line_idx < 0 or line_idx >= len(self.lines):
            raise IndexError(f"Line index {line_idx} out of range")
        
        text = self.lines[line_idx][0]
        return re.sub(r'\[\d+\]', '', text)
    
    def get_full_text_without_annotations(self) -> str:
        """
        Get all text with annotation markers removed.
        
        Returns:
            Full text without [n] markers, sections joined with newlines
        """
        texts = []
        for text, _ in self.lines:
            clean_text = re.sub(r'\[\d+\]', '', text)
            texts.append(clean_text)
        return '\n\n'.join(texts)


if __name__ == '__main__':
    from pathlib import Path

    # Test with the inferno canto 1 file
    basedir = Path(__file__).resolve().parent.parent
    text = (basedir / 'en-norton' / 'inferno' / '01.txt').read_text(encoding='utf-8')
    
    canto = Canto(text)
    
    # Display lines as JSON
    output = []
    for line, annotations in canto.lines:
        output.append({
            'line': line,
            'annotations': annotations
        })
    
    print(json.dumps(output, indent=2, ensure_ascii=False))
