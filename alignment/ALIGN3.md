# Checking the align3 split

`align.py` stage 2 writes `<NN>-3.txt`: one line of Norton English per
tercet-sized group of Italian lines. Stage 3 then splits each of those lines
into one fragment per Italian line (`<NN>-1.txt`). When a stage-3 result looks
wrong, the cause is often further upstream - the group it was handed was already
the wrong English text. The tools described here check stage 2 on its own, so a
stage-3 problem can be attributed before anyone tries to fix it.

The check is word-level correspondence: for each Italian word of a group, which
word of the group's English fragment renders it. A group whose English fragment
genuinely belongs to those Italian lines yields a correspondence for nearly every
content word. A mismatched group does not, and that is the signal.

Both tools only read `align.py`'s output and never write to it.

| | script | output |
|---|---|---|
| Generative LLM | [`check_align3.py`](check_align3.py) | `<NN>-3.tsv`, `<NN>-3-words.log` |
| TypeSafe System One | [`check_align3_jev.py`](check_align3_jev.py) | `<NN>-3-jev.tsv`, `<NN>-3-jev.log` |
| Scoring (no LLM) | `--scores` on either script | stdout |

Both checks write the same three columns - `Group`, `Italian`, `English`, one row
per Italian word - so the two can be diffed directly, and one scorer reads both.
`check_align3_jev.py --scores` calls `check_align3.run_scores()` with a filename
tag rather than repeating the code; neither script's table format needed changing
for that, for the reason under "Two rates" below.

The two are not interchangeable in how they are used. `check_align3.py` on Terra
is what produced the whole-poem measurements below. **`check_align3_jev.py` is
not a second checker for the same job: it exists to test whether a future
implementation could skip Terra and run on Jev alone**, which is why it is run on
chosen cantos and deliberately never over everything - see "What the Jev
experiments are for" below.

## Two rates

The filled-in table is an **assignment between two sets of words**, and reading
it from either end answers a different question. Reading only the Italian side
loses half of what the table already knows.

- **deficit** - the fraction of Italian words marked `-`. High when the group's
  English fragment is missing text those Italian lines need.
- **surplus** - the fraction of the group's English words that no Italian word
  claimed. High when the fragment carries text belonging to somewhere else.

A displaced English fragment raises exactly one of the two, so **neither rate
alone finds both failures**. `--scores` computes both from an existing
`<NN>-3.tsv` with no LLM call:

    uv run python alignment/check_align3.py inferno --scores
    uv run python alignment/check_align3_jev.py inferno --scores   # <NN>-3-jev.tsv

Neither rate is read out of the table alone. Deficit counts the table's `-` rows;
surplus subtracts what the table claimed from the group's English fragment in
`<NN>-3.txt`. So **a table needs nothing but the three columns to be scorable**,
and a one-row-per-Italian-word format is no obstacle to measuring surplus - it
never held that information for either checker. This is also why a reverse answer
that `check_align3_jev.py` skips as a duplicate still counts as surplus: no row
claimed the word, so the subtraction finds it.

It writes no file. One TSV goes to stdout - `Canticle`, `Canto`, `Group`,
`Start`, `End`, `ItalianWords`, `EnglishWords`, `Deficit`, `Surplus`,
`SurplusWords`, one row per group across however many cantos were named - and the
closing summary and any errors go to stderr, so `--scores > scores.tsv` keeps a
clean copy. There is no status line; scoring finishes before one would mean
anything. `SurplusWords` lists the unclaimed English in the fragment's own order,
which is usually the displaced text verbatim.

Nothing is kept on disk because nothing needs to be: scoring is pure computation
over two files that are already there, and a stored copy could only go stale
against the table it came from.

## What `-` means

`-` in the English column means the Italian word has no English counterpart in
that fragment. On its own it is a normal outcome, not an error: Norton's prose is
not a word-for-word translation, and Italian is synthetic where English is
analytic. In Inferno 1 group 5, `check_align3.py` marks three:

```
poi       after
ch'       -        the subordinating particle; English "after" covers both
...
là        -        "where" renders "dove"; "là" leaves no separate word
...
il        -        absorbed into "my heart"
```

Only the *rate* of `-` over a group is diagnostic. The same applies to surplus:
an unclaimed `the` or `I` is English grammar supplying what Italian inflects, and
every group has a few.

## Measured over the whole poem

100 cantos, 4,841 groups, `check_align3.py` on `openai:gpt-5.6-terra`.

### Baselines and thresholds

| | median | p90 | p99 | max |
|---|---:|---:|---:|---:|
| deficit | 0.048 | 0.130 | 0.271 | 1.000 |
| surplus | 0.042 | 0.115 | 0.312 | 1.000 |

`--scores` flags a group at each rate's p99: `DEFICIT_FLAG = 0.271`,
`SURPLUS_FLAG = 0.312`. Inferno 1, whose split is believed sound throughout,
peaks at 0.238 deficit and 0.167 surplus over its 46 groups - nothing flagged.

### Result

**74 groups in 31 cantos** (1.5% of groups): 48 flagged on deficit, 49 on
surplus, 23 on both. Densest cantos:

| canto | flagged / groups |
|---|---|
| Inferno 31 | 8 / 52 |
| Paradiso 16, Paradiso 33, Purgatorio 1, Purgatorio 8 | 5 each |
| Inferno 13, Purgatorio 17 | 4 each |

Spot-checking confirmed the flags in Purgatorio 8, Paradiso 16, Paradiso 33,
Purgatorio 17, Paradiso 2, Paradiso 19, Purgatorio 20 and Purgatorio 23. The
recurring cause in the dense cantos is a **stage-1 range error**: a Norton
annotation paragraph (a Latin hymn, a Tennyson quotation, an editorial note) is
mapped onto real Italian lines, and the translation it displaced piles up in a
neighbouring group. Purgatorio 8 groups 6 and 7 are the clearest case, scoring
1.000 on both rates:

```
17 IT: seguitar lei per tutto l'inno intero,   EN: we
18 IT: avendo li occhi a le superne rote.      EN: pray thee,
19 IT: Aguzza qui, lettor, ben li occhi al vero, EN: O Creator,
```

### The two rates are two ends of one displacement

The pipeline conserves Norton's text exactly:

| step | check | result |
|---|---|---|
| stage 2 | Norton paragraph vs. its `<NN>-3.txt` rows | **818 / 818** identical word multisets |
| stage 3 | group's `<NN>-3.txt` row vs. its `<NN>-1.txt` rows | **4,841 / 4,841** identical |

So English is never created or destroyed, only moved: a group with surplus
implies a group with deficit somewhere. Within a paragraph that partner is a
sibling group; when stage 1 mis-ranged the paragraph itself, it is in the
neighbouring paragraph. Of the 26 groups flagged on surplus alone, **18 sit
within two groups of one flagged on deficit**.

This is why the surplus rate matters even though the deficit rate already flags
most affected cantos: the deficit side points at the starved group - often the
annotation - while the surplus side names the group the real text landed in, and
`SurplusWords` prints it.

```
Par 16 g12  deficit=0.000  surplus=0.696
Pur  8 g4   deficit=0.000  surplus=0.750
Par 33 g24  deficit=0.000  surplus=0.558
```

All three are badly wrong and all three are invisible to the deficit rate: every
Italian word still finds some English word, because the fragment is a superset.

### Cross-check: the English/Italian character ratio

Independent of any LLM: characters of English per character of Italian, per
group. Over the poem it is median 1.15, p5 0.97, p95 1.37 - tight enough that a
displaced fragment stands out. It agrees with the two rates but is noisier: of
the groups it puts outside 0.73-1.79, one (Inferno 4 group 52, ratio 1.81) is a
**false positive** - the translation is correct, merely verbose - and the surplus
rate correctly gives it 0.000. One real failure escapes both rates, Purgatorio 20
group 50, with deficit 0.263 and surplus 0.118 just under their thresholds.

Use it as a second opinion, not as the primary signal.

### Known noise

In 485 groups the table names at least one English word that is not in the
fragment (mean 0.12 words per group) - usually an inflected form the model wrote
from memory. `surplus_words()` consumes claims by lowercased token, so such a
claim matches nothing and is ignored rather than cancelling an unrelated word.

## Scope: this checks align3, not align1

Because stage 3 conserves each group's text exactly (4,841 / 4,841 above),
align1 can only *permute* words inside a group, never change which words it has.
Therefore:

- every group-level measure here - both rates, the character ratio - is
  computed from `<NN>-3.txt` and is **mathematically unaffected by align1**;
- an align1 failure is invisible at group level and shows up only as the English
  being distributed badly *across* the group's lines.

The separation is exact, not heuristic. A first pass for align1 failures - groups
whose own ratio is normal and whose deficit and surplus are low, but whose
per-line ratios inside the group are wildly uneven - turns up Purgatorio 16
group 36, Purgatorio 26 group 46, Purgatorio 24 group 18 and Paradiso 28 group 2,
none of which appear among the 74. For example:

```
Pur 24 g18  ratio=1.17  deficit=0.042  surplus=low
  52 (0.38) E io a lui: «I' mi son un che, quando   / And I to him,
  53 (1.92) Amor mi spira, noto, e a quel modo      / "I am one, who, when Love inspires me, notes, ...
```

That measure only catches unevenly-sized misplacements, so four is a lower
bound. Investigating align1 is separate work and is not what these tools do.

## How check_align3_jev.py works

Two requests per group, sharing one state (both texts verbatim plus that pass's
`guidance`):

1. **forward** - one `Choice` per Italian word: which word of the English
   fragment renders it, labels `W00`, `W01`, ..., or `NONE`.
2. **reverse** - one `Choice` per English word the forward pass left unclaimed:
   which Italian word it renders, labels `S00`, `S01`, ..., or `NONE`.

Several Italian words may pick the same English word, so many-to-one falls out of
the forward pass. One-to-many does not, because a `Choice` returns a single
label, and that is what the reverse pass recovers: `del` -> `of the`, `dirò` ->
`will tell`. Only the leftovers are asked about - about 3.7 English words per
group in Inferno 1, against 23.7 words in the fragment - so the second request
costs a fraction of a full reverse pass.

A reverse answer is attached whenever the model prefers an Italian word over
`NONE`; the criteria already offer that way out, so no separate threshold
second-guesses it. The one exception is a word the Italian word already holds: a
second `the` is the fragment's other article, not a second rendering.

`--no-reverse` runs the forward pass alone.

The English words still unattached after both passes are the reverse pass's own
surplus, and the log records them as `[rev] W12=foo -> NONE`. On Inferno 1 that
is 1.6 words per group of 23.7 (median rate 0.077, max 0.143), and they are
`upon the been`, `the the a`, `I the the` - function words, no content words.
That is the same quantity `--scores` computes from Terra's table, so the reverse
pass does not produce information the generative check lacks; it produces it
directly rather than by subtraction. Read off the log this way the `dup` skip
rule under-counts it - a surplus `the` attached-then-skipped as a duplicate is
neither attached nor logged as leftover (37 such skips over Inferno 1's 46
groups) - which is one reason to prefer `--scores`, whose subtraction counts the
skipped word like any other the table did not claim.

## Jev vs. Terra on Inferno 1

993 Italian words in 46 groups, `check_align3.py` on `openai:gpt-5.6-terra` as
the comparison.

### Agreement

| | |
|---|---:|
| Identical answer | 832 (83.8%) |
| Jev's word among Terra's words | +44 |
| Terra multi-word rows | 92 |
| of those reproduced exactly by Jev | 36 |
| `-` rows: Terra / Jev | 54 / 11 |

The two disagree most about `-`. Terra marks articles, reflexives and fused
prepositions as having no counterpart; Jev finds the English word that carries
the grammatical role. Neither the forward `NONE` probability nor any threshold on
it separates the two readings: sweeping `NONE_THRESHOLD` over the log, precision
against Terra's `-` rows stays near 30-38% everywhere, because only 5.4% of rows
are `-` at all and the two populations' probability distributions overlap.

Note this disagreement is about the per-word reading of `-`; the per-group
*rate* is what the split check uses, and Inferno 1's two baselines are close
(Terra max 0.238 deficit / 0.167 surplus, Jev max 0.143 surplus).

### The per-group mean NONE probability

An earlier attempt at a split-quality score, superseded by the deficit and
surplus rates. Over Inferno 1 it runs 0.024 (min) / 0.069 (median) / 0.162 (max),
correlating only 0.35 with Terra's per-group `-` rate. It was never validated
against a known-bad group, and there is now little reason to: the two rates
separate the 74 flagged groups from the rest with no probability threshold at
all.

### Cost and speed

| | Terra | Jev |
|---|---:|---:|
| Canto 1 wall time | 289.5s | 30.6s (forward 15.7 + reverse 14.9) |
| Canto 1 input tokens | not separable | 517,933 |
| Billing | free up to 2.5M tokens/day | $0.042/M input, output not charged |
| Canto 1 cost | $0 | $0.022 |

Terra's daily free quota is the binding constraint on it: the full 100-canto run
consumed 3.63M tokens across two days.

Jev bills the **state once per question, not once per request**. Regressing
Inferno 1's 91 logged requests, `input = n * (state + question)` fits with
R^2 0.974 while `input = state + n * question` fits with R^2 -0.588. This is why
the questions carry the word lists in their `criteria` and the state carries
none: a state-side copy would be paid for by every question, and one of the two
directions' copies would go unread. It also means each pass gets its own
`guidance`, defining only the numbering its own instructions cite.

## Jev on two flagged cantos

Inferno 31 (8 flagged of 52) and Purgatorio 8 (5 of 48), scored from
`<NN>-3-jev.tsv` against the groups Terra's run flagged.

| | Terra flagged | reproduced by Jev |
|---|---:|---:|
| Purgatorio 8 | 5 | **5** |
| Inferno 31 | 8 | 6 |

Purgatorio 8 comes back exactly, group 4 included - deficit 0.000, surplus 0.771
against Terra's 0.750. That is the case the deficit rate cannot see, so the
surplus rate demonstrably survives the trip through Jev's table.

Inferno 31 misses two, and both are groups where Terra itself was barely over its
threshold:

| g | Terra def / sur | Jev def / sur | |
|---|---|---|---|
| 6, 13, 14 | 1.000 / 1.000 | 1.000 / 1.000 | reproduced |
| 15 | 0.043 / 0.385 | 0.043 / 0.359 | reproduced |
| 51 | 0.000 / 0.406 | 0.000 / 0.375 | reproduced |
| 52 | 0.857 / 0.941 | 0.714 / 0.824 | reproduced |
| 9 | 0.286 / 0.050 | 0.238 / 0.100 | **missed** |
| 18 | 0.000 / 0.333 | 0.000 / 0.111 | **missed** |

### What this does and does not measure

Terra produced the 74 flagged groups, so scoring Jev against them measures
**agreement with Terra, not accuracy**: any disagreement counts against Jev by
construction. Inferno 31 is also not among the cantos whose flags were confirmed
by hand, so neither missed group was a verified failure. Reading the two settles
them individually:

**Group 9 (lines 23-25) - Terra's value is the right one.**

```
23 per le tenebre troppo da la lungi,
24 avvien che poi nel maginare abborri.
25 Tu vedrai ben, se tu là ti congiungi,
EN: through the darkness, it happens that thou dost err in thy imagining.
    Thou shalt see well, if thou arrivest there,
```

Nothing renders `troppo da la lungi`. Those four words genuinely have no
counterpart, so deficit 0.286 is closer to the truth than 0.238.

**Group 18 (line 45) - both are wrong and the truth is between them.**

```
45 Giove del cielo ancora quando tuona.
EN: whom Jove still threatens from heaven when he thunders.
Terra surplus: whom threatens he   (0.333)
Jev   surplus: whom                (0.111)
```

`whom` and `threatens` render `cui ... minaccia` from line 44 and are real
surplus; Terra's third word `he` is English supplying the subject that `tuona`
inflects - the normal case described under "What `-` means". The true rate is
2/9 = 0.222, **below** `SURPLUS_FLAG`. Terra cleared the threshold here partly
for the wrong reason.

### The difference is a bias, not an error rate

Both misses run the same direction, and it is the direction the Inferno 1
comparison already showed: Jev marks 11 rows `-` where Terra marks 54, because it
prefers finding the English word that carries a grammatical role over answering
`NONE`. More rows claiming a word means **both rates read lower** - deficit
because fewer rows are `-`, surplus because fewer English words go unclaimed.

Large displacements are unaffected (1.000 is 1.000 in either hand); groups near
the threshold are compressed downward and fall out. The noise floors match
closely, which makes the compression easy to miss:

| | Terra unflagged max def / sur | Jev unflagged max def / sur |
|---|---|---|
| Inferno 31 (44 groups) | 0.182 / 0.217 | 0.176 / 0.217 |
| Purgatorio 8 (43 groups) | 0.227 / 0.185 | 0.143 / 0.233 |

Equal noise floors with a compressed signal argue for **lower** thresholds on
Jev's output, not the same ones. A deficit threshold near 0.23 recovers group 9
while staying clear of both unflagged maxima; no threshold recovers group 18,
whose 0.111 sits under the noise. But that is a threshold fitted to 95 groups in
two cantos, against p99 values drawn from 4,841 - exactly the move that has
failed to transfer before. It is an observation, not a recommendation.

### Verdict

Jev reproduces **the large displacements** - the mis-ranged annotation paragraphs
that make up the dense cantos - and loses groups sitting near the threshold. On
these two cantos that is 11 of 13. Whether either checker is more accurate is not
something this experiment can answer; that needs labels neither of them produced.

Read against the question these runs exist for - whether a future checker can
skip Terra and run on Jev alone - that is a qualified yes for the failure class
that matters and an open problem at the margins, where the obstacle is
calibration rather than the model. See the next section.

Cost: 578,143 + 585,371 input tokens, about $0.049 for the two cantos.

### What the Jev experiments are for

They are not about getting a second opinion on cantos Terra has already scored.
The question they exist to answer is about **future implementation**: can the
Terra step be skipped, and a checker like this be built on Jev alone?

That is why the runs are **spot checks and never a sweep**. Three cantos have
been scored with Jev - Inferno 1, Inferno 31, Purgatorio 8 - and that is the
intended shape: pick the canto that settles a specific question about the
Jev-only path, run it, stop. Sweeping the poem with Jev would cost about $2.20
and answer nothing this question needs, because the thing being tested is whether
Jev's output *behaves* like a usable signal, not what its flag count over 100
cantos happens to be.

**Where the answer stands.** On the evidence here, a Jev-only checker would find
the dominant failure class and miss the margins:

- The mis-ranged annotation paragraphs - the failures that make up the dense
  cantos - come back at 1.000 on both rates, exactly as they do from Terra. A
  Jev-only checker finds these.
- Groups near the threshold are compressed downward and fall out (Inferno 31
  groups 9 and 18). A Jev-only checker misses these at Terra's thresholds.

**The unsolved part is calibration, and it is circular.** `DEFICIT_FLAG` and
`SURPLUS_FLAG` are p99 values of Terra's distribution over 4,841 groups. A
Jev-only implementation cannot inherit them, because Jev reads both rates lower;
it would need its own percentiles, which is exactly the whole-poem Jev run that
the spot-check policy rules out. Breaking that circle - a per-canto baseline
taken from the canto's own unflagged groups, a threshold fitted on a handful of
cantos, or accepting that only the large displacements are caught - is the real
open question for the Jev-only path, and it is not answered here.

So: spot checks, always with a `-c`, each chosen to test something about whether
Jev can stand alone. Anything phrased as "run Jev over everything" is the one
thing this line of work does not do.

## Status: closed

This investigation is finished. What it set out to do - check stage 2 on its own,
so that a stage-3 problem can be attributed before anyone tries to fix it - is
done, and the successor work is checking align1 within the groups it flagged.

**Settled.** The two rates and why one alone is not enough. The whole-poem
measurement: 74 groups in 31 cantos, with the mis-ranged annotation paragraph as
the recurring cause. That align3 and align1 failures separate exactly, and why.
That both rates are computed from the table plus the group's English fragment, so
any three-column table scores the same way. That Jev reproduces the large
displacements at 10x the speed and reads both rates lower than Terra, losing the
groups near the threshold.

**Deliberately left unresolved.** Each of these needs a labelled set that neither
checker produced, and none of them blocks the align1 work:

- The marginal band. Terra's thresholds are p99 values chosen for a 1.5% flag
  rate, so a flag just over them may be spurious (Inferno 31 group 18) and a real
  failure may sit just under (Purgatorio 20 group 50). Reading the disputed
  groups by hand is cheap - they number in the handful - and would settle both.
- **How a Jev-only implementation would set its thresholds.** This is the live
  question for that path and the one thing standing in its way: Terra's p99
  values do not transfer, and deriving Jev's own would take the whole-poem run
  that the spot-check policy exists to avoid. The bias is established, the number
  is not. Purgatorio 17 - the subtle one-clause case rather than a displaced
  annotation - is the canto to learn more on, as one more spot check.
- The false-negative rate of either checker over the poem, which no measurement
  here touches: 74 is what Terra flagged, not what exists.
- `ChoiceAnswer.confidence` is still unused and unlogged.

**Successor.** The four align1 candidates named under "Scope" came out of a
by-product heuristic and were never investigated. That work, and the question of
what align1 looks like inside the 74 groups whose English was wrong to begin
with, belong to a separate document.
