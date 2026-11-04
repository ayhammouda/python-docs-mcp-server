# G1 handoff evidence

Gate availability follows each gate's evidence. Scoring without `--g1` leaves
G1 and reference-dependent G3 not evaluable; independently supported G2 and G4
remain available. Reports recover a missing `g1-entries.json` projection from
the declared, digest-verified originals. An absent tuning request marks tuning
history unknown without erasing independently verified held-out scores or the
reference check. Contradictory primary scores still withhold eligibility, and
credential validation remains fatal before publication. Missing projections can
be recovered from verified originals. A symlink or oversized projection stops the
report. Plain notes and empty JSON container placeholders can describe unavailable
historical fields. Opaque serialized
contents in an unknown field stop export before any destination is changed.

`agentic-score --workspace DIR --step g1` exports `g1.json`. Each composition's
entry contains `scores.panel` and `scores.judges`, with a pass-1 score for every
retained held-out probe ID and every judge in that composition. An explicit
`null` records an unresolved score and counts as a miss. Pass-2 observations
remain separate under `gate.details.held_out_test_retest`.

`agentic-score --run-dir DIR --g1 PATH` recomputes the selected entry with the
same G1 function used by the workspace exporter, using the run's frozen probe
inventory and construction labels. It checks the panel's overall and per-type
accuracy, each judge's overall accuracy, and zero positive INJ credit for the
panel and each judge. Permitted drops and insufficient demonstration samples
follow the frozen [design §9.3](../AGENTIC-BENCHMARK-DESIGN.md#93-validation-without-humans).
The reference check is recomputed separately from its recorded subset scores.
Scoring still checks the score arrays in historical exports that lack frozen
judge settings. G1, the reference verdict, tuning, and repeat coverage remain
unknown for the current runtime. An export that declares conflicting settings
is rejected.

Report and README publication use that same replay on the current captured
frozen inputs and the run's bounded disclosure file. Matching summaries without
the raw panel and per-judge score arrays cannot establish PASS. Publication
does not read a source workspace, external tuning file, or archived probe
snapshot to fill gaps in the current export. An absent reference check leaves a
separately supported G1 intact; a participating reference must have matching
frozen runtime settings and complete subset scores to support G3.

A saved verdict or aggregate that contradicts its scores is refused before
derived outputs change. Missing judges, omitted probe IDs and malformed scores
are also refused. Older exports without underlying scores or the participating
judges' runtime settings hashes remain readable but cannot establish PASS;
their displayed verdict is unevaluable with a reason. A missing G1 score export
does not invalidate a separately supported reference check.

Source files are captured once, hashed and archived as those exact bytes.
Existing archives are checked before grading writes, so a mismatched archive
cannot partially replace a previous scoring result.

Before scoring or copying a supplied archive, credential inspection covers every
composition's source and tuning source, including unselected fallback history.
It inspects declared JSON vote and receipt replies, including strings retained
inside a structured provider text block, and frozen JSON/YAML inputs.
Ordinary historical summaries remain text. A refused archive leaves existing
grading outputs and immutable archive copies unchanged. The same inspection
applies when publication reads an original archive. Inspection does not expand
YAML alias or merge graphs.

Large source captures use source version 2: digest-addressed bzip2 members,
with original UTF-8 byte sizes and file-name references. Identical files share
a member; every decoded file retains its exact original hash, whitespace and
receipt binding. Captures over 8 MiB use this representation automatically.
Smaller source version 1 captures and historical single-file exports still work.
Copy the one `g1.json` file for `--g1`; no sidecar or original workspace is
needed. Tuning handoff files are self-contained in the same way. The immutable
archive digest names the exact supplied JSON bytes, including packed content.
Report and README replay the same representation as scoring.

Calibration transport is bounded at 1 GiB. Across all compositions and tuning
sources in one document, at most 32,768 source files/inputs and 2 GiB of expanded
UTF-8 are inspected. Each original file/input still has the 64 MiB record limit;
metadata outside packed members retains the 64 MiB ordinary artifact limit.
Worker frames and ordinary report artifacts retain their existing limits.
The registered cohort is 700 probes × three judges × two passes plus 350
reference results; judge output caps are 16,384 tokens, and the explanation
schema imposes no character cap. Aggregate limits also bound accumulated retries
and tuning history; an unbounded history is not a supported export.

Readers expand one member at a time and reject excessive expansion, invalid
UTF-8, mismatched sizes/hashes, trailing or concatenated streams, and file floods.
Encoded members are also bounded by twice their declared expanded size plus
1 KiB, preventing oversized transport padding around tiny source files.
Receipt replay caches parsed invocations in a source-local LRU bounded by 64 MiB
of original manifest bytes. Eviction reparses captured bytes, never current files;
a manifest listing the full cohort is not reparsed for every selected result.
Transports larger than a frame also receive lexical checks before JSON parsing:
at most one million structural/string tokens, depth 128, 64 MiB outside strings,
and 128 MiB per encoded string. This prevents the larger transport allowance
from admitting an arbitrary JSON object/array allocation flood.
Every expanded vote, receipt, request and structured frozen input crosses the
same typed credential inspection as version 1, including unselected compositions
and tuning history. Export validation completes before atomic replacement;
digest-named archive publication remains immutable and safe to retry concurrently.
