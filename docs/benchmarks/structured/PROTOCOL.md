# Structured Python documentation comparison (#63)

This amendment measures whether a model picks the documented factual answer when offered four alternatives. It compares access to Python Docs MCP, an MCP fetch tool restricted to official Python docs, and no documentation tools. The same model receives the same prompts and options in all three conditions. Free-form explanations, citation quality and general assistant quality are outside this score. The earlier open-ended calibration and its failed grading checks remain a separate study record.

## Questions and answer collection

Each of the 50 questions retains its original ID, category, version scope and official Python documentation references. Each has one correct alternative and three distractors. Review the items and their keys against the retained official docs before the first paid answer. Freeze the questions, options, keys, option order, response contract, model matrix, evaluator and pilot gates before collection. The answer worker receives the formatted question and four choices. It does not receive the key, rubric, other arms' replies or scorer output. Tool-enabled models may look up the documentation. No answer or tool call is eligible for executing model-generated code.

Use the existing container launcher, model settings, per-call limits, provider ledger and tool restrictions. The models are Claude Sonnet 5.5, GPT‑6.1 Sol and Gemini 3.8 Flash. Run three repeats per model, system and question. Use the previously registered seed 411122497 and ten pilot question IDs: AP-001, AP-003, CO-009, CO-005, XV-012, XV-010, EX-014, EX-008, PE-005 and PE-003. The pilot has 270 planned answers. A passing pilot permits a fresh 1,350-answer run across all 50 questions. Do not pool the pilot's observations with that run.

## Local score and advancement

The required response is a JSON object with one answer letter A–D. An optional citations array accepts HTTP or HTTPS URL strings. The parser checks their format. It does not grade which source was cited or whether the source supports the choice. Additional fields, duplicate fields, invalid letters, malformed JSON and unsuccessful trajectories receive zero. One exact keyed choice earns one point. Include every planned answer in the denominator. Report unsuccessful trajectories, malformed responses and valid wrong choices separately. Score the final answer locally without a model judge.

Advance only if the pilot has reconciled evidence for all 270 planned answers, every model–system pair has at least 27 successful, correctly formatted responses out of 30, and the budget, timing and ledger checks pass. Correctness never determines advancement. A model may score poorly on the questions and still complete a valid pilot. The full run uses measured answering costs and timings from this pilot, a 1.25 projection factor and zero model-grading fees. Retain the existing per-family 24-hour limit and cumulative caps of USD 180 Anthropic, 65 OpenAI and 180 Google. Stop if an overrun or an unresolved reservation prevents safe continuation. Report an incomplete run or failed pilot explicitly and do not infer full-study accuracy from it.

## Reporting

For each model–system pair report counts and factual accuracy, per-category results and data completeness. Compare systems within a model on matched questions. For a completed full study, quantify uncertainty in paired accuracy differences by resampling questions within their original categories, retaining all repeats for each sampled question (10,000 draws, seed 411122497, percentile 95% intervals). These intervals describe sampling variability across this corpus. Report provider-native tokens, observed latency and ledger-booked dollars with their denominators when available. The three repeats of a question are not independent questions.

Publish the protocol, frozen items, scorer, clean per-answer results and run identity. Keep credentials, private transcripts, execution logs and any unreviewed raw outputs out of the public report.
