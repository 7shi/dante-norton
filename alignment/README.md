# Alignment Scripts

Alignment scripts for the Italian original text and Norton's English translation.

## Documents

| | |
|---|---|
| [ALGORITHM.md](ALGORITHM.md) | The three-stage pipeline in full, and its Canto 1 results |
| [ALIGN3.md](ALIGN3.md) | Checking the stage-2 split: word correspondence, the deficit and surplus rates, the whole-poem measurements, and the Terra/Jev comparison (closed) |
| [MEMO.md](MEMO.md) | Experimental history across LLM backends, and the model-selection decision |
| [PRIOR_WORK.md](../PRIOR_WORK.md) | The earlier hand- and AI-assisted line splits this work builds on |

This table is what the repo root's README points at instead of listing these
documents itself, so a new document under `alignment/` is added here and the
root README is left alone.

## Overview

The current, recommended approach is `align.py`, a single script that runs a
three-stage pipeline hierarchically decomposing each Norton paragraph down
to one row per Italian line, never extracting or verbatim-matching text -
each stage only rearranges words already known to belong to a given span,
validated by word-multiset equality:

1. One LLM call over the whole canto identifies, for each Norton paragraph,
   the range of Italian lines it corresponds to.
2. Each paragraph's Norton text is split into tercet-sized (3-line, by
   default) groups.
3. Any multi-line group is further split into one fragment per Italian
   line.

See [ALGORITHM.md](ALGORITHM.md) for the full algorithm description and
Canto 1 results (near-exact match against both former fixed gold
references).

Two earlier, superseded approaches are documented but no longer present as
scripts:

- **`align_canto.py`** (single-stage, extraction-only, removed): for each
  Norton paragraph, Italian lines are added to a block one at a time and an
  LLM extracts the corresponding Norton span, with island detection via a
  search window to find block boundaries. See [MEMO.md](MEMO.md) (algorithm
  summary near the top, and "Design: island/search-window fix").
- The original **`align3.py`** (two-stage, extraction-based tercet-first,
  now replaced): stage 1 extracted a whole tercet at once with a fixed
  block size, stage 2 split that fixed span into one fragment per Italian
  line. See [MEMO.md](MEMO.md) "Two-stage, tercet-first alignment" for the
  design rationale and gold-comparison results that motivated moving away
  from extraction entirely.

A later, three-script version of the current approach (`align_ranges.py`,
`align3.py`, `align1.py`, run as three separate commands) has since been
unified into the single `align.py`; see git history for the three-script
sources.

[MEMO.md](MEMO.md) has the full experimental history across LLM backends.
`openai:gpt-5.6-terra` is the sole benchmark model going forward (see
MEMO.md's model-selection decision) - `ministral-3:14b`, `qwen3.6`,
`gemma-4-31b-it`, and `gpt-5.6-luna` were all tried and dropped for
documented reasons.

Two fixed gold references for Canto 1, `01-1.txt` (per-line) and `01-3.txt`
(per-tercet), were used during development - see "Reference Data" below.

## Usage

```bash
uv run alignment/align.py inferno -c 1 --model openai:gpt-5.6-terra
```

Positional argument: canticle name (`inferno`, `purgatorio`, or `paradiso`).
Options:

- `-c`/`--canto`: canto number (e.g. `-c 1`). Omit to run every canto of the
  canticle in turn (canto numbers taken from `dante-corpus`), in one process.
- `--model`: LLM model, passed through to `llm7shi`; use an `ollama:`,
  `google:`, or `openai:` prefix to select the backend. Cloud backends need
  the corresponding API key set in the environment.
- `--temperature` (default: 1.0)
- `--think`: enable LLM thinking (disabled by default - see "Thinking makes
  the rearranging split worse" below)
- `--block-size` (default: 3): number of Italian lines per stage-2 group
- `--test`: process only the first paragraph/group at each stage, for a
  quick local smoke test

### Thinking makes the rearranging split worse

`--think` defaults to off. A same-prompt comparison on Canto 1 with
`openai:gpt-5.6-terra` (ranges identical either way) found that enabling
thinking made the stage 2/3 rearranging split *less* faithful to the
Italian line structure at the hyperbaton case (Canto 1 lines 41-43, see
ALGORITHM.md "Design rationale"): with thinking on, the model misattributed
"the hour of the time and the sweet season" to the wrong clause entirely
(as if it were the subject of "set in motion those beautiful things",
which in the Italian is done by `l'amor divino`/Love Divine, not by the
hour and season) and additionally split "But" off into its own line,
detached from the Italian line it was supposed to translate. With thinking
off, the same phrase landed correctly on its own line, matching the
Italian's postposed-subject line exactly. Elsewhere the two runs were a
similar mix of minor word-order variance either way (expected at
temperature 1.0), but this one case was decisive enough to keep `--think`
opt-in rather than default.

### Removed: `align_canto.py` (single-stage, extraction-only)

`align_canto.py` (superseded, single-stage, extraction-only) has been
removed; see MEMO.md's algorithm summary and "Design: island/search-window
fix" for its design and the `--window-words` / `--strict-prefix` /
`--translate` flags it had, and git history for the source itself.

## Reference Data

`01-1.txt` was a fixed, manually-edited gold reference for Canto 1: Norton's
prose rearranged to exactly 136 lines (one per Italian line), preserving
Norton's wording while reordering words to match Dante's line structure.
`01-3.txt` was the intermediate, 46-line tercet-level version from the same
source (line breaks only, mostly no word reordering - two spots were
hand-corrected during this pipeline's development to reflect a genuine
Italian hyperbaton, see ALGORITHM.md "Design rationale"). Both were carried
over from the `dante-la-el` project's Bard experiments
(https://github.com/7shi/dante-la-el/tree/main/Inferno/Bard/en-norton) - see
[PRIOR_WORK.md](../PRIOR_WORK.md) for how they were produced (Bard's own
two-stage process: tercet-level segmentation, then per-line reordering with
no word substitution - the same two stages the current pipeline automates,
this time reliably) and [ALGORITHM.md](ALGORITHM.md) for the current
pipeline's results against both. Both files have since been removed from
this directory; they only informed development-time validation. `align.py`
now writes its own `<NN>-1.txt` / `<NN>-3.txt` in the same per-line /
per-tercet shape, as real pipeline output rather than hand-edited gold data.

## Output

`align.py` writes to `alignment/<canticle>/`, canto-number-prefixed (e.g.
Inferno Canto 1 -> `alignment/inferno/01-*`):

- **`<NN>-ranges.tsv`**: `paragraph<TAB>start_line<TAB>end_line`, one row
  per processed Norton paragraph (stage 1). A paragraph may be left out (a
  non-translation one, e.g. a stray editorial note): it gets no rows and
  its words are never used.
- **`<NN>-3.txt`**: one line of English (Norton) text per tercet-sized
  group (stage 2), comparable 1:1 by position against the former
  `01-3.txt`. English only - the Italian side is not repeated in this file
  (see `<NN>-ranges.tsv`, and `dante-corpus` for the Italian text itself).
- **`<NN>-1.txt`**: same shape, one line per Italian line in the normal
  case (stage 3), comparable against the former `01-1.txt`.
- **`<NN>.log`**: the full processing trace of all three stages
  (per-paragraph/group progress, retries, rejections), a final
  Italian+English listing per stage, and summary counts (total rows/groups,
  coverage). Unconditionally overwritten on every run (gitignored via the
  repo root `.gitignore`'s `*.log`).

A group or paragraph whose split never validates (after `MAX_ATTEMPTS`
retries) is skipped rather than written as a merged line: its rows stay
blank and the stage moves on - so
`<NN>-3.txt` / `<NN>-1.txt` never contain a row spanning multiple Italian
lines, and their row counts always match the deterministic recomputation
(a stage-2 file may still be shorter than the canto's line count when a
paragraph's range is not a multiple of `--block-size`). Stages write their
files as they fill rows in, so a failed or interrupted run keeps the rows
that validated and leaves the rest blank; a rerun re-splits only the blank
rows.

Progress and errors are also printed to the console as the script runs, via
a live `StatusLine` progress bar.

### Resuming: each stage skips if its output file already exists

`align.py` checks each of the three output files before running that stage:
if `<NN>-ranges.tsv` / `<NN>-3.txt` / `<NN>-1.txt` is already present, that
stage is skipped (no LLM calls) and the file is loaded instead. This lets a
rerun pick up only the stages whose output is missing - e.g. after deleting
just `<NN>-1.txt` to retry stage 3 with a different model, without
re-spending stage 1/2's calls.

Because `<NN>-3.txt` and `<NN>-1.txt` carry no Italian side, a skipped
stage's Italian line groups are recomputed deterministically from the
ranges and `--block-size` and cross-checked against the loaded file's row
count; a mismatch (e.g. the file was produced with a different
`--block-size`, or by an older version that kept merged rows) aborts with
a message rather than silently misaligning - delete the file and rerun to
regenerate it. Delete any of the three files
to force that stage, and every stage after it whose input it feeds, to
rerun; deleting only a later-stage file (e.g. `<NN>-1.txt` while keeping
`<NN>-3.txt`) reruns just that stage.

Blank rows are the finer-grained marker: a stage with blank rows in its
output file does not skip - it re-splits exactly what the blanks mark (a
paragraph at stage 2, a group at stage 3) and keeps every other row
row-for-row. Blanks arise from failed or interrupted runs (stages write as
they fill rows in) or from `-p`, and can be edited in by hand to redo
specific rows.

After hand-editing `<NN>-ranges.tsv` (debug.py's diagnosis often ends with
one), `-p` avoids redoing the whole canto: it blanks the named paragraphs'
rows in the existing files and lets stages 2-3 refill exactly those:

    uv run alignment/align.py purgatorio -c 1 -p 10-11

`-p` needs `-c`, takes the same range grammar, and must be one contiguous
paragraph run covering every paragraph whose range changed (an edited
boundary touches the two paragraphs sharing it). Paragraphs outside `-p`
keep their rows row-for-row; kept rows whose word content no longer matches
their Norton paragraph (a stale or truncated file) abort the patch. A range
edit that changes a paragraph's group count is absorbed by re-laying the
file out (the paragraphs before the run keep its leading rows, those after
it its trailing rows). When `<NN>-1.txt` is absent, stage 3 runs fresh -
and keeps its progress as it goes, so a later failure resumes at the first
blank row.

## Checking the stage-2 split

A stage-3 result that looks wrong is often a stage-2 problem: the group it was
handed was already the wrong English text. Two scripts check stage 2 on its own
by asking, for each Italian word of a `<NN>-3.txt` group, which word of that
group's English fragment renders it. A correctly split group yields a
correspondence for nearly every content word; a mismatched one does not.

Neither script is part of the pipeline - both only read `<NN>-ranges.tsv` and
`<NN>-3.txt`, and never write to them. `--scores` on either then turns a
finished table into two per-group rates, without calling an LLM at all (see
"Scoring a checked canto" below).

    uv run python alignment/check_align3.py inferno -c 1 -m openai:gpt-5.6-terra
    uv run python alignment/check_align3_jev.py inferno -c 1

[check_align3.py](check_align3.py) has a generative LLM fill in a markdown
correspondence table; [check_align3_jev.py](check_align3_jev.py) asks TypeSafe
System One (Jev) typed Choice questions instead, and needs `TYPESAFE_API_KEY`.
Both take the same `-c` / `--block-size` / `--test` options as `align.py`, and
both append per-canto token usage to the repo root's `usage.jsonl`.

They write one row per Italian word, `Group<TAB>Italian<TAB>English`, to
`<NN>-3.tsv` and `<NN>-3-jev.tsv` respectively - same columns, so the two can be
diffed directly - plus a full per-word trace to `<NN>-3-words.log` /
`<NN>-3-jev.log`. `-` in the English column means the Italian word has no
counterpart in that fragment, which is a normal outcome rather than an error. A
group already present in the output file with unchanged Italian words is kept
and skipped on rerun, so an interrupted run resumes; move the file aside to
force a fresh one.

The two are not used the same way. `check_align3.py` on Terra is what sweeps the
poem, and all 100 cantos have a `<NN>-3.tsv`. `check_align3_jev.py` is not a
second checker for that job: it exists to test whether a future implementation
could **skip the Terra step and run on Jev alone**. **So it is run on chosen
cantos: always give it a `-c`, and do not run it over a whole canticle.** Three
cantos have been scored with it so far. ALIGN3.md has the question, the evidence
and what is still unresolved about it (thresholds, mainly).

### Scoring a checked canto (`--scores`)

The filled-in table is an assignment between the group's Italian and English
words, and each end of it answers a different question. `--scores` reads an
already-written table back and reports both, per group:

- **deficit** - the fraction of Italian words marked `-`; high when the group's
  English fragment is missing text those Italian lines need.
- **surplus** - the fraction of the group's English words that no Italian word
  claimed; high when the fragment carries text belonging somewhere else.

A displaced fragment raises only one of the two, so neither rate alone finds
both failures.

    uv run python alignment/check_align3.py inferno --scores
    uv run python alignment/check_align3_jev.py inferno -c 31 --scores

This makes no LLM call and writes no file - the table it reads is left untouched
and nothing new lands on disk - so it is safe to run over the whole poem. The
scores go to stdout as a single TSV (`Canticle`, `Canto`, `Group`, `Start`,
`End`, `ItalianWords`, `EnglishWords`, `Deficit`, `Surplus`, `SurplusWords`),
while the closing summary and any errors go to stderr:

    uv run python alignment/check_align3.py inferno --scores > scores.tsv

`SurplusWords` lists the unclaimed English in the fragment's own order, which is
usually the displaced text verbatim. Over the full 100-canto run this flags 74
groups in 31 cantos.

Either script's table scores the same way, because surplus comes from the group's
English fragment minus what the table claimed rather than from the table alone.
`check_align3_jev.py --scores` therefore calls the same code on `<NN>-3-jev.tsv`,
and needs no API key - but only for cantos Jev has actually been run on, which is
a handful by design, so give it a `-c`. Jev also reads both rates lower than
Terra does, so the thresholds - percentiles of Terra's output - are less
sensitive on its table; see ALIGN3.md.

See [ALIGN3.md](ALIGN3.md) for the method, what `-` means in each script, the
whole-poem measurements behind the thresholds, and why this checks stage 2 only
and not stage 3.

## Requirements

- Python 3.13+ (see `pyproject.toml`)
- `dante_norton` library (parent directory)
- `llm7shi` (model access and progress display)
- An LLM backend: local (Ollama) or cloud (Gemini, OpenAI-compatible) with
  the relevant API key set
- `typesafe-sdk` and a `TYPESAFE_API_KEY`, for `check_align3_jev.py` only

## Troubleshooting

### Splits failing / blank rows left behind

Use [debug.py](debug.py) to investigate - start with `diagnose`, which reads
the last failure from `<NN>.log`, cross-checks the files on disk, and shows
where to dig next (for a stage-3 failure it prints the failing group's
stage-2 row against the Italian lines, plus a range-vs-split verdict):

    uv run python alignment/debug.py diagnose purgatorio 1

Its docstring is the step-by-step playbook (`show` a paragraph against the
Italian original, `check` the stage files without the LLM, `rows` a failing
group, `words`-diff a failed response):

    uv run python alignment/debug.py show inferno 16 -p 10
    uv run python alignment/debug.py check inferno 16

The two common causes: a wrong stage-1 line range at a paragraph boundary
(e.g. Inferno 16's paragraph 10/11), and a stage-2 split drawing a group
boundary one sentence too late while the ranges are fine (e.g. Purgatorio
1's group 29) - both are worked examples in debug.py's docstring. After
fixing `<NN>-ranges.tsv`, re-split just the affected paragraphs with `-p`
(see "Resuming" above); a plain rerun also works, retrying only the rows
left blank.

Backend choice matters more than any flag here - see [MEMO.md](MEMO.md) for
measured differences between models. Stages 2/3 have no window/island to
tune; a split that never validates leaves blank rows behind (the retries
are visible in the log), so a model that often fails the numbered-split
format leaves cantos riddled with blanks - which was the deciding factor
in dropping `qwen3.6` from the benchmark set once the pipeline needed 6+
groups in a single call (see MEMO.md). Rerun to retry the blanks (ideally
with a different model).

### `align_canto.py` matching failures (historical, script removed)

See [MEMO.md](MEMO.md) for measured coverage differences between models on
the same canto. If a local/small model is struggling:

- Try a larger or cloud-hosted model
- Enable thinking with `--think` flag (may improve accuracy but is slower)
- Adjust `--temperature`
- Check the logged offsets: many rejections with "Offset N words exceeds
  window" mean `--window-words` is too tight, while many "Block exceeded"
  warnings alongside island events mean it is too loose
