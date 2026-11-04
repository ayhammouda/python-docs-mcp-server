# G1 handoff evidence

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
