# Exported report measurement evidence

Reports inspect the exported run directory. They do not contact a provider,
open a local inference ledger, or query a Git database to establish execution.
The methodology document and model matrix can also be supplied from the export.

Public comparisons require evidence for every contributing panel vote and any
selected reference vote. Each captured vote must match its frozen judge identity,
invocation, host execution receipt, reservation, and paid batch settlement item.
The receipt retains the normalized provider result, including the raw item line,
and binds it to the vote bytes. The report uses the production provider result
normalizers to reconcile item identity, model, status, text and usage. Duplicate
reservations or settlements, failed or free-only items, foreign identities,
incomplete invocations and missing history cannot establish live grading.

Execution history is stored under
`grading/executions/<grading_run_id>/<judge>/<item>--a<attempt>.json`.
`grading/runs/<grading_run_id>.json` records the history file hashes in
`execution_results`. Older exports without these receipts retain their technical
scores and descriptive comparisons, with grading provenance labelled unknown.
Adding a live API label to an older artifact does not supply the missing history.

C2 counts must match the actual counter's per-call results and method identity.
The report rebuilds each count assignment from the exported transcript, system
prompt and tool schemas, including raw tool argument handling. It checks the
frozen assignment in `grading/count-inputs.json`, its input hash, the archived
result under `grading/counts/<count_run_id>/`, the current `tokens/` projection,
and the result hashes and counters in `grading/count-runs/<count_run_id>.json`.
Totals must equal the per-call input/output sums. Empty output, rejected calls,
payload diagnostics, retry counters and clamped output differences must agree
with the assigned trajectory. Counting uses the free count endpoint and has no
inference ledger reservation.

These checks establish consistency of local artifacts. Hashes are ordinary
SHA-256 digests, not cryptographic signatures or independent provider receipts.
A recording-only SDK can produce coherent synthetic evidence for tests. The
report cannot independently authenticate a hosted request or recompute provider
token estimates without contacting that provider. Missing evidence withholds
public comparisons while keeping the raw technical report useful.
