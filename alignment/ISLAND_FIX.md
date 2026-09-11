# Requirements: Make Island Detection Reachable

Status: implemented in `align_canto.py`; acceptance criteria (section 6)
measured. Criteria 1, 2, 5 passed (gemma4 92→136/136, Exceeded flat/down for
gemma4/luna/terra). Criterion 3 (ministral) failed and ministral is dropped
from future benchmark runs — see section 6 item 3. Criterion 4 passed for
luna (Failed 7→5) but not terra (Failed 3→5); this small terra regression is
unresolved and not yet judged.

Implementation notes: the `#`-marker machinery (`is_block_complete()`) and the
never-called `consume_matched_text()` were deleted rather than repaired, so R6
is satisfied by removal. Offsets are measured with `count_words()`, which
counts word tokens only, so punctuation preceding a span does not register as
displacement. `--window-words` (default 20) and `--strict-prefix` implement R2
and R9; coverage (R10) is printed to the console and the log.

## 1. Background

`is_block_complete()` (island detection) is dead code: `Island: True` occurs 0
times across all 8 full-canto runs. See [MEMO.md](MEMO.md) "Structural
analysis" Finding 1 for the measurement and [ALGORITHM.md](ALGORITHM.md)
"Known issue" for the summary.

Cause chain, in `align_canto.py`:

1. The extraction prompt instructs the model to "Start from the very
   beginning of the Norton text" (`align_canto.py:194`).
2. Validation accepts an extraction only if
   `norton_text.startswith(extracted)` (`align_canto.py:263`, with a
   punctuation-normalized fallback at `align_canto.py:272`); otherwise it is
   rejected as "Not at beginning" and a retry is burned.
3. `is_block_complete()` is called only on an accepted extraction
   (`align_canto.py:359`), so its input is always a contiguous prefix of the
   remaining paragraph text. Replacing those words with `#` always yields one
   contiguous `#` region at the start, so no `#` can follow the first
   unmatched character and the function always returns `True`.

The island mechanism exists to handle Italian/English word-order divergence,
but every input that could exhibit divergence is rejected before it reaches
the mechanism. gemma-4-31b-it's dominant failure mode (77 "Not at beginning"
rejects) consists largely of extractions that are verbatim and semantically
correct but not positioned at offset 0 — currently thrown away.

## 2. Goal

Turn an off-position but verbatim extraction from a *rejection* into a
*signal* that the current block needs another Italian line, which is what the
island mechanism was designed to express.

### Non-goals

- Sub-block (per-line) alignment inside a multi-line block. A block still maps
  N Italian lines to one contiguous Norton span. Finding 3's granularity
  problem is not addressed here.
- Replacing the core algorithm with embedding-based DP or paragraph-level
  correspondence extraction (MEMO.md "Redesign directions"). This change is a
  cheaper intermediate step and does not preclude those.

## 3. Design note: only one span is live at a time

Each attempt queries the LLM with the whole current block (N Italian lines)
against the same remaining paragraph text and receives a single extracted
string. When the block finally completes, exactly one span is in play, and it
must be at offset 0. Therefore:

- No union-of-spans bookkeeping is needed.
- `AlignmentBlock.matched_text` stays a single string.
- Text consumption (`align_canto.py:364`) stays a prefix slice.

Given a single span, "no island" is equivalent to "the span starts at offset
0". The `#`-marker machinery is an indirect way of computing that, and can be
replaced by a direct offset comparison.

## 4. Functional requirements

**R1 — Windowed position check.** Replace the strict prefix check with: the
extraction must occur verbatim within the remaining paragraph text at some
offset `o` with `0 <= o <= W`. Reject if not found at all (existing
hallucination check) or if `o > W`.

**R2 — Search window `W`.** `W` bounds how far into the remaining text an
extraction may start. Without it, an extraction matching incidentally far
later in the paragraph would be treated as an island and grow the block until
`MAX_BLOCK_LINES`, converting "Not at beginning" rejects into "Block exceeded"
skips. `W` is measured in words, configurable, default to be chosen from
measured offsets (see R8). Suggested starting value: 20 words.

**R3 — Completion rule.** A block is complete iff the accepted extraction is
at offset 0. If `0 < o <= W`, the block is incomplete: log the island event
and add the next Italian line, without consuming a retry attempt.

**R4 — Retry accounting.** An off-position extraction within `W` must not
count against the 3-attempt retry budget; it is a successful extraction with
an incomplete block. Only parse failures, empty extractions, hallucinations,
length-ratio violations, and `o > W` consume attempts.

**R5 — Prompt change.** Remove "Start from the very beginning of the Norton
text" from the extraction prompt (`align_canto.py:194`); it contradicts R1 and
biases the model toward the failure this change is meant to exploit. Keep the
verbatim-copy and single-line-scope instructions.

**R6 — Word-boundary matching.** If the `#`-marker implementation is retained
rather than replaced by an offset comparison (see section 3), fix
`str.replace()` substring matching (`align_canto.py:106-107`): short words
(`a`, `I`, `in`) currently overwrite characters inside earlier words and would
produce spurious islands once islands can fire. Prefer replacing the mechanism
with a direct offset check.

**R7 — Preserve existing validation.** Empty-extraction rejection,
hallucination (verbatim existence) rejection, and the 2.0 length-ratio check
are unchanged. `MAX_BLOCK_LINES = 6` and the on-failure paragraph-preservation
behavior are unchanged.

**R8 — Logging.** Log the matched offset `o` (in words and characters) for
every accepted extraction, including `o == 0`, so the distribution of offsets
is measurable and `W` can be tuned from data rather than guessed. Log island
events distinguishably from retries.

**R9 — Baseline switch.** Provide a flag (e.g. `--strict-prefix`) that
restores the current behavior (`W = 0`, original prompt), so the change can be
A/B tested against the existing numbers on identical inputs.

**R10 — Coverage metric.** Emit per-line coverage (Italian lines that ended up
in an output block, out of the canto total) in the tool's own output. This is
already a MEMO.md "Next steps" item and is a prerequisite for evaluating this
change, since `italian_idx` reaching the end does not imply lines were
covered.

## 5. Known limitation not addressed

The extraction prompt passes only `norton_text[:500]` to the model
(`align_canto.py:189`). A correct span lying beyond that cutoff is
unreachable regardless of this change. Out of scope here; note it if it shows
up in the offset distribution from R8.

**R8 result:** it does not show up. Max accepted offset across all four
window-mode logs was 50 characters (11 words, gemma4) — nowhere near the
500-char cutoff. Not a live concern for Canto 1; revisit if a later canto's
offsets cluster higher.

## 6. Acceptance criteria

Re-run Inferno Canto 1 in direct-comparison mode with the four models already
measured in MEMO.md, and compare against the recorded baseline:

1. `Island: True` (or its replacement log event) fires a non-zero number of
   times for at least one model — the mechanism is demonstrably reachable.
2. `google:gemma-4-31b-it`: "Not at beginning" rejects drop substantially from
   77, and coverage improves from 92/136 (68%).
3. `ollama:ministral-3:14b`: coverage does not regress below 64/136 (47%).
   **Result: regressed to 31/136 (23%), with Exceeded rising 10 → 15 —
   criterion failed.** Root cause: under islands, a hallucination-prone model
   repeatedly extends a block instead of getting rejected per-line, so
   failures compound until MAX_BLOCK_LINES wipes out several contiguous
   lines at once. Judged a ministral capability ceiling rather than a
   tunable parameter; ministral is dropped from future benchmark runs (see
   `run_models.sh`) rather than retuning the window for it.
4. `openai:gpt-5.6-luna` / `gpt-5.6-terra`: coverage stays at 136/136, and the
   number of failed blocks does not increase (luna 7, terra 3).
5. "Block exceeded" counts do not increase relative to baseline for any model
   — i.e. rejects were not merely converted into skips (the R2 risk).

If criterion 5 fails, `W` is too large; retune from the R8 offset
distribution before concluding the change is unsuccessful.

**Result:** criterion 5 held for gemma4/luna/terra (Exceeded 6/0/0 → 0/0/0)
but failed for ministral (10 → 15). Checked whether `W` was the cause per
the instruction above: it was not — the per-model offset breakdown (see
MEMO.md "Results: search-window mode") shows ministral's islands all sit at
offset 7 words, and gemma4's at 1 and 11, all well inside even an 8-10 word
window. Narrowing `W` would not have removed ministral's islands, only
gemma4's legitimate offset-11 one. The rise in ministral's Exceeded count is
attributed to the hallucination/block-growth interaction (criterion 3), not
`W` being mistuned.

## 7. Expected outcome

This makes the algorithm behave as originally designed. It addresses MEMO.md
Finding 2 (structurally unsatisfiable rejects) and Finding 1 (dead code). It
does not address Finding 3: multi-line blocks will become more frequent and
more meaningful, but the correspondence *inside* a block remains unresolved.
If line-level correspondence is the end goal, the redesign directions in
MEMO.md remain necessary after this change.
