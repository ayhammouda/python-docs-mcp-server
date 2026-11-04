# Exported report measurement evidence

Reports inspect the exported run directory. They do not contact a provider,
open a local inference ledger, or query a Git database to establish execution.
The methodology document and model matrix can also be supplied from the export.

The Air-gapped arm section reports the observed host checks even when every
local trajectory was discarded. Only completed trajectories contribute scores.

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

Technical rescoring also checks withholding against the saved response. A
successful, schema-valid judge reply with an ordinary stop cannot be withheld
using only `refusal: true` and `withheld: refusal`: mixed text/refusal output
needs its matching captured raw result. Without that evidence, rescoring rejects
the ambiguous record before publishing scores; it does not invent a vote from
the text. Refusal-specific stops, plain non-vote refusal text with its retained
refusal flag, and terminal errors remain usable legacy technical data. Those
legacy flags are not independent proof of a refusal or of provider contact.
Matching captured scripted rules can support technical rescoring, including
older vote records without the normalized refusal flag, but never establish
hosted execution or eligibility for public comparisons. Compact receipts without
raw output cannot resolve an ambiguous mixed refusal.

The technical report uses the same bound scripted result for pass-1 diagnostics
and real-answer test–retest, reading each vote and receipt once per capture.
It normalizes the captured rule without compiling or executing its selector.
Unambiguous historical scripted replies remain useful without receipts. Scripted
grading, including a mixed hosted/scripted panel or reference, keeps hosted
disclosures and per-graded-answer cost denominators unavailable.

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

With a complete audit, each retained comparison reconciles its own models and
necessary systems after removing flagged questions. An unrun repeat on a flagged
question or an unrelated system cannot withdraw a supported comparison. Missing,
extra or duplicate cells on the unflagged compared arms remain ineligible.
Comparisons use actual adjudicated grades (including reference
settlements and frozen core/nuance weights), verified retained counts and current
offline evidence. The same scorer C2/C3 and single-judge sensitivity primitives
apply. Missing grades or counts remain not evaluable; removing every question
does not create a passing empty comparison. Missing or inconsistent audit data
never silently means zero flagged keys.

Booked grading dollars come from unique, consistent reservations and settlements
in the bounded capture of `grading/ledger-events.jsonl`. The receipt join and cost
disclosure share batch accounting checks. Duplicate units, mismatched identities
and inconsistent item totals cannot supply a booking total. Valid conservative
charges for paid failures or ledger recovery, and zero bookings for unbilled or
refunded results, remain visible even when no final grade is available. Cached
summary totals and reservations alone never establish spend.

Per-graded-answer divides these totals by completed LLM finals replayed from the
same captured requests, selected votes and execution receipts, using the scorer's
final-grade rules. Clearing or removing a saved scoring projection cannot reduce
this denominator. Automatic zeroes are excluded; a completed LLM grade of zero
still counts. Genuinely pending cells are excluded and zero completed finals have
no per-answer ratio. An unknown vote denominator withholds ratios while retaining
confirmed bookings. A missing ledger is unavailable cost evidence, distinct from
a present empty ledger's zero bookings; an inconsistent ledger is explicitly
unverified. These remain unsigned recording consistency checks.

Probe assembly also reconstructs each generation and checker request from the
frozen spec, question and round, using the generated reply text in the checker
request. Selected attempts must match their assigned generator or checker,
model, effort, pass and item, plus the producer's item document and request
metadata. The original reply is reparsed with the request schema and withholding
rules; a saved `parsed` object cannot override it. Recorded execution receipts
are checked when present. These checks cover rejected rounds and drops as well
as accepted probes, before replacing `probes.yml`. Unneeded later rounds may be
absent; an absent or withheld candidate cannot acquire checker approval.
Historical workspaces with exact request documents remain supported without
execution receipts. Missing request proof cannot establish assembly, even when
a cached candidate or verdict is present. This does not authenticate unsigned
archives or retroactively supply source evidence for an existing G1 export.
