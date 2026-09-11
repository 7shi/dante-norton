"""
Debugging helper for align.py's three-stage alignment pipeline: inspect
stage-1 paragraph ranges against the Italian original and the Norton text,
mechanically cross-check the stage output files, and word-diff split
attempts - all without calling the LLM.

Read this when a canto fails. Symptom playbook:

- "✗ Stage N ... could not be split after 3 attempts" or a row-count
  mismatch like "16-3.txt has 43 line(s) but 46 expected" usually means
  stage 1 assigned the wrong Italian line range to a Norton paragraph
  (typically off by a tercet at a paragraph boundary), so the paragraph's
  English text has no words for the range's last group - the model then
  leaves that group empty and the split can never validate.

1. Find the failing paragraph in the console output or <NN>.log (e.g.
   "✗ Stage 2 failed: paragraph 10 could not be split after 3 attempts").

2. Inspect that paragraph against the originals:

       uv run python alignment/debug.py show inferno 16 -p 10

   Prints the paragraph's range from <NN>-ranges.tsv, the Italian lines it
   covers (via dante-corpus) split into the same tercet groups with the
   canto-wide serial numbers the LLM sees, the lines just before/after the
   range (for boundary checking), and the Norton paragraph text.

3. Check the boundaries: the Norton text must start at the range's first
   Italian line's content and end at the range's last line's content, and
   the next paragraph's text must pick up right where the range ends. A
   boundary error is fixed by editing <NN>-ranges.tsv
   (paragraph<TAB>start<TAB>end; contiguous, no gaps/overlaps), then
   regenerating the stages downstream of it (they are skipped otherwise):

       rm alignment/<cantica>/<NN>-3.txt alignment/<cantica>/<NN>-1.txt
       uv run python alignment/align.py <cantica> -c <NN>

   Real example - Inferno 16: the ranges said paragraph 10 = lines 79-90,
   but Norton paragraph 10 ends at line 87 ("...seemed wings.") and lines
   88-90 ("Un amen ... di partirsi.") belong to paragraph 11, whose text
   opens with "Not an amen could have been said...". Paragraph 10's split
   kept failing because its English had nothing for group 30 (lines 88-90).
   Fix: 10 -> 79-87, 11 -> 88-105.

4. Mechanically cross-check whatever is already on disk (no LLM):

       uv run python alignment/debug.py check inferno 16

   Validates the ranges TSV (contiguity/coverage) and cross-checks the
   <NN>-3.txt / <NN>-1.txt row counts and per-paragraph word content
   against the Norton text. Stale files - produced before a ranges fix, a
   Norton text edit, or a --block-size change - are reported with the file
   to delete and rerun.

5. Word-diff one split against its Norton paragraph (per-group word counts
   plus missing/extra words):

       uv run python alignment/debug.py words inferno 16 -p 10 \
           --response '27 "If other times..." ...
                       28 Therefore, ...'

   --response may be a `response: '...'` line copied straight out of
   <NN>.log; omit it to read the numbered lines from stdin instead. A
   group with 0 words means the English text has nothing for those Italian
   lines -> range problem, go to step 3.

The checks reuse align.py's own helpers (imported, not duplicated), so
what `check` accepts is exactly what align.py's resume path accepts.
"""

import argparse
import ast
import re
import sys
import textwrap
from collections import Counter
from typing import Dict, List, Tuple

import align

CANTICAS = ["inferno", "purgatorio", "paradiso"]
WIDTH = 76


# ============================================================================
# Shared context
# ============================================================================

class CantoContext:
    """
    Everything the debug commands need for one canto: the paths (mirroring
    align.align_canto), the Italian lines, the Norton paragraphs keyed by
    number, and the stage-1 ranges (None when stage 1 has not run).
    """

    def __init__(self, cantica: str, canto: int, block_size: int):
        self.cantica = cantica
        self.canto = canto
        self.block_size = block_size
        self.out_dir = align.ALIGNMENT_DIR / cantica
        self.ranges_path = self.out_dir / f"{canto:02d}-ranges.tsv"
        self.tercet_path = self.out_dir / f"{canto:02d}-3.txt"
        self.line_path = self.out_dir / f"{canto:02d}-1.txt"
        self.norton_file = align.REPO_ROOT / "en-norton" / cantica / f"{canto:02d}.txt"
        self.italian_lines = align.load_italian_lines(cantica, canto)
        self.paragraphs: Dict[int, str] = dict(
            align.load_norton_paragraphs(str(self.norton_file)))
        self.ranges = (align.load_ranges_tsv(str(self.ranges_path))
                       if self.ranges_path.exists() else None)

    def paragraph_groups(self) -> List[Tuple[align.ParagraphRange, int,
                                             List[List[align.ItalianLine]]]]:
        """
        Each paragraph's tercet groups with their canto-wide serial numbers
        - the same grouping and numbering run_stage2 feeds the LLM - as
        (range, first serial, groups) per paragraph, in paragraph order.
        """
        entries = []
        next_num = 1
        for r in self.ranges:
            group_lines = self.italian_lines[r.start_line - 1:r.end_line]
            groups = align.chunk_lines(group_lines, self.block_size)
            entries.append((r, next_num, groups))
            next_num += len(groups)
        return entries


# ============================================================================
# Word helpers
# ============================================================================

def word_diff(target: str, got: str) -> Tuple[Counter, Counter]:
    """(missing, extra) word counters: target vs got, order-insensitive."""
    target_counts = Counter(align.word_multiset(target))
    got_counts = Counter(align.word_multiset(got))
    return target_counts - got_counts, got_counts - target_counts


def format_counts(counter: Counter, limit: int = 8) -> str:
    if not counter:
        return "none"
    items = [f"{count}x '{word}'" for word, count in counter.most_common(limit)]
    if len(counter) > limit:
        items.append(f"... (+{len(counter) - limit} more distinct)")
    return ", ".join(items)


def check_paragraph_words(label: str, target: str, got: str) -> int:
    """Print a word-content mismatch, if any; return 1 on mismatch, else 0."""
    if not target:
        print(f"    ✗ {label}: no Norton text found for this paragraph number")
        return 1
    missing, extra = word_diff(target, got)
    if not missing and not extra:
        return 0
    print(f"    ✗ {label}: word content does not match the Norton text")
    print(f"        missing (in Norton, not in rows): {format_counts(missing)}")
    print(f"        extra   (in rows, not in Norton): {format_counts(extra)}")
    return 1


def parse_numbered_lenient(text: str) -> List[Tuple[int, str]]:
    """
    Parse numbered fragments tolerantly, for inspecting what the model
    actually returned: `12 text`, `12. text`, `12) text`, `12: text`, or a
    bare `12` (an empty fragment - the interesting failure case). Unlike
    align.parse_numbered_lines, nothing is validated; an unnumbered line is
    treated as a continuation of the previous fragment (hard-wrapped pastes).
    """
    pairs: List[Tuple[int, str]] = []
    for raw_line in text.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r'^(\d+)[.)]?:?(?:\s+(.*))?$', line)
        if match:
            pairs.append((int(match.group(1)), (match.group(2) or "").strip()))
        elif pairs:
            pairs[-1] = (pairs[-1][0], f"{pairs[-1][1]} {line}".strip())
    return pairs


# ============================================================================
# Commands
# ============================================================================

def cmd_show(args: argparse.Namespace) -> None:
    ctx = CantoContext(args.cantica, args.canto, args.block_size)
    if ctx.ranges is None:
        sys.exit(f"✗ {ctx.ranges_path}: not found - stage 1 has not run; nothing to inspect yet")
    total = len(ctx.italian_lines)
    shown = 0
    for r, serial_start, groups in ctx.paragraph_groups():
        if args.paragraph is not None and r.paragraph != args.paragraph:
            continue
        shown += 1
        print(f"Paragraph {r.paragraph}: lines {r.start_line}-{r.end_line} -> "
              f"{len(groups)} group(s) (serials {serial_start}-{serial_start + len(groups) - 1})")
        if r.start_line > 1:
            print(f"  before {r.start_line - 1}: {ctx.italian_lines[r.start_line - 2].full_text}")
        for i, group in enumerate(groups):
            print(f"  group {serial_start + i} (lines {group[0].line_num}-{group[-1].line_num}):")
            for line in group:
                print(f"    {line.line_num}: {line.full_text}")
        if r.end_line < total:
            print(f"  after  {r.end_line + 1}: {ctx.italian_lines[r.end_line].full_text}")
        text = ctx.paragraphs.get(r.paragraph)
        if text is None:
            print(f"  ✗ no Norton paragraph {r.paragraph} in {ctx.norton_file}")
        else:
            print("  Norton text:")
            print(textwrap.fill(text, width=WIDTH,
                                initial_indent="    ", subsequent_indent="    "))
        print()
    if args.paragraph is not None and shown == 0:
        sys.exit(f"✗ paragraph {args.paragraph} not found in {ctx.ranges_path} "
                 f"(paragraphs: {[r.paragraph for r in ctx.ranges]})")


def cmd_check(args: argparse.Namespace) -> None:
    ctx = CantoContext(args.cantica, args.canto, args.block_size)
    if ctx.ranges is None:
        sys.exit(f"✗ {ctx.ranges_path}: not found - stage 1 has not run for this canto")
    total = len(ctx.italian_lines)
    failures = 0

    problem = align.validate_ranges(ctx.ranges, sorted(ctx.paragraphs.items()), total)
    if problem:
        print(f"✗ ranges: {problem}")
        failures += 1
    else:
        print(f"✓ ranges: {len(ctx.ranges)} paragraphs cover lines 1-{total} contiguously")

    entries = ctx.paragraph_groups()
    for stage_label, path, rows_of in (
            ("stage 2", ctx.tercet_path, lambda gs: len(gs)),
            ("stage 3", ctx.line_path, lambda gs: sum(len(g) for g in gs))):
        if not path.exists():
            print(f"- {path.name}: not written yet ({stage_label} runs on the next align.py pass)")
            continue
        texts = path.read_text(encoding='utf-8').splitlines()
        expected = sum(rows_of(gs) for _, _, gs in entries)
        if len(texts) != expected:
            print(f"✗ {path.name}: {len(texts)} row(s) but {expected} expected "
                  f"- delete it and rerun to regenerate")
            failures += 1
            continue
        print(f"✓ {path.name}: {len(texts)} row(s)")
        offset = 0
        for r, _, gs in entries:
            n = rows_of(gs)
            got = " ".join(texts[offset:offset + n])
            offset += n
            failures += check_paragraph_words(f"paragraph {r.paragraph}",
                                              ctx.paragraphs.get(r.paragraph, ""), got)
    if failures == 0:
        print("✓ all word content matches the Norton paragraphs")
    sys.exit(1 if failures else 0)


def cmd_words(args: argparse.Namespace) -> None:
    ctx = CantoContext(args.cantica, args.canto, args.block_size)
    if ctx.ranges is None:
        sys.exit(f"✗ {ctx.ranges_path}: not found - stage 1 has not run for this canto")
    entry = next(((r, s, gs) for r, s, gs in ctx.paragraph_groups()
                  if r.paragraph == args.paragraph), None)
    if entry is None:
        sys.exit(f"✗ paragraph {args.paragraph} is not in {ctx.ranges_path}")
    r, serial_start, groups = entry
    serials = list(range(serial_start, serial_start + len(groups)))

    raw = args.response
    if raw is None:
        if sys.stdin.isatty():
            sys.exit("✗ no response given - pass --response '...' or pipe the numbered lines on stdin")
        raw = sys.stdin.read()
    stripped = re.sub(r'^\s*response:\s*', '', raw.strip())
    if stripped[:1] in ("'", '"'):
        try:  # a `response: '...'` line copied straight out of the log
            stripped = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            pass
    fragments = dict(parse_numbered_lenient(stripped))

    # Tolerate responses numbered 1..n instead of with canto-wide serials:
    # if nothing overlaps and the count fits, pair by position.
    if fragments and not any(s in fragments for s in serials) and len(fragments) == len(groups):
        fragments = dict(zip(serials, (fragments[n] for n in sorted(fragments))))

    print(f"Paragraph {r.paragraph} (lines {r.start_line}-{r.end_line}, "
          f"serials {serials[0]}-{serials[-1]}) vs {len(fragments)} numbered fragment(s)")
    issues = 0
    got_parts = []
    for group, serial in zip(groups, serials):
        lo, hi = group[0].line_num, group[-1].line_num
        frag = fragments.get(serial)
        if frag is None:
            print(f"  ✗ group {serial} (lines {lo}-{hi}): no fragment with this number")
            issues += 1
            continue
        n_words = len(align.word_multiset(frag))
        if n_words == 0:
            print(f"  ✗ group {serial} (lines {lo}-{hi}): empty - the English text "
                  f"has no words for these Italian lines (range problem?)")
            issues += 1
        else:
            print(f"  · group {serial} (lines {lo}-{hi}): {n_words} words")
        got_parts.append(frag)
    stray = sorted(set(fragments) - set(serials))
    if stray:
        print(f"  note: fragment number(s) outside this paragraph's groups: {stray}")

    target = ctx.paragraphs.get(r.paragraph)
    if target is None:
        print(f"  ✗ no Norton paragraph {r.paragraph} in {ctx.norton_file}")
        issues += 1
    else:
        missing, extra = word_diff(target, " ".join(got_parts))
        if not missing and not extra:
            print("  ✓ words match the Norton text exactly")
        else:
            print(f"  ✗ missing (in Norton, not in fragments): {format_counts(missing)}")
            print(f"  ✗ extra   (in fragments, not in Norton): {format_counts(extra)}")
            issues += 1
    sys.exit(1 if issues else 0)


# ============================================================================
# Entry point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Debug helpers for align.py - see this file's docstring "
                    "for the full debugging playbook")
    parser.add_argument("--block-size", type=int, default=align.DEFAULT_BLOCK_SIZE,
                        help=f"Italian lines per stage-2 group "
                             f"(default: {align.DEFAULT_BLOCK_SIZE})")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_canto(p):
        p.add_argument("cantica", choices=CANTICAS, help="Cantica name")
        p.add_argument("canto", type=int, help="Canto number")

    p_show = sub.add_parser("show", help="Print a paragraph's range, Italian lines, "
                                         "groups and Norton text")
    add_canto(p_show)
    p_show.add_argument("-p", "--paragraph", type=int,
                        help="Show only this paragraph number (default: all)")

    p_check = sub.add_parser("check", help="Cross-check ranges and stage output "
                                           "files against the Norton text (no LLM)")
    add_canto(p_check)

    p_words = sub.add_parser("words", help="Word-diff a numbered split response "
                                           "against the Norton paragraph")
    add_canto(p_words)
    p_words.add_argument("-p", "--paragraph", type=int, required=True,
                         help="Paragraph number to diff against")
    p_words.add_argument("--response",
                         help="The model's numbered response (default: read from stdin; "
                              "a `response: '...'` line copied from the log works as-is)")

    args = parser.parse_args()
    n_cantos = len(align.dante_corpus.api.cantos(args.cantica))
    if not 1 <= args.canto <= n_cantos:
        parser.error(f"canto must be 1-{n_cantos} for {args.cantica}")

    {"show": cmd_show, "check": cmd_check, "words": cmd_words}[args.command](args)


if __name__ == '__main__':
    main()
