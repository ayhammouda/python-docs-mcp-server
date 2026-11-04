# Exported report measurement evidence

Reports inspect the exported run directory. They do not contact a provider,
open a local inference ledger, or query a Git database to establish execution.
The methodology document and model matrix can also be supplied from the export.

Public comparisons require evidence for every contributing panel vote and any
selected reference vote. Each captured vote must match its frozen judge identity,
invocation, host execution receipt, reservation and settled batch item.
The receipt retains the normalized provider result, including its raw item
line, and binds it to the vote bytes. The report replays the provider
normalizer to check the item, model, status, text, usage and reason for
withholding a vote. A documented refusal, output cap, parse failure or
terminal error can still leave two judges in agreement. The withheld result
must have a matching settlement, including a zero booking for a documented
unbilled error. An unverifiable compact error, a duplicate settlement or a
receipt from a different run leaves the grade unverified.

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

The answer-key disclosure also requires current evidence. A cached `key_audit`
or `claims_without_flagged_keys` entry cannot establish counts, flags or a subset
PASS. Each frozen corpus key needs its selected final `key-audit` reply, the
reference identity and effort, a matching invocation and settled execution
receipt. The original reply must parse to the stored boolean result.

Portable audit inputs live at `grading/items/key-audit/<safe question id>.json`.
They use `JudgeInput.item_evidence` with metadata `corpus_id`, `inputs` (the frozen
input manifest), and `cited_sections`. Each section has `url`, `version`, `text`,
and may retain the production `CitedSection` fields `page`, `anchor`, `heading`.
Sections follow frozen citation order and each citation's version, including on
cross-version questions, with nonempty text and at most 20,000 text characters
in total. The canonical request user is compact, sorted JSON with `question_id`,
`version`, `question`, `answer_key`, and `cited_sections`; the system prompt is
the frozen `key-audit-prompt.md`, and the schema is `key_audit_schema()`. The shared
`audit_request` function reconstructs it without an index, provider or network.
This reader support does not implement the pending key-audit worker target.
Legacy inputs without this evidence remain explicitly unverified and ineligible;
an unsigned but coherent current export can establish internal consistency.
Captured section text is not independently authenticated against Python's site.

With a complete audit, retained comparisons are recomputed from the full planned
cohort minus flagged questions, actual adjudicated grades (including reference
settlements and frozen core/nuance weights), verified retained counts and current
offline evidence. The same scorer C2/C3 and single-judge sensitivity primitives
apply. Missing grades or counts remain not evaluable; removing every question
does not create a passing empty comparison. Missing or inconsistent audit data
never silently means zero flagged keys.

Booked grading dollars come from captured `settle` events in
`grading/ledger-events.jsonl`, including the host's conservative bookings, never
from cached summary totals or reservations. Per-graded-answer divides those same
totals by completed, reconciled LLM finals, as the ordinary scorer does. Automatic
zeroes are excluded; a completed LLM grade of zero still counts. Pending cells are
excluded, zero completed finals have no per-answer ratio, and an unavailable or
inconsistent grade join leaves that denominator unverified. A missing ledger is
unavailable cost evidence, distinct from a present empty ledger's zero bookings.
