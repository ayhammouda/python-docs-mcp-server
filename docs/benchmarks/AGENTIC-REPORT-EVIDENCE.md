# Exported report measurement evidence

Reports inspect the exported run directory. They do not contact a provider,
open a local inference ledger, or query a Git database to establish execution.
The methodology document and model matrix can also be supplied from the export.

The Air-gapped arm section reports the observed host checks even when every
local trajectory was discarded. Both the claims and air-gapped sections list the
destinations the proxy refused. Only completed trajectories contribute scores.

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

The scorer and report also reconcile the vote inventory in both directions.
Every receipt declared by a covered invocation must retain its exact vote file,
and each present vote in an invocation with a receipt manifest must be declared
there. Receipt IDs, file paths, digests, targets, passes and attempts must agree,
including earlier attempts superseded by a later batch or invocation. Omission
of an execution-issued pass-1 vote makes the answer denominator and affected grading
evidence unavailable; it never turns issued work into a pending answer or a
smaller sample. Confirmed ledger bookings remain visible without a per-answer
rate. Unsubmitted work with no declared receipt can still be pending. Older
receiptless records retain their technical compatibility rules.

This check shares one bounded capture of invocations, votes and receipts with
grade replay. A missing issued answer pass-2 record makes real-answer test–retest
evidence unknown in `agentic-score` and the report while preserving supported
pass-1 grades, gates, ledger costs and eligible report/README comparisons.
Each judge's repeat count includes only answers with a score from that judge
on both passes. A repeat by one judge does not increase another judge's count.
The scorer still validates the remaining captured replies, never reconstructs
the missing vote from its receipt, and distinguishes the omission from a repeat
that was never issued. Malformed grading, unsafe paths and key-shaped data still
stop scoring before writes. Probes and key audits do not contribute to
the completed-answer denominator. Missing reference inventory withholds final
and reference-sample evidence while retaining verified independent panel
observations for G2.

Execution history is stored under
`grading/executions/<grading_run_id>/<judge>/<item>--a<attempt>.json`.
`grading/runs/<grading_run_id>.json` records the history file hashes in
`execution_results`. Older exports without these receipts retain their technical
scores and descriptive comparisons, with grading provenance labelled unknown.
Adding a live API label to an older artifact does not supply the missing history.

The raw report keeps saved C2/C3 intervals separate from replayed claims. When
current final-grade evidence cannot reproduce a comparison, a finite saved
interval may remain as a **recorded diagnostic (unverified)**, including a
recorded failed comparison. It is historical display data, not a fresh
measurement or evidence for a positive or negative public comparison. It may
differ from current persisted scores; neither the saved interval nor its verdict
repairs missing votes, supplies G4, establishes a paid-answer denominator, or
enters the README. Replayed quantities take precedence when independently
available. Both displays require matching compared coordinates and a complete,
valid scalar inventory; C2 needs counts and C3 still needs verified local offline
evidence. Invalid booleans, non-finite intervals and invalid scores/counts cannot
supply a diagnostic comparison. The recorded verdict must also agree with its
interval under the existing pass rule. Reporting does not rewrite the saved summary.

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
A frozen scripted adapter marks a demonstration even if the registered panel
is incomplete. Without three seats and one reference judge, the report and README
label grading execution unknown and withhold comparisons.
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
Before reducing rows to statistical coordinates, both primary and retained
comparisons check the declared `cell` and `competitor_id`, canonical
`system--family--rN` spelling (positive integer repeat), system, family and
corpus ID against each captured transcript's directory and filename. These
checks use the already captured records; a second read cannot replace them.
The scorer and report admit each comparison before computing panel and
single-judge C2/C3 statistics or G4. An ineligible comparison supplies no G4
verdict; with no evaluable comparison G4 is unavailable and the outcome cannot
be Validated. A hosted plan conflict does not invalidate a separately matched
local comparison's air-gap evidence. Local cell ownership, attempts, check
coverage and egress must still reconcile.
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
still counts. Scoring, the ordinary report and the README share the same
final-first selector over captured bytes and digests, grouped by target, judge,
effort, pass and item. All takes retain their version and attempt history;
only the selected take supplies reply statistics. An unselected historical retry
with an unscoped empty receipt manifest can remain technical history. Declared
receipt inventories still require every issued vote and digest to match, and a
selected take cannot bypass its declared receipt. Scripted evidence remains a
demonstration and cannot establish a hosted denominator or public comparison.
Genuinely pending cells are excluded and zero completed finals have
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

Calibration effort and held-out repeats have their own evidence requirements.
New workspace `tuning.json` and `g1.json` exports retain a versioned `source`
capture: exact frozen input bytes, candidate vote files, request documents,
invocations and any selected execution receipts. The producer and portable
reader use the same final-attempt selection, reply parsing, core/nuance mapping
and registered primary/fallback composition rules. Tuning trials are recomputed
from those attempts in configured effort order; each chosen effort must match
the run's frozen judge settings. Disclosure retains every verified tuning run,
including unsuccessful effort trials and trials of a registered fallback that
was not selected. A failed trial does not establish the selected effort; a
matching successful trial is still required. Held-out pass-1/pass-2 scores and coverage come
from the selected attempts on identical frozen requests. A copied observation
or two agreeing scalar summaries cannot establish a repeat measurement.

Frozen YAML readers allow ordinary aliases and merges within finite construction
limits: 64 MiB per document, 100,000 nodes including alias references, 64 levels
of nesting or merge dependencies, and 10,000 inherited mapping pairs across all
merges. Merge sizes are checked on the shared node graph before any mapping is
flattened, including pairs later discarded by an override. Inputs exceeding
these limits fail with a content-free validation error during production or
replay; a bounded credential scan alone does not establish safe construction.

Reports also check the disclosure projection against the original digest-named
exports in `grading/disclosure/g1/`. A missing, invalid, hash-mismatched or
contradictory declared original withholds G1 and reference eligibility for the
affected composition; cached disclosure scores cannot replace it. Other
independently supported compositions remain available. Exports predating archive
declarations can still establish results from sufficient frozen score evidence.
An unreadable archive cannot identify its affected compositions, so it withholds
eligibility across the declared set. Missing historical attempt proof leaves
tuning or repeats explicitly unknown while independently supported
G1 accuracy, reference checks and coverage remain available. Missing repeat
history is distinct from a complete capture proving zero pass-2 records.
Calibration joins each declared receipt to its invocation and vote coordinates,
digest, registered model, effort and frozen request. Removing an issued repeat
vote or receipt makes that judge's repeat coverage unknown, preserving supported
pass-1 G1 and reference results and other judges' independently known coverage.
Unallocated efforts, probes and rounds do not count as missing issued work.
Replay uses the captured corpus, prompt, schema and tuning questions without
an index or provider access. Neither source captures nor their hashes
authenticate provider contact.
