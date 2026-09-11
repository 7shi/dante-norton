# StatusLine: Progress Display in align.py

`align.py`'s progress display follows dante-corpus's `ARCHITECTURE.md` §4
("Live-run observability"), mirroring the pattern used by dante-corpus's
`skel/skel.py` (specifically `skel/driver_build.py`). This note records the
design so a future change (or a similar script elsewhere in this repo) can
follow the same shape without re-deriving it.

## Model calls and progress display

`align.py` calls the model through `llm7shi.Client` directly, and drives an
`llm7shi.statusline.StatusLine` (`ui`) for the whole run, giving a live Rich
progress bar per canto plus a single console every human-facing line and
every streamed model reply shares.

`llm7shi.Client` provides:

- A `file=` sink for streaming model output, which is exactly the hook
  `StatusLine` needs to keep streamed text from clobbering the live bar
  (`Client(..., file=ui.stream)`).
- Its own quality-retry loop (empty replies, repetition, invalid JSON
  against a schema).

`align.py`'s own `MAX_ATTEMPTS` retry loop still wraps every call, because
it checks things `Client` cannot know about: mechanical range validity
(stage 1) and word-multiset equality (stages 2/3). The two retry loops are
independent and both matter.

## `StatusLine` wiring, mirroring `skel/driver_build.py`

- **One `StatusLine` for the whole run** (`ui = StatusLine()` in `main()`),
  not one per canto. It is threaded through every function that needs to
  print or call the model, with `args: argparse.Namespace` and
  `ui: StatusLine` as the first two parameters.
- **A fresh, disposable `Client` per attempt**, not a reused stateful one.
  Every call in this pipeline is single-shot (no multi-turn history to
  carry), so there is nothing a reused `Client` would buy here - this is
  the same shape as `driver_build.py`'s `_try_parse`. `ui.log("")` before
  constructing it is the session-boundary blank line; `ui.stream.end()`
  right after the call flushes the streamed reply's trailing partial line.
- **One progress bar per canto**, opened with
  `ui.progress(len(italian_lines), label=f"{cantica} {canto}/{n_cantos}")`
  and closed automatically at the end of `align_canto`'s `with` block.
  `n_cantos` is the canticle's *total* canto count
  (`len(dante_corpus.api.cantos(cantica))`), not the number of cantos
  selected for this run - so the label reads e.g. `Inferno 5/34` even when
  invoked as `-c 5-10`. This folds the "which canto, out of how many" fact
  straight into the bar's label instead of a separate `[index/total]`
  separator line.
- **The bar's numerator walks the canto's Italian lines** (ARCHITECTURE.md
  §4's "Canticle Canto Line" convention): `run_stage2`/`run_stage3` call
  `prog.update(...)` with the first Italian line number of the
  paragraph/group about to be processed. Stage 1 (one call, whole-canto
  scope) does not update it - there is no per-line granularity to show
  there.
- **No separate stderr stream.** `StatusLine`'s `Console()` defaults to
  stdout; forcing progress text to stderr would just split one run's output
  across two streams for no benefit here (there is no JSONL log or other
  machine-readable stdout this needs to stay clean of). All human-facing
  output - the bar, streamed model replies, and this script's own messages
  - shares the one console.

## The `notify()` helper

```python
def notify(ui: StatusLine, text: str, error: bool = False) -> None:
    (ui.stream.error if error else ui.log)(text)
    log_print(text)
```

`ui.log()` prints a normal line that coexists with the active bar;
`ui.stream.error()` does the same in red, for retries/failures.
`log_print()` is `align.py`'s own pre-existing per-canto trace file
(`<NN>.log`, unrelated to `StatusLine`) - `notify()` keeps writing to both,
same as before this change.

## What was tried and dropped

An earlier version of this change routed progress lines through
`dante_corpus.harness` (`HarnessStatusLine`, `progress_separator`) and
forced everything to stderr. That was reverted in favor of the plan above,
once the user pointed at `skel/skel.py` as the actual reference - the
harness wrapper and the major/minor `[index/total]` separator lines it
introduced are heavier than this script needs; the bar's own label already
carries that information.
