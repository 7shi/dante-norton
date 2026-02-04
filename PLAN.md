# Implementation Plan

This document tracks planned improvements and future work.

## Current Implementation

The alignment algorithm has been successfully implemented. See:

- **[alignment/ALGORITHM.md](alignment/ALGORITHM.md)** - Detailed algorithm documentation
- **[alignment/align_canto.py](alignment/align_canto.py)** - Implementation
- **[PRIOR_WORK.md](PRIOR_WORK.md)** - Analysis of previous approaches

## Test Results

**Inferno Canto 1, Lines 1-9:**
- 9 Italian lines → 8 blocks
- 6 individual line alignments
- 1 enjambment case (Lines 4-5)
- 100% success rate

## Planned Improvements

### 1. Performance Optimization
- **Caching:** Cache modern translations to avoid repeated LLM calls
- **Parallel Processing:** Process multiple paragraphs simultaneously

### 2. Quality Assurance
- **Human Review Interface:** Flag uncertain alignments for manual review
- **Alternative Models:** Test with larger models (70B+) for comparison

### 3. Scale Testing
- **Full Canto:** Validate on all 136 lines of Canto 1
- **Measure Success Rate:** Track % of automatic vs. manual review cases

### 4. Additional Features
- Cross-canto alignment patterns
- Statistics on translation choices
- Export to multiple formats

## Usage

```bash
# Align first 9 lines of Canto 1
uv run alignment/align_canto.py 1 --max-lines 9

# Full canto
uv run alignment/align_canto.py 1
```

## Configuration

- **Model:** ollama:ministral-3:14b
- **Temperature:** 0.3
- **Max Retries:** 3

## Files

- `alignment/align_canto.py` - Main implementation
- `alignment/output/` - Results and logs
- `tokenize/inferno/` - Italian text
- `en-norton/inferno/` - Norton translation
