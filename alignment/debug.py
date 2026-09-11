"""
Debugging helper for align.py's three-stage alignment pipeline: diagnose a
failing canto from its log, inspect stage-1 paragraph ranges against the
Italian original and the Norton text, dump each group's Italian lines
against its stage-2/stage-3 rows, mechanically cross-check the stage output
files, and word-diff split attempts - all without calling the LLM.

Read this when a canto fails. Symptom playbook:

- Start here - name the canto and let the tool find where to dig:

       uv run python alignment/debug.py diagnose purgatorio 1

  Reads the last "✗ Stage N failed" from <NN>.log, cross-checks the files
  on disk, and for a stage-3 failure shows the failing group's stage-2 row
  bilingually (it: Italian line, en: English row) with the next group -
  the misplaced English usually sits there - plus a verdict: range problem
  or stage-2 boundary mistake. Then follow the pointed-to step below.

- "✗ Stage 2 ... could not be split after 3 attempts" or a row-count
  mismatch like "16-3.txt has 43 line(s) but 46 expected" usually means
  stage 1 assigned the wrong Italian line range to a Norton paragraph
  (typically off by a tercet at a paragraph boundary), so the paragraph's
  English text has no words for the range's last group - the model then
  leaves that group empty and the split can never validate.

- "✗ Stage 3 ... group N/M (line(s) ...) could not be split after 3
  attempts" with the model leaving the last line(s) empty usually means
  the group's stage-2 row has no words for them: the stage-2 split drew
  the group boundary one sentence too late (Norton punctuation differs
  from the Italian, e.g. ':' repunctuated as '.'). The ranges are fine
  here - the paragraph's words all match, so `check` stays green. See
  steps 5-6.

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

   Or re-split just the affected paragraphs, splicing the new rows into the
   existing files (one contiguous run covering every paragraph whose range
   changed - an edited boundary touches the two paragraphs sharing it):

        uv run python alignment/align.py purgatorio -c 1 -p 10-11

   Real example - Inferno 16: the ranges said paragraph 10 = lines 79-90,
   but Norton paragraph 10 ends at line 87 ("...seemed wings.") and lines
   88-90 ("Un amen ... di partirsi.") belong to paragraph 11, whose text
   opens with "Not an amen could have been said...". Paragraph 10's split
   kept failing because its English had nothing for group 30 (lines 88-90).
   Fix: 10 -> 79-87, 11 -> 88-105.

4. Mechanically cross-check whatever is already on disk (no LLM):

       uv run python alignment/debug.py check inferno 16

   Validates the ranges TSV (contiguity/coverage) and cross-checks the
   <NN>-3.txt / <NN>-1.txt row counts (blank rows are reported as pending
   re-splits, not failures) and per-paragraph word content against the
   Norton text. Stale files - produced before a ranges fix, a Norton text
   edit, or a --block-size change - are reported with the file to delete
   and rerun.

5. Eyeball the failing group - its Italian lines, the stage-2 row claiming
   to cover them (from <NN>-3.txt), and the stage-3 rows (from <NN>-1.txt):

       uv run python alignment/debug.py rows purgatorio 1 -g 29

   Filter with -p instead of -g to walk a whole paragraph. If the stage-2
   row has no words for the group's last Italian line(s), it is a stage-2
   boundary mistake: re-split the affected paragraphs with -p (step 3's
   commands), or blank the paragraph's rows in <NN>-3.txt / <NN>-1.txt and
   rerun. The range itself is only wrong if `show` (step 2) shows a
   boundary mismatch.

   Real example - Purgatorio 1: stage 3 kept failing on group 29 (lines
   79-81) with an empty fragment for line 81, three times identically. Its
   stage-2 row ended at "...thou hold her." (Norton turns the Italian ':'
   into '.'), and line 81's words ("For her love, then, incline thyself to
   us;") sat in the next group's row. The paragraph's words all matched on
   disk, so `check` was green. Fix: align.py purgatorio -c 1 -p 10-11.

6. Word-diff one split response (per-group/line word counts plus
   missing/extra words):

       uv run python alignment/debug.py words inferno 16 -p 10 \
           --response '27 "If other times..." ...
                       28 Therefore, ...'

   --response may be a `response: '...'` line copied straight out of
   <NN>.log; omit it to read the numbered lines from stdin instead. A
   group with 0 words means the English text has nothing for those Italian
   lines -> a range problem (step 3) or a stage-2 boundary mistake
   (step 5). For a stage-3 failure, diff just the failing group against
   its stage-2 row (fragments numbered by Italian line number; when the
   fragments reproduce the row but a line is still empty, the command says
   whether the range or the stage-2 split is at fault):

       uv run python alignment/debug.py words purgatorio 1 -g 29 \
           --response '79 of thy Marcia, who in her look still prays thee,
                       80 O holy breast, that for thine own thou hold her.
                       81'

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


def check_paragraph_words(label: str, target: str, got: str) -> List[str]:
    """Word-content mismatch lines for one paragraph, if any."""
    if not target:
        return [f"    ✗ {label}: no Norton text found for this paragraph number"]
    missing, extra = word_diff(target, got)
    if not missing and not extra:
        return []
    return [f"    ✗ {label}: word content does not match the Norton text",
            f"        missing (in Norton, not in rows): {format_counts(missing)}",
            f"        extra   (in rows, not in Norton): {format_counts(extra)}"]


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


def read_stage_rows(path) -> List[str] | None:
    """A stage output file's rows, or None when it has not been written."""
    return path.read_text(encoding='utf-8').splitlines() if path.exists() else None


def parse_response_text(raw: str) -> Dict[int, str]:
    """A model's numbered fragments: a `response: '...'` line copied out of
    the log, or raw numbered lines."""
    stripped = re.sub(r'^\s*response:\s*', '', raw.strip())
    if stripped[:1] in ("'", '"'):
        try:  # a `response: '...'` line copied straight out of the log
            stripped = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            pass
    return dict(parse_numbered_lenient(stripped))


def load_response(args: argparse.Namespace) -> Dict[int, str]:
    """The model's numbered fragments from --response or stdin."""
    raw = args.response
    if raw is None:
        if sys.stdin.isatty():
            sys.exit("✗ no response given - pass --response '...' or pipe the numbered lines on stdin")
        raw = sys.stdin.read()
    return parse_response_text(raw)


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


def cmd_rows(args: argparse.Namespace) -> None:
    """
    Per group: the Italian lines, the stage-2 row that claims to cover
    them, and the stage-3 rows on disk - the view for eyeballing a split
    that keeps failing. A stage-2 row whose words stop short of the
    group's last Italian line(s) is a stage-2 boundary mistake.
    """
    ctx = CantoContext(args.cantica, args.canto, args.block_size)
    if ctx.ranges is None:
        sys.exit(f"✗ {ctx.ranges_path}: not found - stage 1 has not run for this canto")
    entries = ctx.paragraph_groups()
    offsets = []
    row_cursor = 0
    for _, _, gs in entries:
        offsets.append(row_cursor)
        row_cursor += len(gs)
    tercets = read_stage_rows(ctx.tercet_path)
    singles = read_stage_rows(ctx.line_path)
    shown = 0
    for (r, serial_start, groups), offset in zip(entries, offsets):
        if args.paragraph is not None and r.paragraph != args.paragraph:
            continue
        for i, group in enumerate(groups):
            serial = serial_start + i
            if args.group is not None and serial != args.group:
                continue
            shown += 1
            print(f"Group {serial} (lines {group[0].line_num}-{group[-1].line_num}, "
                  f"paragraph {r.paragraph}):")
            for line in group:
                print(f"  {line.line_num}: {line.full_text}")
            if tercets is None:
                print(f"  stage 2 row: - ({ctx.tercet_path.name} not written yet)")
            elif offset + i >= len(tercets):
                print(f"  stage 2 row: - (beyond {ctx.tercet_path.name}'s {len(tercets)} row(s))")
            else:
                row = tercets[offset + i]
                mark = "" if row.strip() else "   ✗ EMPTY"
                print(f"  stage 2 row: {row}{mark}")
            if singles is None:
                print(f"  stage 3 rows: - ({ctx.line_path.name} not written yet)")
            else:
                for line in group:
                    if line.line_num - 1 >= len(singles):
                        print(f"  stage 3 {line.line_num}: - (beyond the file's "
                              f"{len(singles)} row(s))")
                        continue
                    row = singles[line.line_num - 1]
                    mark = "" if row.strip() else "   ✗ EMPTY"
                    print(f"  stage 3 {line.line_num}: {row}{mark}")
            print()
    if shown == 0:
        wanted = []
        if args.paragraph is not None:
            wanted.append(f"paragraph {args.paragraph}")
        if args.group is not None:
            wanted.append(f"group {args.group}")
        sys.exit(f"✗ nothing matches {' and '.join(wanted)} in {ctx.ranges_path} "
                 f"(paragraphs: {[r.paragraph for r in ctx.ranges]}, groups: 1-{row_cursor})")


def check_canto(ctx: CantoContext) -> Tuple[int, List[str]]:
    """
    The mechanical `check` verdicts as (failure count, report lines): the
    ranges TSV's validity, the stage files' row counts and empty rows, and
    each paragraph's word content vs the Norton text.
    """
    failures = 0
    lines = []
    total = len(ctx.italian_lines)
    problem = align.validate_ranges(ctx.ranges, sorted(ctx.paragraphs.items()), total)
    if problem:
        lines.append(f"✗ ranges: {problem}")
        failures += 1
    else:
        lines.append(f"✓ ranges: {len(ctx.ranges)} paragraphs cover lines 1-{total} contiguously")

    entries = ctx.paragraph_groups()
    for stage_label, path, rows_of in (
            ("stage 2", ctx.tercet_path, lambda gs: len(gs)),
            ("stage 3", ctx.line_path, lambda gs: sum(len(g) for g in gs))):
        if not path.exists():
            lines.append(f"- {path.name}: not written yet ({stage_label} runs on the next align.py pass)")
            continue
        texts = path.read_text(encoding='utf-8').splitlines()
        expected = sum(rows_of(gs) for _, _, gs in entries)
        if len(texts) != expected:
            lines.append(f"✗ {path.name}: {len(texts)} row(s) but {expected} expected "
                         f"- delete it and rerun to regenerate")
            failures += 1
            continue
        empties = [n + 1 for n, t in enumerate(texts) if not t.strip()]
        if empties:
            lines.append(f"· {path.name}: {len(texts)} row(s), {len(empties)} blank "
                         f"(pending re-split: rows {empties})")
        else:
            lines.append(f"✓ {path.name}: {len(texts)} row(s)")
        offset = 0
        for r, _, gs in entries:
            n = rows_of(gs)
            seg = texts[offset:offset + n]
            offset += n
            if any(not t.strip() for t in seg):
                lines.append(f"    · paragraph {r.paragraph}: pending "
                             f"({sum(not t.strip() for t in seg)} blank row(s))")
                continue
            msgs = check_paragraph_words(f"paragraph {r.paragraph}",
                                         ctx.paragraphs.get(r.paragraph, ""),
                                         " ".join(seg))
            failures += 1 if msgs else 0
            lines.extend(msgs)
    return failures, lines


def cmd_check(args: argparse.Namespace) -> None:
    ctx = CantoContext(args.cantica, args.canto, args.block_size)
    if ctx.ranges is None:
        sys.exit(f"✗ {ctx.ranges_path}: not found - stage 1 has not run for this canto")
    failures, lines = check_canto(ctx)
    for line in lines:
        print(line)
    if failures == 0:
        print("✓ all word content matches the Norton paragraphs")
    sys.exit(1 if failures else 0)


def cmd_words(args: argparse.Namespace) -> None:
    ctx = CantoContext(args.cantica, args.canto, args.block_size)
    if ctx.ranges is None:
        sys.exit(f"✗ {ctx.ranges_path}: not found - stage 1 has not run for this canto")
    fragments = load_response(args)
    if args.group is not None:
        issues = words_group(ctx, ctx.paragraph_groups(), fragments, args.group)
    else:
        issues = words_paragraph(ctx, ctx.paragraph_groups(), fragments, args.paragraph)
    sys.exit(1 if issues else 0)


def words_paragraph(ctx: CantoContext, entries, fragments: Dict[int, str],
                    para_num: int) -> int:
    """Word-diff a stage-2 response (fragments numbered by group serial)
    against the whole Norton paragraph."""
    entry = next(((r, s, gs) for r, s, gs in entries
                  if r.paragraph == para_num), None)
    if entry is None:
        sys.exit(f"✗ paragraph {para_num} is not in {ctx.ranges_path}")
    r, serial_start, groups = entry
    serials = list(range(serial_start, serial_start + len(groups)))

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
                  f"has no words for these Italian lines (range problem? or a stage-2 "
                  f"boundary mistake - see 'rows -g {serial}')")
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
    return issues


def assess_group_split(ctx: CantoContext, entries, fragments: Dict[int, str],
                       serial: int) -> Tuple[Dict[int, str] | None, int, List[str]]:
    """
    Judge a stage-3 split response (fragments numbered by the group's
    Italian line numbers, 1..n tolerated) against the group's stage-2 row -
    the text split_norton_span was given. Returns (paired fragments,
    issues, report lines); paired fragments is None when the serial is not
    in the ranges. When the fragments reproduce the row exactly but some
    line still got no words, the row itself has nothing for that line: a
    stage-2 boundary mistake if every Norton word sits in some row of the
    paragraph, a range problem if it does not.
    """
    entry = next(((r, s, gs) for r, s, gs in entries
                  if s <= serial < s + len(gs)), None)
    if entry is None:
        return None, 1, [f"group {serial} is not in {ctx.ranges_path}"]
    r, serial_start, groups = entry
    index = serial - serial_start
    group = groups[index]

    nums = [line.line_num for line in group]
    # Tolerate responses numbered 1..n instead of by line number:
    if fragments and not any(k in fragments for k in nums) and len(fragments) == len(nums):
        fragments = dict(zip(nums, (fragments[k] for k in sorted(fragments))))

    issues = 0
    report = []
    for n in nums:
        frag = fragments.get(n)
        if frag is None:
            report.append(f"✗ line {n}: no fragment with this number")
            issues += 1
        elif not frag.strip():
            report.append(f"✗ line {n}: empty fragment")
            issues += 1
    stray = sorted(set(fragments) - set(nums))
    if stray:
        report.append(f"note: fragment number(s) outside this group's lines: {stray}")

    tercets = read_stage_rows(ctx.tercet_path)
    if tercets is None:
        report.append(f"- {ctx.tercet_path.name} not on disk; nothing to diff the fragments against")
        return fragments, 1, report
    offset = sum(len(gs) for _, s, gs in entries if s < serial_start)
    if offset + index >= len(tercets):
        report.append(f"✗ {ctx.tercet_path.name} has {len(tercets)} row(s): row "
                      f"{offset + index + 1} is missing - run 'check' for the row-count problem")
        return fragments, issues + 1, report
    target = tercets[offset + index]
    missing, extra = word_diff(target, " ".join(fragments.get(n, "") for n in nums))
    if missing or extra:
        report.append(f"✗ missing (in the stage-2 row, not in fragments): {format_counts(missing)}")
        report.append(f"✗ extra   (in fragments, not in the stage-2 row): {format_counts(extra)}")
        return fragments, issues + 1, report
    report.append("✓ fragments reproduce the stage-2 row exactly")
    empties = [n for n in nums if not fragments.get(n, "").strip()]
    if not empties:
        return fragments, issues, report
    report.append(f"✗ the stage-2 row itself has no words left for line(s) {empties}")
    norton = ctx.paragraphs.get(r.paragraph, "")
    if not norton:
        report.append(f"    ✗ no Norton paragraph {r.paragraph} in {ctx.norton_file}")
        return fragments, issues + 1, report
    para_rows = tercets[offset:offset + len(groups)]
    unplaced, _ = word_diff(norton, " ".join(para_rows))
    if unplaced:
        report.append(f"    and the Norton paragraph has words no stage-2 row contains "
                      f"({format_counts(unplaced)}) - the range likely runs past the paragraph's "
                      f"end; inspect with 'show -p {r.paragraph}' (step 3 in the docstring)")
    else:
        report.append(f"    but every Norton word sits in some stage-2 row - the stage-2 split "
                      f"drew this group's boundary wrong; re-split the affected paragraphs: "
                      f"align.py {ctx.cantica} -c {ctx.canto} -p "
                      f"{paragraph_span(entries, serial)} (or just rerun align.py - "
                      f"blank rows are retried on their own)")
    return fragments, issues, report


def words_group(ctx: CantoContext, entries, fragments: Dict[int, str],
                serial: int) -> int:
    """
    Word-diff a stage-3 response against its group's stage-2 row: per-line
    word counts plus the assess_group_split verdict.
    """
    entry = next(((r, s, gs) for r, s, gs in entries
                  if s <= serial < s + len(gs)), None)
    if entry is None:
        sys.exit(f"✗ group {serial} is not in {ctx.ranges_path}")
    r, serial_start, groups = entry
    group = groups[serial - serial_start]
    nums = [line.line_num for line in group]
    paired, issues, report = assess_group_split(ctx, entries, fragments, serial)
    if paired is None:
        sys.exit(f"  ✗ {report[0]}")
    print(f"Group {serial} (lines {nums[0]}-{nums[-1]}, paragraph {r.paragraph}) "
          f"vs {len(paired)} numbered fragment(s)")
    for n in nums:
        frag = paired.get(n)
        if frag and frag.strip():
            print(f"  · line {n}: {len(align.word_multiset(frag))} words")
    for line in report:
        print(f"  {line}")
    return issues


# ============================================================================
# Diagnose: the entry point when a canto fails
# ============================================================================

def find_log_failure(ctx: CantoContext) -> Tuple[int, str, Dict[int, str]] | None:
    """
    The last "✗ Stage N failed" in <NN>.log as (stage, detail line, last
    response's fragments). The response is the failing split's final
    attempt ({} when the log has none, e.g. an LLM-connection failure).
    """
    path = ctx.out_dir / f"{ctx.canto:02d}.log"
    if not path.exists():
        return None
    text = path.read_text(encoding='utf-8', errors='replace')
    fails = list(re.finditer(r'^✗ Stage (\d) failed: (.*)$', text, re.M))
    if not fails:
        return None
    last = fails[-1]
    stage, detail = int(last.group(1)), last.group(2).strip()
    responses = list(re.finditer(r'^\s*response: (.*)$', text[:last.start()], re.M))
    fragments = parse_response_text(responses[-1].group(1)) if responses else {}
    return stage, detail, fragments


def paragraph_span(entries, serial: int) -> str:
    """
    A ready-to-use `-p` argument for re-splitting the failing group's
    paragraph plus the next group's (a stage-2 boundary mistake pushes the
    words into the neighbouring group, which may sit in the next
    paragraph): "10" or "10-11".
    """
    for i, (r, s, gs) in enumerate(entries):
        if s <= serial < s + len(gs):
            if serial + 1 < s + len(gs) or i + 1 >= len(entries):
                return str(r.paragraph)
            return f"{r.paragraph}-{entries[i + 1][0].paragraph}"
    return str(serial)


def diagnose_group(ctx: CantoContext, entries, serial: int,
                   fragments: Dict[int, str]) -> None:
    """
    The stage-3 failure view: the failing group's stage-2 row bilingually
    (with the next group's, where the misplaced English usually sits), the
    log's last attempt per line, and the range-vs-stage-2 verdict.
    """
    entry = next(((r, s, gs) for r, s, gs in entries
                  if s <= serial < s + len(gs)), None)
    if entry is None:
        print(f"  ✗ group {serial} is not in {ctx.ranges_path}")
        return
    r, serial_start, groups = entry
    print(f"\nGroup {serial} is in paragraph {r.paragraph} (lines {r.start_line}-"
          f"{r.end_line}, groups {serial_start}-{serial_start + len(groups) - 1})")

    print(f"\nStage-2 rows around the failure (it: Italian line, en: the stage-2 row "
          f"that claims to cover it):")
    index = serial - serial_start
    tercets = read_stage_rows(ctx.tercet_path)
    offset = sum(len(gs) for _, s, gs in entries if s < serial_start)
    for k in range(index, min(index + 2, len(groups))):
        group = groups[k]
        lo, hi = group[0].line_num, group[-1].line_num
        print(f"  group {serial_start + k} (lines {lo}-{hi}, paragraph {r.paragraph}):")
        for line in group:
            print(f"    it {line.line_num}: {line.full_text}")
        if tercets is not None and offset + k < len(tercets):
            print(f"    en {ctx.tercet_path.name} row {offset + k + 1}: {tercets[offset + k]}")
        else:
            print(f"    en {ctx.tercet_path.name} row: - (not on disk)")
        if k == index and fragments:
            print("    last attempt (from the log):")
            for line in group:
                frag = fragments.get(line.line_num)
                if frag is None:
                    frag = "(no fragment)"
                elif not frag.strip():
                    frag = "(empty)"
                print(f"      {line.line_num}: {frag}")

    _, issues, report = assess_group_split(ctx, entries, fragments, serial)
    print("\nVerdict:")
    for line in report:
        print(f"  {line}")
    print(f"\nDig deeper:")
    print(f"  uv run python alignment/debug.py rows {ctx.cantica} {ctx.canto} -g {serial}")
    print(f"  uv run python alignment/debug.py words {ctx.cantica} {ctx.canto} -g {serial} "
          f"--response '...'")
    print(f"Fix (after correcting {ctx.ranges_path.name} if the range is at fault; "
          f"a plain rerun also works - it retries only the blank rows):")
    print(f"  uv run python alignment/align.py {ctx.cantica} -c {ctx.canto} "
          f"-p {paragraph_span(entries, serial)}")


def diagnose_paragraph(ctx: CantoContext, entries, para_num: int,
                       fragments: Dict[int, str]) -> None:
    """
    The stage-2 failure view: the paragraph's boundaries against the
    Italian text and the next Norton paragraph (a tercet off here is the
    usual cause), plus the log's last attempt diffed vs the paragraph.
    """
    entry = next(((r, s, gs) for r, s, gs in entries
                  if r.paragraph == para_num), None)
    if entry is None:
        print(f"  ✗ paragraph {para_num} is not in {ctx.ranges_path}")
        return
    r, serial_start, groups = entry
    total = len(ctx.italian_lines)
    print(f"\nBoundary view for paragraph {para_num} (lines {r.start_line}-{r.end_line}, "
          f"groups {serial_start}-{serial_start + len(groups) - 1}):")
    if r.start_line > 1:
        print(f"  before {r.start_line - 1}: {ctx.italian_lines[r.start_line - 2].full_text}")
    if r.end_line < total:
        print(f"  after  {r.end_line + 1}: {ctx.italian_lines[r.end_line].full_text}")
    text = ctx.paragraphs.get(r.paragraph, "")
    if text:
        print(f"  Norton opens:  {textwrap.shorten(text, 100)}")
        print(f"  Norton closes: ... {textwrap.shorten(text[-100:], 100)}")
    nxt = ctx.paragraphs.get(r.paragraph + 1, "")
    if nxt:
        print(f"  next paragraph {r.paragraph + 1} opens: {textwrap.shorten(nxt, 100)}")
    print(f"  -> the English must start at the range's first Italian line's content and end "
          f"at its last (steps 2-3); a tercet off means edit {ctx.ranges_path.name}, then "
          f"re-split with -p (or blank the paragraph's rows and rerun)")
    if fragments:
        print("\nLast attempt (from the log) vs the Norton paragraph:")
        words_paragraph(ctx, entries, fragments, para_num)


def cmd_diagnose(args: argparse.Namespace) -> None:
    """
    Entry point: name a canto, learn where its last failure is and what to
    look at first - no LLM calls. Reads the failure from <NN>.log when
    present, cross-checks the files on disk (see `check`), and for a
    stage-3 failure shows the failing group's stage-2 row bilingually with
    the next group's - the misplaced English usually sits there - plus a
    range-vs-stage-2 verdict and a ready-to-run `-p` fix command.
    """
    ctx = CantoContext(args.cantica, args.canto, args.block_size)
    print(f"Diagnosing {args.cantica} {args.canto:02d} (block size {ctx.block_size})")
    if ctx.ranges is None:
        print(f"- {ctx.ranges_path.name}: not found - stage 1 has not run; run align.py first")
        return
    failures, check_lines = check_canto(ctx)
    log_failure = find_log_failure(ctx)

    print("\nDisk state (`check`):")
    for line in check_lines:
        print(f"  {line}")
    if failures == 0:
        print("  ✓ all word content matches the Norton paragraphs")

    if log_failure is None:
        if failures:
            print("\nNo stage failure in the log, but the disk state above has problems - "
                  "fix them (the ✗ lines say how) and rerun align.py.")
        else:
            print("\n✓ no stage failure in the log and the disk state is consistent - "
                  "nothing to diagnose")
        sys.exit(1 if failures else 0)

    stage, detail, fragments = log_failure
    log_path = ctx.out_dir / f"{ctx.canto:02d}.log"
    print(f"\nLast failure ({log_path.name}):")
    print(f"  ✗ Stage {stage} failed: {detail}")
    entries = ctx.paragraph_groups()
    if stage == 3:
        m = re.search(r'group (\d+)/', detail)
        if m:
            diagnose_group(ctx, entries, int(m.group(1)), fragments)
        else:
            print("  (could not parse the failing group from the detail - use `rows`)")
    elif stage == 2:
        m = re.search(r'paragraph (\d+)', detail)
        if m:
            diagnose_paragraph(ctx, entries, int(m.group(1)), fragments)
        else:
            print("  (could not parse the failing paragraph from the detail - use `show`)")
    else:
        print("  (stage-1 failure - inspect the ranges with `show`)")
    sys.exit(1)


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

    p_diag = sub.add_parser("diagnose", help="Name a canto: identify its last failure and "
                                             "where to start digging (no LLM)")
    add_canto(p_diag)

    p_show = sub.add_parser("show", help="Print a paragraph's range, Italian lines, "
                                         "groups and Norton text")
    add_canto(p_show)
    p_show.add_argument("-p", "--paragraph", type=int,
                        help="Show only this paragraph number (default: all)")

    p_rows = sub.add_parser("rows", help="Per group: Italian lines vs its stage-2 row "
                                         "and stage-3 rows (eyeball a failing split)")
    add_canto(p_rows)
    p_rows.add_argument("-p", "--paragraph", type=int,
                        help="Show only this paragraph number (default: all)")
    p_rows.add_argument("-g", "--group", type=int,
                        help="Show only this group serial")

    p_check = sub.add_parser("check", help="Cross-check ranges and stage output "
                                           "files against the Norton text (no LLM)")
    add_canto(p_check)

    p_words = sub.add_parser("words", help="Word-diff a numbered split response - whole "
                                           "paragraph (-p) or one group (-g)")
    add_canto(p_words)
    p_words.add_argument("-p", "--paragraph", type=int,
                         help="Paragraph number to diff a stage-2 response against")
    p_words.add_argument("-g", "--group", type=int,
                         help="Group serial to diff a stage-3 response against its "
                              "stage-2 row (fragments numbered by Italian line number)")
    p_words.add_argument("--response",
                         help="The model's numbered response (default: read from stdin; "
                              "a `response: '...'` line copied from the log works as-is)")

    args = parser.parse_args()
    if args.command == "words" and args.paragraph is None and args.group is None:
        p_words.error("words needs -p PARAGRAPH or -g GROUP")
    n_cantos = len(align.dante_corpus.api.cantos(args.cantica))
    if not 1 <= args.canto <= n_cantos:
        parser.error(f"canto must be 1-{n_cantos} for {args.cantica}")

    {"diagnose": cmd_diagnose, "show": cmd_show, "rows": cmd_rows,
     "check": cmd_check, "words": cmd_words}[args.command](args)


if __name__ == '__main__':
    main()
