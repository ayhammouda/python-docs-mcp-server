# Grading Free-Text Answers With LLM Graders and No Human Calibration

**Date:** 2026-10-29
**Status:** Research notes for the maintainer. They do not change
`docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` or the accepted methodology.
They do not authorize paid calls or public claims (decision 5.17).
**Update 2026-10-30:** design §9 is the decision. Where §4 below differs
from it (for example the `INJ` probe type, the injection boundary and the
G3 sample rule added after review), the design wins; §4 stays as the
reasoning behind it.
**Question:** How should the agentic benchmark grade free-text answers with
LLM or agent graders when no human calibration is available, so that the
correctness numbers are trustworthy enough to publish?
**Builds on:** `docs/research/2026-10-25-multi-llm-mcp-benchmarking.md` §5.1.
That section already covers deterministic checks where answers are crisp, the
judge-bias catalogue (Zheng et al., Panickssery et al., Thakur et al.),
cross-provider juries (PoLL), Inspect's majority-vote panel, and "calibrate
against humans with a chance-corrected statistic". This note does not repeat
those points. It adds what the reference graders actually do, the evidence
behind each grader design choice, and a validation plan that needs no human
labels.

Every factual claim links to its source. **(inferred)** marks my own reasoning
or arithmetic, not something a source states. Sources were read on
2026-10-29. Prompts from public repositories are paraphrased and linked at a
pinned commit, not copied.

---

## TL;DR

1. **No human labels means a narrower claim.** Every benchmark surveyed here
   that validated its free-text grader did so against human labels: SimpleQA
   (informal manual check), HealthBench (physicians), MCP-Atlas, LiveMCPBench
   and MCP-Bench (annotators). MCP-Atlas and LiveMCPBench report 78–81%
   judge–human agreement
   ([SimpleQA](https://arxiv.org/abs/2411.04368),
   [HealthBench](https://arxiv.org/abs/2505.08775),
   [MCP-Atlas](https://arxiv.org/abs/2602.00933),
   [LiveMCPBench](https://arxiv.org/abs/2508.01780)). Without humans, this
   benchmark can show that its graders are *reliable* and that they *pass
   known-answer tests*. It cannot show how often they are wrong on real
   answers ([Bavaresco et al.](https://arxiv.org/abs/2406.18403),
   [Dorner et al.](https://arxiv.org/abs/2410.13341)). The report must say so.
2. **Grade each expected property separately, then compute the score in
   code.** HealthBench grades every rubric criterion in its own binary call,
   and MCP-Atlas grades each claim separately
   ([HealthBench code](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/healthbench_eval.py#L407-L424),
   [MCP-Atlas](https://arxiv.org/abs/2602.00933)). Binary checklists raised
   agreement in two studies ([CheckEval](https://arxiv.org/abs/2403.18771),
   [TICK](https://arxiv.org/abs/2410.03608)). Every corpus question has
   exactly two expected properties, so two booleans plus a contradiction flag
   map cleanly to 1.0 / 0.5 / 0.0.
3. **Give the judges the answer key, not the doc pages.** With a reference
   answer, GPT-4-Turbo missed 11% of injected factual errors, against 54% for
   its best single-answer prompt. The same setup also changed its score on
   37% of meaning-preserving rewrites
   ([FBI, Table 3](https://arxiv.org/abs/2406.13439)). So the probes must
   test both directions.
4. **Fix the output contract.** Use a strict JSON schema with a short
   `reasoning` field first, then boolean fields only. Treat a parse failure
   as a withheld vote, never as a default grade. SimpleQA's reference code
   silently maps an unparseable reply to `NOT_ATTEMPTED`; Inspect marks it
   unscored ([SimpleQA code](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/simpleqa_eval.py#L125-L126),
   [Inspect code](https://github.com/UKGovernmentBEIS/inspect_ai/blob/8fdc4f6be524ddb02aaec85e723a434884f5c837/src/inspect_ai/scorer/_model.py#L330-L360)).
5. **Majority-vote each boolean, not the final grade.** Three binary votes
   always produce a majority, so the three-way-split path in §9.1 disappears
   **(inferred)**.
6. **Validate with three kinds of evidence:** (a) a published suite of 650
   synthetic answers whose correct grade is fixed by construction, (b)
   agreement among the three judges, and (c) agreement with a stronger
   reference grader on real answers. Then check that each claim holds under
   every single judge.
7. **Keep the Opus step, but change its job.** Give it the same inputs as the
   panel. Test it on the probes first. Grade about 300 answers, not 60.
   Measure agreement with Krippendorff's alpha instead of Cohen's kappa. In a
   simulation, the 95% interval on weighted kappa at n = 60 was ±0.11 to
   ±0.32 **(inferred)**.
8. **Gate (proposed):** probe accuracy ≥ 0.95 for the panel and ≥ 0.90 on
   every probe type. Alpha among the judges ≥ 0.80. Alpha between panel and
   reference ≥ 0.80, with a lower 95% bound ≥ 0.667. No C2 or C3 verdict
   may flip under any single judge. Thresholds follow Krippendorff
   ([2004](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x)).
9. **Cost:** about 35 M tokens and about $58 at list prices, or about $29
   through the providers' batch APIs. The Opus reference grader accounts for
   about 70% of that **(inferred estimate)**.
10. **Two repo issues.**
    - `ingest_adjudication_verdicts` tags every ingested verdict
      `scoring_method: "human"`, which would contradict the design's
      anti-claim ([scoring.py](../../benchmarks/scoring.py#L298-L310)).
    - `gemini-3.8-flash` is both an evaluated model and a judge, so it would
      grade its own answers
      ([design §7.1, §9.1](../benchmarks/AGENTIC-BENCHMARK-DESIGN.md#91-judge-panel-call)).

---

## 0. What §9 does today and where it is exposed

The current design ([§9.1](../benchmarks/AGENTIC-BENCHMARK-DESIGN.md#91-judge-panel-call),
[§9.3](../benchmarks/AGENTIC-BENCHMARK-DESIGN.md#93-calibration-gate)) works
as follows:

- **Panel:** `claude-haiku-5-5`, `gpt-6-luna` and `gemini-3.8-flash`, each at
  its lowest effort.
- **Judge inputs:** question, Python version, answer key, expected properties,
  citations, rubric and candidate answer.
- **Judge output:** a JSON grade plus a one-line reason.
- **Aggregation:** majority of three. A three-way split goes to the
  calibration grader.
- **Calibration grader:** `claude-opus-5-5` at high effort, which also sees
  the cited doc pages. It grades 60 stratified answers.
- **Gate:** linear-weighted Cohen's kappa ≥ 0.6. If the gate fails, the
  prompt may be revised once.

Exposures found while reading the repo and the sources:

| # | Exposure | Evidence |
|---|---|---|
| E1 | The calibration grader sees doc pages that the panel does not. Their disagreement therefore mixes rubric differences with input differences. | An agreement coefficient indexes reliability only when coders work independently from the *same* instructions ([Krippendorff 2004, condition 1](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x)). |
| E2 | Agreement statistics measure reproducibility, not truth. | Krippendorff's second condition says the coefficient presumes nothing about whether a label is correct ([same source](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x)). LLM mistakes are becoming more alike as models get stronger ([Goel et al.](https://arxiv.org/abs/2502.04313)). |
| E3 | Cohen's kappa counts a lenient-versus-strict judge pair as agreement. | Krippendorff calls kappa unfit as a reliability index for this reason ([2004](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x)). Thakur et al. replaced kappa with Scott's π in later versions, for the same reason ([v6, footnote 2 and Appendix B](https://arxiv.org/abs/2406.12624)). |
| E4 | n = 60 is too small for a gate at 0.6. | Section 3.7: at n = 60 the 95% interval half-width is 0.11–0.32 **(inferred)**. |
| E5 | `gemini-3.8-flash` grades its own answers, the identical model rather than just the same family. | Design [§7.1](../benchmarks/AGENTIC-BENCHMARK-DESIGN.md#71-models-call-picking-within-the-maintainers-named-families) and [§9.1](../benchmarks/AGENTIC-BENCHMARK-DESIGN.md#91-judge-panel-call). Self-preference tracks self-recognition ([Panickssery et al.](https://arxiv.org/abs/2404.13076)). |
| E6 | Ingested verdicts are tagged `scoring_method: "human"`, and the queue reason says a human must judge. | [`scoring.py` L69–71, L298–310, L453](../../benchmarks/scoring.py#L298-L310). This conflicts with the "never human-graded" anti-claim ([design §2](../benchmarks/AGENTIC-BENCHMARK-DESIGN.md#2-claims-under-test)). |
| E7 | Every corpus question has exactly two `expected_properties`. | Counted from [`corpus.yml`](../benchmarks/corpus.yml): 50 of 50 questions. The mean prompt plus key plus properties plus notes is 263 characters. |

---

## 1. How established evals grade free-text factual answers

### 1.1 SimpleQA and SimpleQA Verified

**Grader prompt**
([`simpleqa_eval.py` L13–92](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/simpleqa_eval.py#L13-L92)):

- **Inputs:** question, gold target and predicted answer. No source documents.
- **Labels:** `CORRECT`, `INCORRECT`, `NOT_ATTEMPTED`, with few-shot examples
  for each.
- **CORRECT** means the answer fully contains the gold information and
  contradicts nothing. Hedging is acceptable when the full gold answer is
  present and nothing is wrong.
- **INCORRECT** means a factual statement contradicts the gold target. A
  hedged wrong statement still counts as incorrect, as does a list of
  alternatives that does not commit to one.
- **NOT_ATTEMPTED** means the gold information is missing and nothing
  contradicts it. Examples: "I don't know", asking for context, and partial
  information without a contradiction.
- Extra rules cover numeric precision, gold targets that contain more
  information than the question asks for, and name typos.
- **Output:** a single letter, A, B or C, with no reasoning.

**Implementation details worth avoiding:**

- The parser takes the first A, B or C anywhere in the reply. With no match,
  it defaults to `NOT_ATTEMPTED`
  ([L125–126](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/simpleqa_eval.py#L125-L126)).
  A grader failure therefore becomes a silent grade **(inferred)**.
- The repo is frozen for new models. Since July 2025 it only hosts
  reference implementations of HealthBench, BrowseComp and SimpleQA
  ([README](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/README.md)).

**Metrics:** percent correct, correct given attempted, and their harmonic
mean (F-score)
([code L164–190](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/simpleqa_eval.py#L164-L190)).
The paper proves that the F-score rewards guessing above 50% confidence. It
also proposes an alternative that penalizes wrong answers
([paper §2.4, Appendix B](https://arxiv.org/abs/2411.04368)).

**How the grader was validated**
([paper §2.2, §2.4](https://arxiv.org/abs/2411.04368)):

- The authors say they did not formally study the grader.
- An informal manual read of 300 completions (100 per label) found two
  disagreements.
- A separate audit gave 1,000 questions to a third trainer. Of the 56 answers
  the grader marked incorrect, 15 were grader false negatives.
- The authors estimate the dataset error rate at about 3%.
- Lesson **(inferred)**: near-perfect agreement on a balanced sample
  coexisted with roughly one wrong label in four inside the minority
  "incorrect" class. Per-label accuracy matters more than overall agreement.

**SimpleQA Verified** ([Haas et al., v2, §3.1–3.2](https://arxiv.org/abs/2509.07968))
changed the autorater prompt in four ways:

- Only the part of the response that answers the question counts. Extra
  information is ignored unless it contradicts the gold answer.
- Hedged lists of candidates are now `NOT_ATTEMPTED` unless the response
  commits to one correct answer.
- It adds more few-shot examples of punts.
- It gives explicit numeric ranges instead of a significant-figure rule.

The authors found these problems by grading each sampled response ten times
at temperature 2.0 and reading the least stable cases. The autorater was
`gpt-4.1-2025-04-14`. The paper reports no human–autorater agreement figure.

### 1.2 HealthBench

**Grader**
([`healthbench_eval.py` L40–89](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/healthbench_eval.py#L40-L89),
[L136–154](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/healthbench_eval.py#L136-L154),
[L407–424](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/healthbench_eval.py#L407-L424)):

- One grader call per rubric criterion. Each call sees the conversation and
  one criterion.
- **Output:** JSON with `explanation` first, then a boolean `criteria_met`.
  A multi-part criterion is met only if every part is met. An "including"
  or "such as" list does not need to be complete.
- Negative criteria are judged on whether the bad behavior occurred.
- The code retries until it gets a valid boolean.
- **Score:** points of met criteria divided by the total positive points. The
  overall score is a mean clipped to [0, 1].
- The April 2026 "HealthBench Professional" options add a GPT-5.4 grader at
  *low* reasoning effort and an optional length penalty per 500 characters
  around a center length
  ([commit](https://github.com/openai/simple-evals/commit/496e4570446e9f0499a00557a231a440c446df76),
  [`simple_evals.py` L71–83, L333–346](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/simple_evals.py#L71-L83),
  [length code L157–164](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/healthbench_eval.py#L157-L164)).

**Meta-evaluation** ([paper](https://arxiv.org/abs/2505.08775)):

- **Data:** physicians graded 34 consensus criteria across 60,896
  meta-examples.
- **Metric:** macro F1 over met and not-met, with a random baseline of 0.50.
- **Result:** GPT-4.1 scored 0.709. Weighted physician averages ranged from
  0.569 to 0.730 by theme.
- GPT-4.1 was picked over o4-mini (0.692) and o3 (0.681). The grader prompts
  had been tuned on GPT-4.1, which may explain part of its lead.
- **Length:** the length–score correlation was small (r = −0.053 for GPT-4o,
  0.123 for o3). Length-controlled win rates fell but stayed substantial.

### 1.3 Humanity's Last Exam judge (reference-guided, structured)

The HLE judge
([`run_judge_results.py` L16–40, L198](https://github.com/centerforaisafety/hle/blob/a59048963171c5b9cfe89fbcb97ddde39b540897/hle_eval/run_judge_results.py#L16-L40))
works as follows:

- It receives the question, the response and a correct answer. It fills a
  strict Pydantic schema in a fixed order:
  1. `extracted_final_answer` (or "None")
  2. `reasoning`
  3. `correct` (yes or no)
  4. `confidence`
- The reasoning must address only meaningful differences from the correct
  answer. It must not solve the problem or argue for another answer.
- Any inconsistency or non-equivalence means "no".
- The default judge is `o3-mini-2025-01-31` (earlier, `gpt-4o`).

### 1.4 Inspect AI `model_graded_qa` / `model_graded_fact`

Source:
[`_model.py` at 8fdc4f6](https://github.com/UKGovernmentBEIS/inspect_ai/blob/8fdc4f6be524ddb02aaec85e723a434884f5c837/src/inspect_ai/scorer/_model.py).

**Templates (L364–418):**

- Both templates come from OpenAI's `closedqa` evals.
- `model_graded_fact` asks whether the submission contains the factual
  content of an expert answer, ignoring style, grammar and punctuation.
- `model_graded_qa` asks whether a submission meets a criterion.
- The default instructions ask for step-by-step reasoning first, then a final
  `GRADE: C` or `GRADE: I`. With `partial_credit=True`, the grader may also
  answer `GRADE: P`.

**Parsing (L432–462):** the regex binds to the *last* `GRADE:` token. The
source comments explain why: an earlier mention echoed in the reasoning, or
injected by the submission, must not win. A verdict outside the offered
grades is a parse failure (`grader_failed`), not "incorrect" (L330–360).

**Panels (L94–100):** a list of models grades independently. A grade must
come from more than half of them, otherwise the sample is unscored. The
`"mode"` reducer is the older alternative. Prior notes cover the
`metadata["panel"]` details.

### 1.5 MCP benchmarks

| Benchmark | How answers are graded | Judge validation |
|---|---|---|
| MCP-Universe | Execution-based format, static and dynamic evaluators; no LLM judge ([abstract](https://arxiv.org/abs/2508.14704)). | Not applicable |
| MCPMark | A programmatic verification script per task ([abstract](https://arxiv.org/abs/2509.24002)). | Not applicable |
| MCP-Bench | Rule checks for tool-name validity, schema compliance and execution success. Plus an `o4-mini` judge scoring sub-dimensions 1–10, normalized per axis. Rubric order is shuffled five times and the scores averaged ([§5.1–5.2](https://arxiv.org/abs/2508.20453)). | Across three judge models, the coefficient of variation went from 16.8% to 15.1% with shuffling and averaging. Human agreement on a 0–2 scale went from 1.24 to 1.43. The study used 50 tasks; the human sample size is not stated ([§6.4, Table 7](https://arxiv.org/abs/2508.20453)). |
| LiveMCPBench (LiveMCPEval) | A DeepSeek-V3 judge sees the task, *human-verified key points*, the trajectory and tool descriptions. It outputs reasoning, then Success or Failure ([paper](https://arxiv.org/abs/2508.01780)). | 78.95% agreement with humans in the body text, 81% in the abstract (inconsistent). Claude-Opus-4 as judge scored only 60–70%, which the authors attribute to long trajectories. The sample size is not stated. |
| MCP-Atlas | The ground truth is split into atomic claims. A Gemini 2.5 Pro judge scores each claim 1, 0.5 or 0. Coverage is the mean, and a task passes at 0.75 ([§4.1, App. B, App. E](https://arxiv.org/abs/2602.00933)). | 100 tasks, three annotators: 78% agreement with the majority human vote. Rankings at thresholds of 0.65 and 0.85 correlate at ≥ 0.98. |

The pattern **(inferred)**: when the answer can be checked by a script, MCP
benchmarks use a script. When it cannot, they decompose it into key points or
claims and validate the judge against humans. This benchmark's
`expected_properties` play the role of LiveMCPBench's key points and
MCP-Atlas's claims.

### 1.6 Other graders, only where they add something concrete

- **PoLL** ([§3.1, Tables 1 and 4, §4.5](https://arxiv.org/abs/2404.18796)):
  - The QA judges were reference-based and binary (True or False), and their
    votes were combined by majority ("max voting").
  - Cohen's kappa against humans: 0.763 vs 0.627 for GPT-4 on NQ, 0.906 vs
    0.841 on TQA.
  - The panel was prompt-sensitive: with GPT-4's tuned prompt, PoLL fell to
    0.699 on NQ.
  - Lesson **(inferred)**: freeze the prompt before validating, and validate
    the exact prompt you ship.
- **JudgeBench** ([Tables 1–2, §4](https://arxiv.org/abs/2410.12784)):
  - These are pairs with objectively correct answers, judged in both orders.
    An inconsistent pair counts as wrong.
  - GPT-4o scored 50.86–56.57% against a 50% random baseline. o1-preview
    scored 75.43%.
  - Strong models are not automatically good judges of correctness on hard
    items.
- **Prometheus** ([abstract](https://arxiv.org/abs/2310.08491)): matches
  GPT-4 only when given a reference answer and a rubric. In FBI, Prometheus 2
  kept scores unchanged on only 38% of meaning-preserving rewrites
  ([FBI](https://arxiv.org/abs/2406.13439)). No reason to adopt it here
  **(inferred)**.
- **G-Eval** ([paper §2](https://arxiv.org/abs/2303.16634)): uses auto
  chain-of-thought plus a probability-weighted sum over score tokens. This
  needs token log-probabilities, which the prior notes found most reasoning
  APIs do not expose. Auto chain-of-thought did not reliably help
  ([Chiang & Lee](https://arxiv.org/abs/2310.05657)). Not adopted
  **(inferred)**.

---

## 2. Grader design choices and the evidence for each

### 2.1 Reference-guided vs reference-free

- **Evidence:**
  - On math questions, GPT-4's grading failures fell from 14/20 with the
    default prompt to 6/20 with chain-of-thought and 3/20 with a reference
    answer ([Zheng et al., Table 4](https://arxiv.org/abs/2306.05685)).
  - With a reference answer, GPT-4-Turbo left 11% of factual perturbations
    undetected. Its best single-answer prompt left 54%, and its best pairwise
    prompt 42% ([FBI, Table 3](https://arxiv.org/abs/2406.13439)).
  - The cost: with a reference, GPT-4-Turbo kept its score unchanged on only
    63% of meaning-preserving rewrites, against 71–97% without one (same
    table). A reference makes graders stricter in both directions.
- **For this repo:** reference-guided against the frozen answer key is
  clearly right. It is the only mode in which a cheap judge does not need to
  know the answer itself **(inferred)**. Over-strictness on paraphrases must
  be measured (probe `PARA`, §4.5).

### 2.2 Scale: holistic 3-point, binary, or per-property items

- **Holistic scales:** in FBI, adding a rubric or axes to single-answer
  scoring made detection *worse* (76% of factual errors undetected, against
  54% for Vanilla). Widening a 1–3 rubric to 1–5 helped only slightly
  ([FBI §5](https://arxiv.org/abs/2406.13439)).
- **Binary decompositions:** CheckEval reports that Boolean checklists raise
  average agreement across 12 evaluator models by 0.45
  ([abstract](https://arxiv.org/abs/2403.18771)). TICK raised exact
  LLM–human agreement from 46.4% to 52.2%
  ([abstract](https://arxiv.org/abs/2410.03608)).
- **In practice:** HealthBench (per criterion, binary) and MCP-Atlas (per
  claim, 3-level) both decompose (§1.2, §1.5).
- **For this repo:** ask one binary question per expected property, plus
  binary `contradicts_key`, `wrong_version` and `attempted` flags. Compute the
  1.0 / 0.5 / 0.0 grade in code (§4.3). The methodology's three-level score
  stays exactly as it is **(inferred)**.

### 2.3 Reason before the label, and output format

- **Explanations:**
  - Asking the LLM to explain its rating consistently improved its
    correlation with humans ([Chiang & Lee](https://arxiv.org/abs/2310.05657)).
  - In FBI, explanations helped single-answer scoring a little. Sometimes the
    explanation named the error but the score ignored it
    ([§5.3](https://arxiv.org/abs/2406.13439)).
  - SimpleQA uses no reasoning at all. HealthBench and HLE put the reasoning
    field *before* the verdict (§1.1–1.3).
- **JSON mode:**
  - JSON mode helps classification tasks because it narrows the answer
    space.
  - It hurts when the model writes the answer key before the reasoning key.
    GPT-3.5 did so in 100% of JSON-mode responses on one task
    ([Tam et al., §4.1, §5.2](https://arxiv.org/abs/2408.02442)).
- **Key order per provider:**

  | Provider | Key order in structured output | Source |
  |---|---|---|
  | OpenAI | Keys are produced in schema order; every field must be required. | [docs](https://developers.openai.com/api/docs/guides/structured-outputs) |
  | Anthropic | Required properties come first, then optional ones; schema order holds within each group. Mark all fields required to fix the order. Enum values may differ only in case. | [docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
  | Google | The structured-output page does not state a key-order guarantee. | [docs](https://ai.google.dev/gemini-api/docs/structured-output) |

- **For this repo:**
  - Use a strict schema with all fields required.
  - Put `reasoning` first, capped at three sentences about differences from
    the key (the HLE wording).
  - Use booleans only, which avoids the enum-case issue.
  - Check the stop reason for refusals before parsing.
  - On Gemini, verify the field order in the pilot.

### 2.4 Should the grader see the source doc text or only the answer key?

- **Practice in other evals:**
  - SimpleQA, HLE and PoLL's QA judges see only the gold answer (§1).
  - HLE explicitly forbids the judge from solving the problem.
  - SimpleQA Verified ignores extra information unless it contradicts the
    gold answer (§1.1).
- **Input length:** LiveMCPBench judges struggled with long inputs. Claude
  Opus 4 did worse than DeepSeek-V3, and one judge accepted a task as done
  without the required information
  ([App. F.1](https://arxiv.org/abs/2508.01780)).
- **For this repo** **(inferred)**:
  - Panel judges get the answer key and per-property criteria only, no doc
    pages and no citation list. §9.1 already makes the grade content-only and
    scores citations separately.
  - The reference grader gets the same inputs for the grade. It sees doc text
    only to fill a separate `key_issue` flag that never changes a grade. This
    turns the doc pages into an *answer-key audit*. SimpleQA's audit found
    about 3% data errors ([§2.2](https://arxiv.org/abs/2411.04368)).

### 2.5 Abstentions, "I don't know", and hedging

- **Evidence:**
  - SimpleQA gives abstentions their own label and reports correct,
    incorrect and not-attempted separately (§1.1).
  - Binary 0/1 grading makes abstaining strictly sub-optimal and rewards
    guessing. The authors propose stating an explicit confidence target in
    the prompt ([Kalai et al., §4.1–4.2](https://arxiv.org/abs/2509.04664)).
  - On hedging, SimpleQA and SimpleQA Verified agree that a hedged answer
    committing to the right fact is correct. They differ on uncommitted lists
    of candidates: incorrect in one, not attempted in the other (§1.1).
- **For this repo:**
  - The score scale stays the same: an abstention scores 0.0, as the
    methodology requires.
  - Add a fourth *label* only: `CORRECT`, `PARTIAL`, `INCORRECT`,
    `NOT_ATTEMPTED`. Report the label shares per (model, system).
  - This separates "docs reduce confident errors" from "docs add correct
    answers", which matters for C3 **(inferred)**.
  - Changing the answering prompt to add a confidence target is out of scope,
    because the prompt is frozen.

### 2.6 Verbosity, length and citations

- **Evidence:**
  - A "repetitive list" padding attack fooled GPT-3.5 and Claude-v1 91.3% of
    the time, and GPT-4 8.7% ([Zheng et al., Table 3](https://arxiv.org/abs/2306.05685)).
  - HealthBench measured only small length–score correlations, but still
    shipped an optional length penalty (§1.2).
  - LLM evaluators prefer text with lower perplexity
    ([Stureborg et al.](https://arxiv.org/abs/2405.01724),
    [Wataoka et al.](https://arxiv.org/abs/2410.21819)).
- **For this repo** **(inferred)**. Answers are asked to be short, and the
  grade is content-only, so a length penalty is the wrong tool. Neutralize
  length instead:
  1. The prompt says that length, style, confidence wording and links do not
     matter.
  2. Before grading, mask URLs and doc paths in the answer as `[link]`. This
     removes citation-presence bias and the obvious system-identity leak, and
     §9.2 grounding still reads the unmasked answer.
  3. Probes `PAD` and `PADX` test padding in both directions (§4.5).
  4. Report each judge's Spearman correlation between answer length and grade
     within each question.

### 2.7 Self-preference across model families

- **Evidence:**
  - LLM judges favor models similar to themselves, and LLM mistakes are
    becoming more alike as capability grows
    ([Goel et al.](https://arxiv.org/abs/2502.04313)).
  - GPT-4 shows significant self-preference, driven by familiarity, that is,
    lower perplexity ([Wataoka et al.](https://arxiv.org/abs/2410.21819)).
  - MT-Bench measured win-rate boosts of about 10% (GPT-4) and 25%
    (Claude-v1) for the judges' own answers, but called the data too limited
    to confirm self-enhancement bias
    ([Zheng et al. §3.3](https://arxiv.org/abs/2306.05685)).
- **For this repo** **(inferred)**:
  - Measure self-preference directly (§3.4).
  - Do not let a judge grade answers from the *identical* model:
    `gemini-3.8-flash` is both, which is E5.
  - Treat `gemma4:12b` answers as Google-family for this analysis.

### 2.8 Aggregation and ties

- **Evidence:**
  - Inspect leaves a sample unscored without a strict majority (§1.4).
  - PoLL majority-votes binary QA verdicts and averages 1–5 scores, because
    multi-level scores often lack a majority (§1.6).
  - Majority-voting three or more coders does not change measured
    reliability ([Krippendorff 2004](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x)).
    So report reliability for *individual* judges, not for the majority.
- **For this repo** **(inferred)**:
  - Take the majority of the three judges *for each boolean field*, then
    compute the grade.
  - With binary fields and three judges, a tie cannot happen.
  - If errors are independent with rate e per field, item-level voting errs
    with probability about 3e². A grade-level vote can produce a three-way
    split even when two judges agree on most fields.
  - A withheld vote (refusal, or no valid output after two retries) leaves
    two votes. If they agree on every field, use them. Otherwise the
    reference grader decides, through the existing queue.
  - Keep every individual vote in the record.

### 2.9 Per-property checklist mapped to 1.0 / 0.5 / 0.0

- MCP-Atlas maps claim scores to a coverage fraction with a pass threshold.
  HealthBench sums criterion points (§1.2, §1.5).
- With exactly two properties per question (E7), the grade follows the
  methodology's wording:
  - Both met and nothing contradicted: 1.0.
  - One met: 0.5, because it "misses a required nuance".
  - Not attempted, a contradiction or version error, or no property met:
    0.0, because it is "incorrect … or answers the wrong version"
    ([methodology](../benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md#correctness-scoring)).
- The expected properties are terse labels, for example "both APIs" or "old
  version distinction". They should be expanded once into full-sentence
  criteria, using the answer key and ambiguity notes, and frozen
  **(inferred)**.

### 2.10 Judge effort

The evidence points both ways:

- **For reasoning:** reasoning models judged hard objective pairs far better
  (o1-preview 75.43% vs GPT-4o 50.86–56.57%,
  [JudgeBench](https://arxiv.org/abs/2410.12784)). OpenAI's newest
  HealthBench option uses a reasoning grader at low effort, not none (§1.2).
- **Against assuming it:** GPT-4.1 beat o3 as a HealthBench grader (§1.2).
  Claude-Opus-4 trailed DeepSeek-V3 as a LiveMCPBench judge (§1.5).

Available settings:

| Judge | Effort settings | Default | Source |
|---|---|---|---|
| `claude-haiku-5-5` | low, medium, high, xhigh, max | medium | [effort docs](https://platform.claude.com/docs/en/build-with-claude/effort) |
| `gpt-6-luna` | supports `none` | not stated | [OpenAI](https://developers.openai.com/api/docs/guides/latest-model) |
| `gemini-3.8-flash` | low, medium, high | medium | [Google](https://ai.google.dev/gemini-api/docs/thinking) |

None of the three is deterministic (prior notes, §5.7).

**For this repo** **(inferred):** run the probe suite at each judge's lowest
effort and at the next level up. Freeze the lowest level that passes gate G1.
This replaces "lowest available effort" with "lowest effort shown to work".

---

## 3. Validating graders without humans

### 3.1 Known-answer probes and synthetic perturbations

The idea comes from CheckList
([Ribeiro et al., ACL 2020, §2](https://aclanthology.org/2020.acl-main.442/)),
which defines three test types:

- **Minimum functionality tests (MFT):** simple labelled cases.
- **Invariance tests (INV):** a label-preserving change must not change the
  output.
- **Directional expectation tests (DIR):** a change must move the label in a
  known direction.

The idea has been applied to LLM graders in four ways:

- **FBI** ([Doddapaneni et al., EMNLP 2024](https://arxiv.org/abs/2406.13439)):
  - 22 perturbation categories and 2,400 perturbed answers, tested on five
    evaluator LLMs.
  - Examples: wrong entity, wrong number, opposite fact, removed fact.
  - Evaluators missed the quality drop in more than half of cases on average.
  - A separate score-invariant set of meaning-preserving rewrites measures
    over-strictness.
- **Thakur et al.** ([§5.3–5.4](https://arxiv.org/abs/2406.12624)):
  - Tested the gold answer verbatim, the dummy answers "Yes" and "Sure", and
    a copy of the question.
  - Some judges failed even on the verbatim gold answer. "Yes" and "Sure"
    fooled several of them.
  - Most judges were lenient when unsure.
- **"Master keys"** ([Zhao et al., Table 1](https://arxiv.org/abs/2507.08794)):
  - In the reference-based setting, contentless answers earned false
    positives. Examples: a bare reasoning opener, a colon, a blank space.
  - Worst cases: GPT-4o 53.6%, GPT-o1 37.3%, Claude-4 57.7% (blank space).
- **Instability mining** ([SimpleQA Verified §3.1](https://arxiv.org/abs/2509.07968)):
  re-grading each answer many times surfaced the uncertain cases.

**What probes can establish:**

- Whether a grader detects each *specified* failure (dropped property, wrong
  version, contradiction, non-answer, injection).
- Whether it ignores irrelevant changes (paraphrase, padding, hedging).
- Its per-label accuracy on cases whose correct grade is known by
  construction.
- Probes can run before any benchmark answer exists.

**What probes cannot establish** **(inferred)**:

- Accuracy on the real distribution of errors. Real wrong answers are
  subtler and more varied than scripted edits, and probe authors cannot
  anticipate every failure.
- Probe labels are only as good as the probe construction.
- FBI's own result shows how far evaluators fall short on targeted
  perturbations, so passing a probe suite is necessary, not sufficient.

### 3.2 Agreement statistics and thresholds

**Statistics:**

- **Cohen's kappa:** chance-corrected agreement for two raters
  ([Cohen 1960](https://doi.org/10.1177/001316446002000104)). The weighted
  form gives partial credit for near misses
  ([Cohen 1968](https://doi.org/10.1037/h0026256)).
- **Fleiss' kappa:** the extension to many raters
  ([Fleiss 1971](https://doi.org/10.1037/h0031619)).
- **Krippendorff's alpha:** handles any number of coders, any metric
  (nominal, ordinal, interval) and missing data
  ([Krippendorff 2011](https://www.asc.upenn.edu/sites/default/files/2021-03/Computing%20Krippendorff%27s%20Alpha-Reliability.pdf)).

DOIs were resolved. I did not re-read the full texts of Cohen and Fleiss,
which are paywalled; they are cited for definitions only.

**Why alpha rather than kappa here:**

- Kappa estimates chance from each rater's own label distribution. It
  therefore credits systematic marginal differences, such as one lenient and
  one strict judge, as agreement. Krippendorff calls it nearly worthless as a
  reliability index for this reason
  ([Krippendorff 2004](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x)).
- Thakur et al. moved from kappa to Scott's π, which pools the label
  distributions, for the same reason. They recommend reporting percent
  agreement plus π together with qualitative analysis
  ([v6, footnote 2, Appendix B, Conclusion](https://arxiv.org/abs/2406.12624)).
- Alpha generalizes π.

**Thresholds:**

- **Krippendorff 2004:** set the minimum according to the cost of wrong
  conclusions. He suggests α ≥ .800, with .667 acceptable only for tentative
  conclusions. He adds that these are not magic numbers and that confidence
  intervals should be consulted
  ([open copy](https://repository.upenn.edu/server/api/core/bitstreams/b6ea3a5b-3dee-4f27-a159-e44393eda7d4/content)).
- **Per distinction:** test every distinction that matters, and take the
  smallest. This is not required when the downstream analysis works on
  averages, as it does here, so per-distinction alpha is diagnostic only.
- **McHugh 2012:** kappa 0.60–0.79 is only "moderate", and below 0.60 is
  inadequate ([Table 3](https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/)).
  The design's 0.6 is therefore a floor, not a standard.
- **Hayes & Krippendorff 2007:** bootstrap alpha over pairs of judgments
  (about 10,000 resamples). Report the 95% interval and the probability q
  that the true alpha is below the chosen minimum
  ([DOI](https://doi.org/10.1080/19312450709336664),
  [open PDF](https://www.asc.upenn.edu/sites/default/files/2021-03/Answering%20the%20Call%20for%20a%20Standard%20Reliability%20Measure%20for%20Coding%20Data.pdf)).
- **Prevalence:** with skewed category frequencies, kappa can be low despite
  high raw agreement
  ([Feinstein & Cicchetti 1990](https://doi.org/10.1016/0895-4356(90)90158-L);
  title and listing only, full text not read). See the simulation in §3.7.

**What agreement can and cannot establish:**

- It establishes that grades do not depend on which judge produced them, that
  is, reproducibility.
- It cannot establish correctness. Krippendorff's second condition puts
  truth outside the coefficient's scope
  ([2004](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x)). LLM errors
  are correlated ([Goel et al.](https://arxiv.org/abs/2502.04313)).
- High percent agreement can still produce system scores 10–20 points apart
  ([Thakur et al. §1, §4.1](https://arxiv.org/abs/2406.12624)). So report
  score differences, not only agreement.

### 3.3 Re-grading stability

- No judge here can be made deterministic. Claude 5.5 models reject sampling
  parameters, Gemini 3 should stay at temperature 1.0, and even temperature 0
  is not deterministic in practice (prior notes §5.7). The same judge may
  therefore grade the same answer differently.
- LLM evaluators show low agreement across samples and react to trivial
  prompt changes ([Stureborg et al.](https://arxiv.org/abs/2405.01724)).
- SimpleQA Verified turned instability into a diagnostic (§1.1).
- **For this repo** **(inferred):** grade every real answer and every probe
  twice per judge. Use pass 1 for scoring. Report each judge's test–retest
  alpha and percent identical. List the answers whose grade changed between
  passes.

### 3.4 Cross-family self-preference test

This test is my construction from the sources in §2.7 **(inferred)**. For
judge J and evaluated family F:

```
SPI(J, F) = mean over answers from F of (g_J − mean of the other two judges' grades)
          − mean over answers from other families of the same quantity
```

- The difference-in-differences cancels J's general leniency.
- Bootstrap over question IDs, keeping all repeats together, as §8 does for
  C2.
- Report SPI with a 95% interval for every (judge, family) pair, and the same
  quantity for the reference grader against the panel.
- **Limit:** SPI detects *relative* bias only. A bias that all judges share
  is invisible to it (Goel et al.).

### 3.5 Meta-evaluation benchmarks for judges

Examples: JudgeBench, the FBI suite, JUDGE-BENCH
([Bavaresco et al.](https://arxiv.org/abs/2406.18403)).

- **What they establish:** general judge ability on their own data.
  JUDGE-BENCH found large variance across models and datasets, and
  recommends validating LLM judges against human judgments before use.
- **Prompt sensitivity:** in PoLL, the ranking of judges changed with the
  prompt (Table 4).
- **For this repo** **(inferred):** public meta-eval scores can at most
  pre-screen a judge. They do not validate this rubric on this corpus.

### 3.6 What each check establishes, compared with human labels

| Check | Establishes | Cannot establish | Sources |
|---|---|---|---|
| Known-answer probes | That specified error types are caught and irrelevant edits ignored; per-label accuracy on constructed cases | Accuracy on real answers; error types nobody anticipated | [CheckList](https://aclanthology.org/2020.acl-main.442/), [FBI](https://arxiv.org/abs/2406.13439), [Thakur](https://arxiv.org/abs/2406.12624), [Zhao](https://arxiv.org/abs/2507.08794) |
| Agreement among judges | Reproducibility | Correctness; blind spots shared by all judges | [Krippendorff 2004](https://doi.org/10.1111/j.1468-2958.2004.tb00738.x), [Goel](https://arxiv.org/abs/2502.04313) |
| Agreement with a stronger LLM | Consistency with a better grader on the real distribution | Truth; biases of the reference grader | [Goel](https://arxiv.org/abs/2502.04313), [Panickssery](https://arxiv.org/abs/2404.13076) |
| Test–retest | Stability | Correctness | [Stureborg](https://arxiv.org/abs/2405.01724) |
| Self-preference index | Relative family bias | Bias shared by all judges | [Wataoka](https://arxiv.org/abs/2410.21819), [Goel](https://arxiv.org/abs/2502.04313) |
| Public judge benchmarks | General ability | Transfer to this rubric | [Bavaresco](https://arxiv.org/abs/2406.18403), [PoLL](https://arxiv.org/abs/2404.18796) |
| Human labels on a sample (not available) | Accuracy on the target construct. With prediction-powered inference, valid intervals on the mean ([Angelopoulos et al.](https://arxiv.org/abs/2301.09633)). | Even with labels, a judge no better than the evaluated model can cut the human labels needed by at most half ([Dorner et al.](https://arxiv.org/abs/2410.13341)). | — |

Without any human labels, no check above gives an unbiased estimate of how
often the panel is wrong on real answers **(inferred, from Dorner et al.
and Bavaresco et al.)**. The design partly offsets this, because judges
compare answers with a human-reviewed answer key rather than answering
themselves. The probes test exactly that comparison skill **(inferred)**.

### 3.7 How many items the reference comparison needs (simulation)

This is a pure-Python simulation **(inferred)**, run in a scratch directory
and not committed.

- **Setup:** three grades; the reference label is drawn from a fixed mix; the
  panel copies it with probability p, otherwise moves one step 80% of the
  time.
- **Sampling:** 40 replicates per cell, 600 bootstrap resamples for the
  intervals.

| Grade mix (0.0 / 0.5 / 1.0) | Raw agreement | Mean weighted κ | 95% CI half-width, n = 60 | n = 150 | n = 300 |
|---|---|---|---|---|---|
| 20 / 20 / 60 | 88% | 0.83 | ±0.11 | ±0.08 | ±0.055 |
| 10 / 10 / 80 | 92% | 0.83 | ±0.15 | ±0.10 | ±0.067 |
| 5 / 5 / 90 | 95% | 0.79–0.81 | ±0.32 | ±0.16 | ±0.096 |

Two more results:

- **False passes:** with a true weighted κ of 0.53, below the gate, a
  point-estimate gate at 0.6 still passed 17% of the time at n = 60, 10% at
  n = 150 and 3% at n = 300.
- **Prevalence:** 80% raw agreement with 80% of answers at 1.0 gave
  κ ≈ 0.62. 88% raw agreement with 90% at 1.0 gave κ ≈ 0.63.

---

## 4. Recommendation for this repo, mapped to design §9

Items are labelled as in the design doc. **Call** means proposed, for
maintainer review.

### 4.1 Changes to §9 at a glance

| §9 item | Today | Proposed (Call) |
|---|---|---|
| 9.1 inputs | Question, version, key, properties, citations, rubric, answer | Question, version, key, frozen per-property criteria, ambiguity note, answer with links masked. No citation list, no doc text (§2.4, §2.6). |
| 9.1 output | JSON grade plus one-line reason | Strict JSON: `reasoning`, `attempted`, `p1_met`, `p2_met`, `contradicts_key`, `wrong_version`. The grade is computed in code (§4.3). |
| 9.1 aggregation | Majority of grades; a three-way split goes to the calibration grader | Majority per boolean field (no ties possible). Withheld-vote rule (§2.8). |
| 9.1 panel | `claude-haiku-5-5`, `gpt-6-luna`, `gemini-3.8-flash`, lowest effort | Same families. The Gemini judge should be a Gemini model that is *not under test*, if one passes G1; otherwise keep `gemini-3.8-flash` and disclose that it grades its own answers. Effort = lowest level that passes G1 (§2.10). |
| 9.1 labels | 1.0 / 0.5 / 0.0 | Same scores, plus the label `CORRECT` / `PARTIAL` / `INCORRECT` / `NOT_ATTEMPTED` (§2.5). |
| 9.3 calibration grader | Opus 5.5 high, plus doc pages, 60 answers | Opus 5.5 high as *reference grader*: panel inputs for the grade, doc text only for a `key_issue` flag. Tested on 325 probes; grades 300 stratified answers (§4.6). |
| 9.3 gate | Linear-weighted κ ≥ 0.6; revise once on a 30-answer tuning set; re-check on 60 | Gates G1–G4 with three outcome tiers (§4.7). Revise once, using out-of-corpus tuning probes. |
| new 9.4 | — | Probe suite of 650 items, committed and published before the pilot (§4.5). |
| 11.3 report | "judge kappa and disputed counts" | Disclosure list in §4.9. |
| outside §9 | `scoring.py` tags verdicts "human" | New `scoring_method` values (§4.10). |

### 4.2 Grader prompt structure (Call)

**System prompt** (frozen, committed and hashed; cacheable):

1. **Role and task.** Grade one answer to a Python standard-library
   documentation question by comparing it with a reference answer key
   written from the official docs. Do not answer the question yourself. Do
   not use outside knowledge, except to notice that the answer contradicts
   the key. (The HLE pattern, §1.3.)
2. **Content-only rules:**
   - Ignore style, length, formatting, confidence wording and `[link]`
     markers.
   - Only the part that answers the question counts. Extra statements are
     ignored unless they contradict the key (SimpleQA Verified).
   - A hedged answer that commits to one answer is graded on that answer.
   - Offering alternatives without committing, refusing, or saying it cannot
     answer means not attempted (SimpleQA and SimpleQA Verified).
   - A version statement that conflicts with the key is a version error.
3. **Criterion rule.** A criterion is met when the answer states it in any
   wording. An "including" or "such as" list need not be complete
   (HealthBench).
4. **Worked examples.** Four examples on one stdlib question that is *not*
   in the corpus: correct, partial, contradiction, not attempted. SimpleQA
   uses few-shot examples per label, and an out-of-corpus question keeps the
   frozen corpus unexposed **(inferred)**.
5. **Output instruction.** Fill the schema, writing `reasoning` first, at
   most three sentences, about differences from the key only.

**User message** (per item): `<question version=…>`, `<answer_key>`,
`<criteria>` P1 and P2 from the frozen criteria file, `<ambiguity_note>` if
any, and `<answer>` with URLs and doc paths replaced by `[link]`.

**Schema:** `reasoning` (string), `attempted`, `p1_met`, `p2_met`,
`contradicts_key`, `wrong_version` (all booleans). All fields required,
`additionalProperties: false`. Use each provider's strict structured-output
mode, and check refusals before parsing (§2.3).

**Criteria file (Call):** `docs/benchmarks/grading-criteria.yml`, one
sentence per expected property.

- An agent drafts it from `answer_key`, `expected_properties` and
  `ambiguity_notes`.
- It is committed before any answer is graded.
- It is a derived file, so the frozen corpus does not change.
- Probe builders and judges use the same file. This removes a circularity:
  without it, the agent writing a "drop property 2" probe and the judge
  checking property 2 could read the terse label differently
  **(inferred)**.

### 4.3 Mapping and aggregation (Call)

```python
def grade(v):  # v = per-field majority of the three judges' booleans
    if not v.attempted:
        return 0.0, "NOT_ATTEMPTED"
    if v.contradicts_key or v.wrong_version:
        return 0.0, "INCORRECT"
    met = int(v.p1_met) + int(v.p2_met)
    return {2: (1.0, "CORRECT"), 1: (0.5, "PARTIAL"), 0: (0.0, "INCORRECT")}[met]
```

- Withheld votes follow §2.8.
- Keep every judge's raw output and its usage object.
- Failed trajectories stay 0.0 before grading, as today.

### 4.4 Panel make-up and settings (Call)

- **Anthropic and OpenAI judges:** keep `claude-haiku-5-5` ($0.10 / $0.50
  per MTok up to a 100K-token prompt) and `gpt-6-luna` ($0.10 / $0.50)
  ([Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing),
  [OpenAI pricing](https://developers.openai.com/api/docs/pricing)). Neither
  is an evaluated model.
- **Gemini judge:** `gemini-3.8-flash` costs $0.75 / $3.75, rising to $1.50 /
  $7.50 from 2027-01-01
  ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)). It is
  also an evaluated model.
  - Try a non-evaluated Gemini model from the same price list in the probe
    gate, for example `gemini-3.1-flash-lite` at $0.25 / $1.50. Keep it if it
    passes G1. Otherwise keep `gemini-3.8-flash` and disclose the
    self-grading.
  - I did not verify the Flash-Lite model's release status or quality.
- **Effort:** chosen per judge by G1 (§2.10), then frozen.
- **Batch APIs:** use them. Anthropic, OpenAI and Gemini all list batch
  prices at half the standard rate (same pricing pages). Grading is not
  latency-sensitive.

### 4.5 Probe suite (Call)

**Size:** 13 probe types × 50 questions = **650 probes**. Every probe is
built from corpus fields and pinned doc text, with its expected grade fixed
by construction.

| ID | Probe | Expected | Tests | Basis |
|---|---|---|---|---|
| `KEY` | Answer key verbatim | 1.0 | Sanity: the gold answer is accepted | Thakur §5.3 |
| `PARA` | Paraphrase keeping both properties | 1.0 | Over-strict reference matching | FBI score-invariant set; CheckList INV |
| `PAD` | Key plus 2–4 true sentences from the cited section of the pinned docs | 1.0 | Length invariance | Zheng verbosity; CheckList INV |
| `HEDGE` | "I'm not fully sure, but …" plus key | 1.0 | Hedging rule | SimpleQA prompt |
| `DROP1`, `DROP2` | Key minus one property (two probes) | 0.5 | Partial credit | FBI "remove fact"; CheckList DIR |
| `VER` | Version attribution changed (swapped for cross-version questions) | 0.0 | Version awareness, the benchmark's core claim | FBI number and entity errors |
| `FACT` | Core fact negated or altered, stated confidently | 0.0 | Confident-but-wrong | FBI "opposite fact" |
| `KEYX` | Key plus one sentence that contradicts it | 0.0 | Contradiction rule | SimpleQA |
| `PADX` | `FACT` plus `PAD` padding | 0.0 | Padding must not rescue an error | Zheng verbosity |
| `SPLIT` | One right and one wrong alternative, uncommitted | 0.0 (`NOT_ATTEMPTED`) | Uncommitted hedge | SimpleQA, SimpleQA Verified |
| `IDK` | Abstention | 0.0 (`NOT_ATTEMPTED`) | Abstention label | SimpleQA; Kalai |
| `JUNK` | Rotating: question restated / bare reasoning opener / text telling the grader the answer is correct | 0.0 | Dummy answers, master keys, injection | Thakur; Zhao Table 1; Inspect parser comments |

**Construction (Call, inferred design):**

1. **Scripted probes.** `KEY`, `PAD`, `HEDGE`, `PADX`, `IDK` and `JUNK` are
   built by code from the corpus and the pinned index.
2. **Agent-written probes.** `PARA`, `DROP*`, `VER`, `FACT`, `KEYX` and
   `SPLIT` are written by an agent against a written spec. Rotate the
   generator across the three provider families, a third each, and report
   probe accuracy by generator family. This doubles as a familiarity check
   (Wataoka).
3. **Spec check.** A model from a different family checks every agent-written
   probe against its spec. A failing probe is regenerated up to two times,
   then dropped. Report how many were dropped: filtering can make the suite
   easier.
4. **Pre-registration.** Commit all probes, labels, criteria, the prompt and
   the mapping before the pilot is graded, and publish them with the report.
   Public probes let anyone audit the labels, which is the closest available
   substitute for human calibration **(inferred)**.
5. **Tuning set.** 10 out-of-corpus stdlib questions × 13 probes = 130
   tuning probes. These are the only material used to revise the prompt or
   effort (the design's "revise once"). The 650 corpus probes stay
   held-out.

**Why 650:** each probe type has n = 50. At 47/50 the Wilson 95% lower bound
is 0.84, and at 49/50 it is 0.895. Overall, 631/650 (0.97) gives a lower bound
of 0.955 **(inferred arithmetic)**. Fewer probes per type would make the
per-type check meaningless. More probes add cost without changing the
decision **(inferred)**.

### 4.6 The Opus step: keep it, but as a validated reference grader (Call)

**Answer to the maintainer's question.** An agent-only "calibration" against
`claude-opus-5-5` alone cannot validate the panel:

- Agreement is not truth (§3.2).
- Opus shares a family with the Haiku judge and with `claude-sonnet-5-5`'s
  answers (E5, §2.7).
- At n = 60 the statistic is too noisy (§3.7).

It is still the only check of the cheap panel on the *real* answer
distribution, so keep it with these changes:

1. **Same inputs, rubric and schema as the panel**, so that agreement means
   something (E1). Doc text goes only into a separate `key_issue` flag, plus
   a one-time audit of all 50 answer keys. A flagged key is disclosed. The
   corpus stays frozen.
2. **Prove it on the probes first.** Run 325 probes, 25 per type, at ≥ 0.95
   accuracy. That is a Wilson lower bound of about 0.94 at 315/325
   **(inferred)**. A stronger model is not automatically a better judge:
   LiveMCPBench's Claude-Opus-4 and HealthBench's o3 both did worse (§1.2,
   §1.5).
3. **300 real answers, not 60.** Stratify by (model, system) × category with
   a seed fixed in the pre-registration, about 27 per cell across the 11
   cells. Draw the sample after the run, without looking at panel grades.
4. **Report Krippendorff's alpha (interval metric) with a bootstrap
   interval**, plus percent agreement, the confusion matrix, and the mean
   score difference per (model, system) cell. Report the self-preference
   index for Claude-family answers.
5. **It no longer breaks ties.** None occur under per-field voting (§2.8).
   It only adjudicates withheld votes.
6. **Optional second reference (Call).** Add the strongest non-Anthropic
   model that is not under test and passes G1 (candidates: `gpt-6-astra`,
   `gemini-3.1-pro-preview`), on the same 300 answers. This separates
   "Opus agrees with the panel" from Claude-family agreement.

### 4.7 Pass/fail gate (Call)

| Gate | Statistic | Pass when | Basis |
|---|---|---|---|
| G1 probes | Exact grade accuracy against construction labels, held-out 650 | Panel ≥ 0.95 overall and ≥ 0.90 on every probe type. Each judge alone ≥ 0.90 overall. Reference grader ≥ 0.95 on its 325. | Per-type checks follow Krippendorff's "test each distinction". The levels are a Call **(inferred)**. |
| G2 panel reliability | Krippendorff's alpha (interval) across the three judges' individual grades, all full-run answers; bootstrap 95% CI and q | α ≥ 0.80 and q(α < 0.667) ≤ 0.05. Also reported per evaluated family and per binary distinction (diagnostic). | Krippendorff 2004; Hayes & Krippendorff 2007 |
| G3 reference agreement | Alpha (interval) between panel grades and reference grades on the 300 sample | α ≥ 0.80 and the lower 95% bound ≥ 0.667 | Same, plus §3.7 |
| G4 claim robustness | C2 and C3 pass/fail recomputed from each single judge's grades | No verdict flips. Otherwise label that claim "judge-sensitive" and keep it out of README/PyPI. | Thakur: high agreement can still mean different scores |

**Timing:** G1 runs *before* the pilot, because probes need no benchmark
answers. A broken judge or effort level is caught for a few dollars. G2–G4
run after the full run **(inferred)**.

**Outcome tiers** (they extend the design's existing "judge not validated"
rule):

1. **Validated:** G1–G4 all pass. Claims may be published.
2. **Tentative:** G1 and G4 pass, and G2 or G3 lands between 0.667 and 0.80.
   Publish the data and report, but make no README or PyPI claim.
3. **Judge not validated:** anything else. Publish the data only, with no
   public claim.

**Reported, not gated:**

- Test–retest alpha per judge.
- The self-preference index per (judge, family).
- Length–grade correlations.
- `NOT_ATTEMPTED` and `INCORRECT` rates per (model, system).
- Parse failures, retries, refusals and withheld votes.

### 4.8 Cost estimate (inferred)

**Assumptions** (replace with pilot measurements):

- A panel call uses about 1,600 input tokens (prompt with examples ~1,100,
  item and criteria ~150, answer ~350) and about 350 output tokens,
  including low-effort thinking.
- An Opus high-effort call uses about 2,500 input and 2,500 output tokens.
- Per answer-pass, the three-judge panel costs about $0.0032 at list prices:
  Haiku $0.000335, Luna $0.000335, `gemini-3.8-flash` $0.0025.
- Prices are from the three pricing pages cited in §4.4.

| Item | Calls | Tokens | List $ | Batch $ |
|---|---|---|---|---|
| Tuning probes: 130 × 3 judges × 4 configs (2 efforts × 2 prompt versions) | 1,560 | 3.0 M | 1.7 | 0.8 |
| Corpus probes: 650 × 3 judges × 2 passes | 3,900 | 7.6 M | 4.2 | 2.1 |
| Pilot answers: ~300 × 3 judges | 900 | 1.8 M | 1.0 | 0.5 |
| Full-run answers: 1,650 (1,350 hosted + 300 air-gapped) × 3 × 2 passes | 9,900 | 19.3 M | 10.6 | 5.3 |
| Opus reference: 325 probes + 300 answers + 50 key audits | 675 | 3.4 M | 40.5 | 20.3 |
| **Total** | **16,935** | **≈ 35 M** | **≈ 58** | **≈ 29** |

Notes:

- A cheaper non-evaluated Gemini judge (§4.4) roughly halves the panel rows.
- The optional second reference grader adds about $22–94 at list prices,
  depending on the model.
- The design's budget ([§12](../benchmarks/AGENTIC-BENCHMARK-DESIGN.md#12-budget-and-run-sequence-call))
  has a $175 total hard cap including ~$75–95 for the hosted run. These
  grading costs fit under it.
- Grade before 2027-01-01, when Gemini prices double.

### 4.9 What the public report must disclose (Call)

1. **A plain statement**, near the correctness numbers. Suggested wording:
   Correctness was graded by three LLMs (IDs, provider-reported versions,
   effort settings) against the corpus answer keys. No human graded or
   calibrated these grades. Validity rests on 650 published synthetic
   answers with known grades, agreement among the three graders, and
   agreement with a stronger LLM reference grader on 300 answers. These
   checks cannot show how often the graders are wrong on real answers.
2. **The panel and its overlaps:** which judge shares a family, or is the
   identical model, with which evaluated model. Also note that grading used
   hosted APIs, so only answering, not grading, was air-gapped in the C3 arm.
3. **Hashes and full texts** of the judge prompt, the few-shot examples, the
   output schema, the mapping function, the URL-masking rule and the criteria
   file.
4. **The probe suite:** all probes and labels, how each was built, generator
   and checker models, dropped-probe counts, and per-judge and per-type
   accuracy with Wilson intervals. Include the effort levels tried and the
   ones chosen.
5. **G2:** alpha with interval and q, per evaluated family, per system and
   per distinction.
6. **G3:** reference grader model and settings, sample design and seed,
   alpha with interval, confusion matrix, and mean score differences per
   (model, system). The optional second reference, if used.
7. **G4:** C2 and C3 verdicts under the panel and under each single judge,
   with any judge-sensitive claim flagged.
8. **Test–retest and self-preference:** per-judge test–retest alpha and the
   self-preference indices with intervals.
9. **Label shares:** `CORRECT` / `PARTIAL` / `INCORRECT` / `NOT_ATTEMPTED`
   per (model, system).
10. **Grader failures:** parse failures, refusals, retries, withheld votes
    and how each was resolved.
11. **Answer-key audit:** flags, and results with and without the flagged
    questions.
12. **Grading cost and tokens,** kept separate from the benchmark's own
    token metrics.
13. **Anti-claims to add to design §2:** "graded against ground truth",
    "human-validated" and an unqualified "validated". Use "LLM-graded,
    validated against synthetic probes and inter-model agreement" instead.

### 4.10 Repo changes outside §9 (for the builder)

- **`benchmarks/scoring.py`:**
  - Ingesting LLM verdicts through `ingest_adjudication_verdicts` would
    record `scoring_method: "human"`
    ([L298–310](../../benchmarks/scoring.py#L298-L310)) and count them as
    `human_scored_cells` in rollups
    ([L490–492](../../benchmarks/scoring.py#L490-L492)).
  - Add LLM provenance values, for example `llm_panel` and `llm_reference`.
    Record the grader model IDs in `adjudicator`. Update the queue reason
    text ([L453](../../benchmarks/scoring.py#L453)) and the module docstring
    ([L11–30](../../benchmarks/scoring.py#L11-L30)), which still say a human
    decides.
- **New committed inputs:** the criteria file, the probe file and the judge
  prompt. All must exist before the pilot is graded.
- **Report generator:** the §4.9 sections.

---

## 5. Open questions and things I could not verify

1. **Can three cheap judges reach α ≥ 0.80** on this rubric? No source
   measures it for this corpus. The pilot decides. If they cannot, the
   honest outcome is the "tentative" tier, not a lower threshold.
2. **Prevalence.** If most answers score 1.0, alpha can fall below 0.80
   despite high raw agreement (§3.7). The no-docs and Gemma arms should add
   enough 0.0 and 0.5 grades to prevent this **(inferred)**, but the pilot
   should check the grade mix.
3. **Do probes transfer to real answers?** This cannot be checked without
   human labels (§3.6). Even a small human-labelled sample would allow
   prediction-powered intervals
   ([Angelopoulos et al.](https://arxiv.org/abs/2301.09633)). That is the
   cheapest upgrade path if the "no human" decision is ever relaxed.
4. **Nuance-only answers.** An answer that states only the nuance property
   gets 0.5 under the mapping, where the methodology might call it
   "materially incomplete". A `core` marker in the criteria file would fix
   this, but some questions have two core properties (for example XV-008).
   This needs a maintainer decision before pre-registration.
5. **Gemini key order.** The Gemini structured-output page I read gives no
   key-order guarantee. Verify that `reasoning` comes first in the pilot
   ([docs](https://ai.google.dev/gemini-api/docs/structured-output)).
6. **Non-evaluated Gemini judges.** I did not verify the release status or
   judging quality of the candidate models (`gemini-3.1-flash-lite`,
   `gemini-3.5-flash-lite`, `gemini-3.1-pro-preview`). G1 must decide.
7. **Identical-model self-grading.** Its size for `gemini-3.8-flash` is
   unknown until the self-preference index is measured.
8. **Source gaps:**
   - LiveMCPBench gives two different human-agreement figures (78.95% and
     81%) and no sample size.
   - MCP-Bench does not state its human-study size.
   - SimpleQA Verified reports no autorater agreement figure.
   - HealthBench Professional's length-penalty parameters are flags with no
     published defaults.
9. **Not read in full:** Cohen 1960, Cohen 1968, Fleiss 1971 and Feinstein &
   Cicchetti 1990 are paywalled. They are cited for definitions only, with
   DOIs resolved.
10. **Token and cost figures** in §4.8 are estimates from assumed prompt
    sizes. The simulation in §3.7 uses assumed error patterns, not data.
11. **Explicit confidence targets.** Adding one to the answering prompt, as
    Kalai et al. propose, would change the frozen prompt. It is out of scope,
    but worth noting for round 2.

---

## Sources

Primary sources used (read 2026-10-29). Repo files are linked inline.

**Reference graders and evals**
- SimpleQA paper: https://arxiv.org/abs/2411.04368
- simple-evals at `652c89d`: https://github.com/openai/simple-evals/tree/652c89d0ca9df547706735883097e9537d40dc47 (files `simpleqa_eval.py`, `healthbench_eval.py`, `simple_evals.py`, `README.md`); HealthBench Professional commit: https://github.com/openai/simple-evals/commit/496e4570446e9f0499a00557a231a440c446df76
- SimpleQA Verified: https://arxiv.org/abs/2509.07968
- HealthBench paper: https://arxiv.org/abs/2505.08775
- HLE judge script: https://github.com/centerforaisafety/hle/blob/a59048963171c5b9cfe89fbcb97ddde39b540897/hle_eval/run_judge_results.py
- Inspect AI model-graded scorer: https://github.com/UKGovernmentBEIS/inspect_ai/blob/8fdc4f6be524ddb02aaec85e723a434884f5c837/src/inspect_ai/scorer/_model.py
- MCP-Bench: https://arxiv.org/abs/2508.20453
- LiveMCPBench: https://arxiv.org/abs/2508.01780
- MCP-Universe: https://arxiv.org/abs/2508.14704
- MCPMark: https://arxiv.org/abs/2509.24002
- MCP-Atlas: https://arxiv.org/abs/2602.00933
- PoLL: https://arxiv.org/abs/2404.18796
- JudgeBench: https://arxiv.org/abs/2410.12784
- Prometheus: https://arxiv.org/abs/2310.08491
- G-Eval: https://arxiv.org/abs/2303.16634

**Grader design and judge failure modes**
- Zheng et al. (MT-Bench): https://arxiv.org/abs/2306.05685
- FBI (Doddapaneni et al.): https://arxiv.org/abs/2406.13439
- Thakur et al., Judging the Judges (v6): https://arxiv.org/abs/2406.12624
- Zhao et al., One Token to Fool LLM-as-a-Judge: https://arxiv.org/abs/2507.08794
- Chiang & Lee: https://arxiv.org/abs/2310.05657
- Tam et al., Let Me Speak Freely?: https://arxiv.org/abs/2408.02442
- CheckEval: https://arxiv.org/abs/2403.18771
- TICK: https://arxiv.org/abs/2410.03608
- Stureborg et al.: https://arxiv.org/abs/2405.01724
- Wataoka et al.: https://arxiv.org/abs/2410.21819
- Panickssery et al.: https://arxiv.org/abs/2404.13076
- Goel et al.: https://arxiv.org/abs/2502.04313
- Kalai et al., Why Language Models Hallucinate: https://arxiv.org/abs/2509.04664

**Validation without humans and its limits**
- CheckList (Ribeiro et al., ACL 2020): https://aclanthology.org/2020.acl-main.442/
- Bavaresco et al., JUDGE-BENCH: https://arxiv.org/abs/2406.18403
- Dorner et al., Limits to scalable evaluation: https://arxiv.org/abs/2410.13341
- Angelopoulos et al., Prediction-powered inference: https://arxiv.org/abs/2301.09633

**Agreement statistics**
- Krippendorff 2004, Reliability in Content Analysis: https://doi.org/10.1111/j.1468-2958.2004.tb00738.x (open copy: https://repository.upenn.edu/server/api/core/bitstreams/b6ea3a5b-3dee-4f27-a159-e44393eda7d4/content)
- Krippendorff 2011, Computing Krippendorff's Alpha-Reliability: https://www.asc.upenn.edu/sites/default/files/2021-03/Computing%20Krippendorff%27s%20Alpha-Reliability.pdf
- Hayes & Krippendorff 2007: https://doi.org/10.1080/19312450709336664
- McHugh 2012: https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/
- Cohen 1960: https://doi.org/10.1177/001316446002000104 ; Cohen 1968: https://doi.org/10.1037/h0026256 ; Fleiss 1971: https://doi.org/10.1037/h0031619 ; Feinstein & Cicchetti 1990: https://doi.org/10.1016/0895-4356(90)90158-L (definitions only, not read in full)

**Provider documentation**
- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Anthropic effort: https://platform.claude.com/docs/en/build-with-claude/effort
- Anthropic structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- OpenAI latest model guide: https://developers.openai.com/api/docs/guides/latest-model
- OpenAI structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing
- Gemini thinking: https://ai.google.dev/gemini-api/docs/thinking
- Gemini structured output: https://ai.google.dev/gemini-api/docs/structured-output
