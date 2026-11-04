# Grading: batch APIs, structured outputs, judge models, token counting and alpha

- **Date:** 2026-11-04
- **Scope:** Facts the PR 5 plan (`docs/plans/2026-11-04-agentic-grading-report.md`) needs for the grading half of the agentic benchmark: the three providers' batch APIs, their structured-output contracts, the judge and reference models (limits, effort levels, prices), the Anthropic count-tokens endpoint, and Krippendorff's alpha.
- **Status:** Research only. No product code changed. No API key was read or used and no paid API call was made.
- **Method:** Primary sources only: vendor documentation and pricing pages, and Krippendorff's own paper. Pages were read with `firecrawl_scrape` (query mode, verbatim quotes where the page allowed) and one `firecrawl_search` restricted to `platform.claude.com`. Anything marked **(inferred)** is my reading, not a published statement.
- **Retrieved:** every page on 2026-11-04 between 03:58:44Z and 04:02:56Z (UTC, bracketed by `date -u`). The scraper returns no per-page timestamp.

## Answers at a glance

| Q | Answer | Main source |
|---|---|---|
| A. Anthropic Message Batches | 50% of standard prices. At most 100,000 requests or 256 MB. Expires if not done in 24 h. Results kept 29 days. `errored`, `canceled` and `expired` requests are not billed. Results matched by `custom_id`; order not guaranteed. Structured outputs work in batches. | https://platform.claude.com/docs/en/build-with-claude/batch-processing |
| B. OpenAI Batch API | 50% discount. `/v1/responses` is a supported endpoint. Completion window `24h` only. At most 50,000 requests, 200 MB file. On expiry, unfinished requests are cancelled and "You will be charged for tokens consumed from any completed requests." Output order may differ; match by `custom_id`. | https://developers.openai.com/api/docs/guides/batch |
| C. Gemini batch mode | 50% of standard. 24 h target. Inline requests under 20 MB, or a JSONL file up to 2 GB keyed per line. A job expires after 48 h with no results. Structured output works in batch requests. No statement on billing failed or expired requests. | https://ai.google.dev/gemini-api/docs/batch-mode |
| D. Structured outputs | Anthropic `output_config.format` (`json_schema`); OpenAI `text.format` (`json_schema`, `strict`); Gemini `response_mime_type` plus a response schema. Anthropic warns that a property asking for reasoning can trigger a `reasoning_extraction` refusal. | Section D |
| E. Judge and reference models | Haiku 5.5 $0.10 / $0.50 (≤100K prompt); Opus 5.5 $4 / $20; GPT-6 Luna $0.10 / $0.50 (≤272K); Gemini 3.1 Flash-Lite $0.25 / $1.50, stable. Batch is half on all four. Lowest effort: Haiku `low`, Luna `none`, Flash-Lite `minimal` (its default). | Section E |
| F. Count tokens | Free, rate-limited (5,000 / 10,000 / 20,000 RPM by tier). Takes `system`, `tools`, `messages`. The docs call the result an estimate. Claude 4.6 and later reject a conversation that ends in an assistant turn (no prefill). | https://platform.claude.com/docs/en/build-with-claude/token-counting |
| G. Krippendorff's alpha | α = 1 − D_o/D_e from the coincidence matrix; interval δ² = (c − k)²; units with fewer than two values are omitted. The paper's worked example gives nominal α = 0.743 and interval α = 0.849; a local recomputation gives the same. | Krippendorff 2011 (Section G) |

## A. Anthropic Message Batches

Source: https://platform.claude.com/docs/en/build-with-claude/batch-processing

- Limits: "A Message Batch is limited to either 100,000 Message requests or 256 MB in size, whichever is reached first."
- Timing: "most batches completing within 1 hour. You can access batch results when all messages have completed or after 24 hours, whichever comes first. Batches expire if processing does not complete within 24 hours."
- Retention: "Batch results are available for 29 days after creation."
- Price: "All usage is charged at 50 percent of the standard API prices." The FAQ adds that the discount "applies to input tokens, output tokens, and any special tokens".
- Result types, quoted from the table:
  - `succeeded`: "Request was successful. Includes the message result."
  - `errored`: "... a message was not created. ... You will not be billed for these requests."
  - `canceled`: "... before this request could be sent to the model. You will not be billed for these requests."
  - `expired`: "Batch reached its 24-hour expiration before this request could be sent to the model. You will not be billed for these requests."
- Each `succeeded` line carries the full message, with `model`, `stop_reason` and `usage` (example `.jsonl` on the page).
- Matching: "Use meaningful `custom_id` values to easily match results with requests, because order is not guaranteed." Each `custom_id` must be unique.
- Features: "the Message Batches API supports nearly all features available in the Messages API ... A small number of parameters (stream, speed, fallbacks, and max_tokens: 0) are not supported."
- The structured-outputs page lists "Batch processing: Process structured outputs at scale with a 50 percent discount".

## B. OpenAI Batch API

Sources: https://developers.openai.com/api/docs/guides/batch (read as `batch.md`), and the GPT-6 Luna model page in Section E.

- Endpoints: "For now, the available endpoints are: /v1/responses (Responses API), /v1/chat/completions ..."
- "Each request must include a unique custom_id value". "For now, the completion window can only be set to 24h."
- Limits: "A single batch may include up to 50,000 requests, and a batch input file can be up to 200 MB in size." "You can create up to 2,000 batches per hour."
- Statuses: `validating`, `failed` ("the input file has failed the validation process"), `in_progress`, `finalizing`, `completed`, `expired`, `cancelling`, `cancelled`.
- Output: "The output .jsonl file will have one response line for every successful request line in the input file. Any failed requests in the batch will have their error information written to an error file". A line is `{"id", "custom_id", "response": {"status_code", "request_id", "body"}, "error"}`, and `body` carries `usage`.
- Order: "the output line order may not match the input line order. ... use the custom_id field".
- Expiry: "unfinished requests within that batch are cancelled, and any responses to completed requests are made available via the batch's output file. You will be charged for tokens consumed from any completed requests." An expired request's error line has `"code": "batch_expired"`.
- "The output file will automatically be deleted 30 days after the batch is complete."
- Price: "50% cost discount compared to synchronous APIs". The GPT-6 Luna page: "Batch and Flex are priced at 50% of Standard rates."

## C. Gemini batch mode

Source: https://ai.google.dev/gemini-api/docs/batch-mode (served from `aistudio.google.com/docs/batch-api`)

- Price and time: "process large volumes of requests asynchronously at 50% of the standard cost. The target turnaround time is 24 hours". "Batch API usage is priced at 50% of the standard interactive API cost for the equivalent model."
- Inline requests suit "smaller batches that keep the total request size under 20MB".
- File input: "Each line in this file must be a JSON object containing a user-defined key and a request object ... The user-defined key is used in the response to indicate which output is the result of which request." "The maximum allowed file size for an input file is 2GB."
- Structured output is shown in a batch request with `'response_mime_type': 'application/json'` and `'response_schema'`.
- States: `JOB_STATE_PENDING`, `JOB_STATE_RUNNING`, `JOB_STATE_SUCCEEDED`, `JOB_STATE_FAILED`, `JOB_STATE_CANCELLED`, and `JOB_STATE_EXPIRED`: "The job has expired because it was running or pending for more than 48 hours. The job will not have any results to retrieve."
- Errors: "Check the batchStats for failedRequestCount ... parse each line to check if it's a GenerateContentResponse or a status object".
- "The creation of a batch job is not idempotent. If you send the same creation request twice, two separate batch jobs will be created."

## D. Structured outputs

**Anthropic** (https://platform.claude.com/docs/en/build-with-claude/structured-outputs):

- "The request carries the schema in output_config.format with type: \"json_schema\"."
- Supported by "All models". Supported: "`required` and `additionalProperties` (must be set to `false` for objects)". Not supported: "String constraints (`minLength`, `maxLength`)".
- "If property order in the output is important to your application, mark all properties as required".
- Refusals: "The response has stop_reason: \"refusal\" ... You'll be billed for the tokens generated. The output might not match your schema".
- "A property that asks for the model's thinking or step-by-step reasoning might lead to a reasoning_extraction refusal. Ask for a short explanation instead."
- `max_tokens` stop: "The output might be incomplete and not match your schema."

**OpenAI** (https://developers.openai.com/api/docs/guides/structured-outputs, read as `.md`):

- Responses API: `text: { format: { type: "json_schema", "strict": true, "schema": … } }`, with `additionalProperties: false`.
- "When using Structured Outputs, outputs will be produced in the same order as the ordering of keys in the schema."
- Refusal content item: `{"type": "refusal", "refusal": "..."}`. An output may not match "in the case of a refusal ... or if for example you reach a max tokens limit and the response is incomplete."

**Gemini** (https://ai.google.dev/gemini-api/docs/structured-output, and the batch page above):

- The structured-output page lists unsupported keywords: `minLength`, `maxLength`, `minProperties`, `maxProperties`, `uniqueItems`, `contains`, `minContains`, `maxContains`.
- The batch page shows `response_mime_type` plus `response_schema` in a `generateContent` request.
- No key-order guarantee was found **(not found)**, so a parser must read keys by name.

## E. Judge and reference models

**Claude** (https://platform.claude.com/docs/en/about-claude/models/overview, https://platform.claude.com/docs/en/about-claude/pricing, https://platform.claude.com/docs/en/build-with-claude/effort.md):

| Model | API ID | Context / max output | Thinking | Default effort |
|---|---|---|---|---|
| Opus 5.5 | `claude-opus-5-5` | 1M / 128K | Adaptive (always on) | `medium` |
| Haiku 5.5 | `claude-haiku-5-5` | 1M / 128K | Adaptive | `medium` |

- Effort is `output_config.effort`; levels `low`, `medium`, `high`, `xhigh`, `max`, and the effort page lists Haiku 5.5 and Opus 5.5 for `max` and `xhigh`. `low` is "Most efficient".
- Prices per MTok, standard: Opus 5.5 "$4" input, "$20" output; Haiku 5.5 "$0.10/ MTok for prompts up to 100,000 tokens", "$0.50/ MTok" output, and "$0.50/ MTok for prompts over 100,000 tokens", "$2.50/ MTok" output.
- Batch: Opus 5.5 "$2/ MTok" input; Haiku 5.5 "$0.05/ MTok for prompts up to 100,000 tokens", "$0.25/ MTok for prompts over 100,000 tokens", with "$1.25/ MTok" output above the threshold.
- Caching: "A cache hit costs 10% of the standard input price ... (1.25x write) ... (2x write)". "On Claude Opus 5.5 and Claude Sonnet 5.5, a cache hit costs 5% ... ($0.20 USD per million tokens on Claude Opus 5.5". Haiku's write and hit rates in the plan's snapshot are derived from these multipliers **(inferred)**.
- "Claude Haiku 5.5 is priced by prompt length: a request whose prompt is over 100,000 tokens pays higher prices." Every other 4.6-and-later model has no long-context tier.

**GPT-6 Luna** (https://developers.openai.com/api/docs/models/gpt-6-luna, https://developers.openai.com/api/docs/pricing as `pricing.md`):

- "1,050,000 context window", "128,000 max output tokens". "reasoning.effort supports none, low, medium (default), high, xhigh, and max."
- Standard row: short context (≤272K) input $0.10, output $0.50, cached $0.01, 30m write $0.125, 12h write $0.25; long context (>272K) $0.20, $0.75, $0.02, $0.25, $0.50.
- Batch row: every figure half the standard one ($0.05, $0.25, $0.005, $0.0625, $0.125; long context $0.10, $0.375, $0.01, $0.125, $0.25). The table labels came back without tab names; the batch row is the one equal to half of standard **(inferred)**, which matches "Batch and Flex are priced at 50% of Standard rates."

**Gemini** (https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite, https://ai.google.dev/gemini-api/docs/pricing, https://ai.google.dev/gemini-api/docs/generate-content/thinking):

- `gemini-3.1-flash-lite`: "Stable", input limit 1,048,576, output limit 65,536, Batch API supported, structured outputs supported. Latest update May 2026.
- Thinking levels for "Gemini 3.5 & 3.1 Flash-Lite": `minimal` "Supported (Default)", `low`, `medium`, `high` (Dynamic). Gemini 3.8 Flash: `minimal` "Not supported (error)", default `medium`.
- Paid-tier standard prices: Gemini 3.1 Flash-Lite input "$0.25 (text / image / video)", output "$1.50", context caching "$0.025". Gemini 3.5 Flash-Lite (`gemini-3.5-flash-lite`, not marked preview): $0.30 input, $2.50 output.
- The scraper could not separate the Standard and Batch tabs for the Flash-Lite models; the batch price comes from the batch page's 50% statement (Section C).

## F. Anthropic count tokens

Sources: https://platform.claude.com/docs/en/build-with-claude/token-counting and https://platform.claude.com/docs/en/api/errors (via `firecrawl_search`).

- "Token counting is free to use but subject to requests per minute rate limits based on your usage tier": Start 5,000, Build 10,000, Scale 20,000 RPM.
- The endpoint "accepts the same structured list of inputs for creating a message, including support for system prompts, tools". It returns "The total number of tokens across the provided list of messages, system prompt, and tools."
- FAQ: "token counting provides an estimate without using caching logic."
- "Consecutive user or assistant turns in your request will be combined into a single turn."
- Errors page: "Claude 4.6 and later models ... do not support prefilling assistant messages", with the message "This model does not support assistant message prefill. The conversation must end with a user message." Whether `count_tokens` enforces this for `claude-sonnet-5-5` is **not documented**; the pilot's live check settles it.

## G. Krippendorff's alpha

Source: K. Krippendorff, "Computing Krippendorff's Alpha-Reliability" (2011), https://www.asc.upenn.edu/sites/default/files/2021-03/Computing%20Krippendorff%27s%20Alpha-Reliability.pdf

- α = 1 − D_o/D_e, with D_o = (1/n) Σ_c Σ_k o_ck δ²_ck and D_e = (1/(n(n−1))) Σ_c Σ_k n_c n_k δ²_ck over the coincidence matrix.
- Interval metric: δ² = (c − k)².
- Units with lone or no values are omitted.
- Worked example (4 observers × 12 units, `.` = missing):

| Unit | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 1 | 2 | 3 | 3 | 2 | 1 | 4 | 1 | 2 | . | . | . |
| B | 1 | 2 | 3 | 3 | 2 | 2 | 4 | 1 | 2 | 5 | . | 3 |
| C | . | 3 | 3 | 3 | 2 | 3 | 4 | 2 | 2 | 5 | 1 | . |
| D | 1 | 2 | 3 | 3 | 2 | 4 | 4 | 1 | 2 | 5 | 1 | . |

  Published: nominal α = 0.743, interval α = 0.849, ratio α = 0.797. A 30-line stdlib recomputation in a scratch directory gave 0.743 and 0.849, so the plan uses these two values as test fixtures.
- Bootstrap intervals and the probability q that α is below a minimum follow Hayes and Krippendorff (2007), https://doi.org/10.1080/19312450709336664, already cited in `docs/research/2026-10-29-llm-grading-without-human-calibration.md` §3.2. Their algorithm resamples pairs of judgments; the plan proposes a question-level cluster bootstrap instead (an owner question).

## Not found

- Whether Gemini bills failed or expired batch requests: the batch page says nothing.
- A key-order guarantee for Gemini structured output.
- Whether `count_tokens` rejects a trailing assistant message for Claude 4.6 and later, and whether it requires `tools` when `tool_use` blocks are present (the Step 9b premise from `docs/plans/2026-10-30-benchmark-integrity.md`).
- Separate Standard and Batch tab figures for the Gemini Flash-Lite models.
- Exact integer token limits for Haiku 5.5 and Opus 5.5 (the docs give "1M" and "128K").
- An explicit structured-outputs line on the GPT-6 Luna model page; the scrape returned limits, effort and prices only. The tuning step exercises it before G1.
