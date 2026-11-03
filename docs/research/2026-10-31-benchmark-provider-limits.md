# Benchmark provider limits, prices and dependency dates

- **Date:** 2026-10-31
- **Scope:** Facts the benchmark harness needs for three models (`claude-sonnet-5-5`, `gpt-6.1-sol`, `gemini-3.8-flash`) and for six pinned or candidate package releases (`anthropic==1.15.0`, `openai==3.32.0`, `google-genai==2.31.0`, `jiter==0.17.0`, `pyasn1==0.6.4`, `pyasn1==0.6.5`).
- **Status:** Research only. No product code changed. No API key was read or used and no paid API call was made.
- **Method:** Primary sources only: vendor documentation and pricing pages, the `pyasn1` GitHub repository and its Security Advisories, PyPI's JSON API (`https://pypi.org/pypi/<name>/<version>/json`) and OSV (`https://api.osv.dev`). Pages were read with `firecrawl_scrape` and `firecrawl_search`. The PyPI, OSV and GitHub JSON endpoints were read with `curl`. Where a page is dynamic, the scraper was asked for verbatim quotes of the rows. Anything marked **(inferred)** is my reading, not a published statement. Times are UTC; see "Retrieved".

## Questions

- **A.** `claude-sonnet-5-5` on the Anthropic Messages API: context window, max output, prices (input, output, 5-minute and 1-hour cache write, cache read), long-context tier, and whether adaptive thinking tokens count inside `max_tokens`.
- **B.** `gpt-6.1-sol` on the OpenAI Responses API: context window, max output, whether reasoning tokens count inside `max_output_tokens`, prices (input, cached input, output, cache write), long-context tier, default reasoning effort.
- **C.** `gemini-3.8-flash` through `google-genai`, paid tier: max input and output tokens, whether thinking tokens count inside `max_output_tokens`, default thinking level and any thinking ceiling, prices (input, output, cache read, cache storage, prompt-size tier), and the price change dated 2027-01-01.
- **D.** Is an output cap of 16,384 tokens at or below the documented maximum output for A, B and C?
- **E.** Is `pyasn1` 0.6.5 (2026-10-27) a security fix? Which advisories affect 0.6.4 and earlier, and what is vulnerable?
- **F.** First-file PyPI upload time for six releases, and whether each is at least 14 days before 2026-10-31.

## Answers at a glance

| Q | Answer | Main source |
|---|---|---|
| A | 1M context, 128K max output. $2 in, $10 out, $2.50 5m write, $4 1h write, $0.10 cache read, per 1M tokens. No long-context tier. Thinking tokens count inside `max_tokens`. | https://platform.claude.com/docs/en/models/sonnet-5-5/overview |
| B | 1,050,000 context, 128,000 max output. Reasoning tokens count inside `max_output_tokens`. $2.00 in, $0.10 cached, $10.00 out, $2.50 (30m) or $5.00 (12h) cache write. Above 272K input: $4.00 / $0.20 / $15.00. Default effort `medium`. | https://developers.openai.com/api/docs/models/gpt-6.1-sol |
| C | 1,048,576 input, 65,536 output. Thought tokens count inside `max_output_tokens`. Default `medium`. $0.75 in, $3.75 out, $0.075 cache read per 1M tokens, and $0.50 per 1M tokens per hour of cache storage, through 2026-12-31. From 2027-01-01 all four double. No prompt-size tier. | https://ai.google.dev/gemini-api/docs/pricing |
| D | Yes for all three: 16,384 <= 128,000 (A), <= 128,000 (B), <= 65,536 (C). Thinking shares the cap on all three. | Sections A to C |
| E | Yes. 0.6.5 fixes four high-severity DoS advisories that affect 0.6.4: GHSA-fm9c-5rcf-2q5p, GHSA-9m75-vc3g-4x39, GHSA-2qm8-x737-gmw5, GHSA-mm57-4v3p-rchx. | https://github.com/pyasn1/pyasn1/releases/tag/v0.6.5 |
| F | anthropic 1.15.0: 2026-10-15, yes. openai 3.32.0: 2026-10-16, yes (14.15 days). google-genai 2.31.0: 2026-10-14, yes. jiter 0.17.0: 2026-09-12, yes. pyasn1 0.6.4: 2026-07-09, yes. pyasn1 0.6.5: 2026-10-27, no (3.73 days). | https://pypi.org/pypi/pyasn1/0.6.5/json |

## A. `claude-sonnet-5-5` (Anthropic Messages API)

**Context window and max output.**

- Context window is 1M tokens. Max output is 128K tokens. Source: https://platform.claude.com/docs/en/models/sonnet-5-5/overview, section "Capabilities": "Context window 1M tokens", "Max output 128K tokens".
- The comparison table on https://platform.claude.com/docs/en/about-claude/models/overview agrees. Its Claude Sonnet 5.5 column reads "Claude API ID | claude-sonnet-5-5", "Context window | 1M tokens", "Max output | 128K tokens".
- https://platform.claude.com/docs/en/build-with-claude/context-windows: "Claude Sonnet 5.5, ... have a 1M-token context window. A single request to any of them can generate up to 128k output tokens (max_tokens)."
- Batch API only, in beta: "Max output (Batch API, beta) 300K tokens" with the `output-300k-2026-03-24` beta header (same Sonnet 5.5 page, section "Good to know"). This is not relevant to the Messages API cap.
- The docs give rounded values ("1M", "128K"). The exact integers (`max_input_tokens`, `max_tokens`) come from the Models API, which needs an API key. Not read (see "Not found").

**Prices per 1M tokens (Claude API, standard).**

- Source A1, the Sonnet 5.5 page, section "Pricing": "Input $2 / MTok", "Output $10 / MTok", "5m cache write $2.50 / MTok", "1h cache write $4 / MTok", "Cache read $0.10 / MTok", "Batch API 50% discount on input and output".
- Source A2, https://platform.claude.com/docs/en/about-claude/pricing, table "Model pricing", columns "Input | Output | 5m writes | 1h writes | Hits and refreshes": "Claude Sonnet 5.5 ... | $2/ MTok | $10/ MTok | $2.50/ MTok | $4/ MTok | $0.10/ MTok".
- Cache read is 0.05x base input for this model, not the usual 0.1x. Same page: "0.1x base input price (0.025x on Claude Fable 5.1 and Claude Mythos 5.1; 0.05x on Claude Opus 5.5 and Claude Sonnet 5.5)". The row for the earlier Claude Sonnet 5 shows $0.20, so do not reuse that figure.
- Other modifiers on the same page: Batch API input $1 and output $5 per MTok. `inference_geo: "us"` applies a 1.1x multiplier to all categories.

**Long-context tier.** None for this model. https://platform.claude.com/docs/en/about-claude/pricing, section "Long context pricing": "Claude 4.6 and later models (except Claude Haiku 5.5) and Claude Mythos Preview include the full 1M token context window at standard pricing. (A 900k-token request is billed at the same per-token rate as a 9k-token request.)" Only Claude Haiku 5.5 has a threshold (100,000 prompt tokens), so no threshold applies to Sonnet 5.5.

**Do adaptive thinking tokens count inside `max_tokens`? Yes.**

- https://platform.claude.com/docs/en/build-with-claude/thinking: "the tokens Claude spends reasoning are billed as output tokens, even when the thinking text isn't returned to you, and they count toward max_tokens alongside the response text."
- Same page: "Thinking tokens count toward max_tokens, so set it high enough to leave room for both the thinking and the response text."
- Same page: "max_tokens, which includes all thinking Claude generates in the current turn, is enforced as a strict limit."
- https://platform.claude.com/docs/en/build-with-claude/context-windows: "Thinking tokens are a subset of your max_tokens parameter, are billed as output tokens, and count toward rate limits."
- The Sonnet 5.5 page links its "Adaptive thinking" resource to this same thinking page, so the rule applies to adaptive thinking. It states no model-specific exception.
- Related facts from the Sonnet 5.5 page: "Adaptive thinking is on by default", default effort is `high`, and the Models API reports `thinking.types.disabled.supported` as `false` for this model (models overview page). So a request cannot switch thinking off. The lowest setting is `between_tools`.

## B. `gpt-6.1-sol` (OpenAI Responses API)

**Context window and max output.**

- https://developers.openai.com/api/docs/models/gpt-6.1-sol: "1,050,000 context window", "128,000 max output tokens", "Reasoning token support".
- The page publishes one combined context window. It gives no separate "maximum input" figure (see "Not found").

**Do reasoning tokens count inside `max_output_tokens`? Yes.**

- https://developers.openai.com/api/docs/guides/reasoning: "you can limit the total number of tokens the model generates, including reasoning tokens, visible output tokens, and non-visible formatting tokens, by using the max_output_tokens parameter."
- Same page: "If the generated tokens reach the context window limit or the max_output_tokens value you've set, you'll receive a response with a status of incomplete and incomplete_details with reason set to max_output_tokens. This might occur before any visible output tokens are produced".
- Same page: "OpenAI recommends reserving at least 25,000 tokens for reasoning and outputs when you start experimenting with these models."
- https://developers.openai.com/api/docs/guides/token-counting: "The max_output_tokens and max_completion_tokens parameters limit all tokens generated by the model, including non-visible tokens."
- API reference, https://developers.openai.com/api/reference/resources/responses/methods/create.md: "max_output_tokens: optional integer or null. An upper bound for the number of tokens that can be generated for a response, including visible output tokens and reasoning tokens. minimum: 16".

**Prices per 1M tokens (Standard).**

- The model page, section "Pricing": "Input $2.00", "Cached input $0.10", "Cache writes $2.50", "Output $10.00". Notes: "Cached input tokens are priced at 5% of the uncached input token rate." "Cache writes are billed at 1.25x the uncached input token rate."
- https://developers.openai.com/api/docs/pricing (read as `pricing.md`), first text-token table, row: "gpt-6.1-sol | $2.00 | $10.00 | $0.10 | $2.50 | $5.00 | $4.00 | $15.00 | $0.20 | $5.00 | $10.00". Header: "Model | Short context input | Short context output | Short context cached input | Short context 30m write | Short context 12h write | Long context input | Long context output | Long context cached input | Long context 30m write | Long context 12h write".
- So there is a cache-write charge: $2.50 for a 30-minute write and $5.00 for a 12-hour write (short context). The model page shows only the $2.50 figure. The migration guide ties the `30m` value to `prompt_cache_options.ttl` (https://developers.openai.com/api/docs/guides/latest-model.md).
- The scraper dropped the tier labels on the pricing tables. I took the first table to be Standard because its row equals the model page's Standard figures. The later tables match the model page's multipliers (**inferred**): "Fast mode prices are 2x Standard. Batch and Flex prices are 50% lower than Standard." and "Ultrafast mode prices are 6x Standard." The pricing page also notes "Regional processing ... 10% uplift" for eligible models.

**Long-context tier. Yes, above 272K input tokens.**

- The model page: "API prompts with more than 272K input tokens are priced at 2x input and cache rates and 1.5x output for the full request."
- The pricing page: "Short context: <=272K input tokens. Long context: >272K input tokens." and the long-context columns of the row above: input $4.00, output $15.00, cached input $0.20, 30m write $5.00, 12h write $10.00.

**Default reasoning effort: `medium`.**

- The model page: "`reasoning.effort` supports `low`, `medium` (default), `high`, `xhigh`, and `max`."
- https://developers.openai.com/api/docs/guides/latest-model.md: "It supports `low`, `medium` (default), `high`, `xhigh`, and `max` reasoning effort." and "GPT-6 Astra and GPT-6.1 Sol do not support the `none` reasoning effort".

## C. `gemini-3.8-flash` (Gemini API, `google-genai`, paid tier)

**Token limits.**

- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash, table row "Token limits": "Input token limit 1,048,576", "Output token limit 65,536".
- https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8: "Gemini 3.8 Flash supports a 1M token context window, 64k max output tokens, tunable thinking levels (`low`, `medium`, `high`)".

**Do thinking tokens count inside `max_output_tokens`? Yes.**

- generateContent API page, https://ai.google.dev/gemini-api/docs/generate-content/thinking: "The max_output_tokens generation parameter sets the maximum number of tokens a response can generate, including thought tokens. When set, this parameter acts as a hard cutoff ... If the model hits this limit while reasoning, it stops generating with finish_reason: MAX_TOKENS and returns truncated or empty output (while still billing for any thinking tokens generated)."
- Interactions API page, https://ai.google.dev/gemini-api/docs/thinking, section "Token limits and max_output_tokens": the same sentence, with status `"incomplete"` instead of `finish_reason`.
- Both pages advise: "To reduce cost or latency without truncating responses, lower thinking_level (low or medium) instead of setting a small max_output_tokens."
- The docs label the Interactions API "(Recommended)" and generateContent "(Legacy)". The SDK exposes both, so I read both pages. They agree.

**Default thinking level: `medium`. No thinking-token ceiling is published.**

- https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8, table "New model": "Gemini 3.8 Flash | `gemini-3.8-flash` | Default thinking level `medium`". Same page: "Medium (default): Best quality for most tasks."
- generateContent thinking page, table "Thinking Level": `minimal` "Not supported (error)"; `low` "Supported"; `medium` "Supported (Default)"; `high` "Supported (Dynamic)" (columns for Gemini 3.8 Flash).
- No numeric ceiling or budget for `medium` appears on the model page, the whats-new page or either thinking page. The `thinkingBudget` table on the generateContent thinking page lists only 2.5-series models, and the whats-new migration step says "Replace `thinking_budget` with the string enum `thinking_level`."

**Paid-tier prices per 1M tokens (Standard tab).**

Source: https://ai.google.dev/gemini-api/docs/pricing, section "Gemini 3.8 Flash". The scraper confirmed the tab is Standard.

| Item | Through 2026-12-31 | Starting 2027-01-01 |
|---|---|---|
| Input price | $0.75 | $1.50 |
| Output price (including thinking tokens) | $3.75 | $7.50 |
| Context caching price (cache read) | $0.075 | $0.15 |
| Context cache storage, per 1M tokens per hour | $0.50 | $1.00 |

- Quoted rows: "Input price | Free of charge | $0.75 through December 31, 2026.$1.50 starting January 1, 2027." and "Output price (including thinking tokens) | Free of charge | $3.75 through December 31, 2026.$7.50 starting January 1, 2027." and "Context caching price | ... | $0.075 through December 31, 2026.$0.15 starting January 1, 2027.$0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026.$1.00 / 1,000,000 tokens per hour (storage price) starting January 1, 2027."
- **Prompt-size tier: none published.** The Standard tab rows are input, output, context caching, two grounding rows and a data-use row. A targeted question to the page returned "No" for any price at a prompt-size threshold.
- Other tabs on the same page, not asked: Batch and Flex are half the Standard price ($0.375 / $1.875 input and output through 2026-12-31). Priority is $1.35 / $6.75. The tab labels for these three come from tab order and price ratios (**inferred**).

**The price change dated 2027-01-01.** All four Standard token prices double on 2027-01-01 (table above). Three sources agree:

- The pricing page rows quoted above.
- https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8: "Gemini 3.8 Flash is available at an introductory rate of $0.75/1M input tokens and $3.75/1M output tokens through December 31, 2026. Standard pricing of $1.50/1M input tokens and $7.50/1M output tokens takes effect January 1, 2027."
- Google's launch post of 2026-09-02, https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/: "Introductory price expires on December 31, 2026. Starting January 1, 2027, $1.50/1M input tokens and $7.50/1M output tokens will apply."

## D. Is a 16,384-token output cap at or below the documented maximum?

| Provider | Documented max output | 16,384 at or below it? | Source |
|---|---|---|---|
| Anthropic `claude-sonnet-5-5` | 128K tokens | Yes (about 12.8%) | Section A |
| OpenAI `gpt-6.1-sol` | 128,000 tokens | Yes (about 12.8%) | Section B |
| Google `gemini-3.8-flash` | 65,536 tokens | Yes (25%) | Section C |

The cap is legal on all three. Thinking shares it on all three, so it can still cut an answer short:

- Anthropic: thinking is on by default, cannot be disabled, and "is enforced as a strict limit" (section A).
- OpenAI: the docs recommend "reserving at least 25,000 tokens for reasoning and outputs" (section B), which is more than 16,384.
- Google: a hit on the cap while reasoning returns `MAX_TOKENS` with "truncated or empty output" (section C).

Owner ruling raised the cap to 32,768: 25.6%, 25.6% and 50% of the documented maxima.

## E. `pyasn1` 0.6.5: security fix?

**Yes. Release 0.6.5 fixes four high-severity denial-of-service advisories that all affect 0.6.4.**

- Release notes, https://github.com/pyasn1/pyasn1/releases/tag/v0.6.5, "Release 0.6.5", published 2026-10-27T06:47:21Z. The same text is `CHANGES.rst` at tag v0.6.5, "Revision 0.6.5, released 26-10-2026". Each bullet is an advisory ID:
  - "GHSA-mm57-4v3p-rchx: Decoded binary REAL mantissas in linear time instead of repeatedly shifting a growing integer and slicing the remaining bytes"
  - "GHSA-2qm8-x737-gmw5: Reassembled constructed BIT STRING fragments in linear time"
  - "GHSA-9m75-vc3g-4x39: Reassembled constructed OCTET STRING and character-string fragments and indefinite-length ANY content in linear time."
  - "GHSA-fm9c-5rcf-2q5p: Bounded schemaless container type tracking after two distinct tag sets to prevent quadratic CPU use from colliding long tag IDs"
- No bullet mentions a feature or non-security change.

**Advisories, from the GitHub advisory database (`api.github.com/advisories?affects=pyasn1&ecosystem=pip`) and OSV (`https://api.osv.dev/v1/vulns/<id>`).** All four: CVSS 3.1 `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H` (score 7.5, High), CWE-407 (inefficient algorithmic complexity), fixed in 0.6.5.

| GHSA | CVE | Affected | What is vulnerable |
|---|---|---|---|
| GHSA-fm9c-5rcf-2q5p | CVE-2026-119194 | `>= 0.3.3, < 0.6.5` | Schemaless BER, CER and DER decoding. `ConstructedPayloadDecoderBase._decodeComponentsSchemaless` grows a set of tag sets. Many distinct long tag IDs with colliding hashes cause quadratic CPU use. |
| GHSA-9m75-vc3g-4x39 | CVE-2026-119195 | `<= 0.6.4` | Constructed OCTET STRING and character strings built from many fragments (BER, CER, and some non-DER forms in DER), and indefinite-length `ANY` (BER, CER). Each fragment re-copies the accumulated value. |
| GHSA-2qm8-x737-gmw5 | CVE-2026-119196 | `<= 0.6.4` | Constructed BIT STRING decoding. `BitStringPayloadDecoder` shifts and copies the whole value for each fragment. BER and CER, and DER when an `asn1Spec` is given. |
| GHSA-mm57-4v3p-rchx | CVE-2026-119197 | `<= 0.6.4` | Binary REAL decoding. `RealPayloadDecoder.valueDecoder` builds a large mantissa one octet at a time. BER, CER and DER all reach it. Decoding alone triggers it. |

- Impact quote (GHSA-2qm8; the other three say much the same): "An application that passes sufficiently large, attacker-controlled ASN.1 data to an affected decoder can suffer CPU exhaustion during `decode()`".
- OSV query for `pyasn1` 0.6.4 (`POST /v1/query`) returns exactly these four IDs. The query for 0.6.5 returns none. The GitHub repository's own advisories list shows the same four, published 2026-10-27T06:58Z to 06:59Z.
- Older advisories do not affect 0.6.4. CVE-2026-59884, CVE-2026-59885 and CVE-2026-59886 (all `<= 0.6.3`) were fixed in 0.6.4, per `CHANGES.rst` "Revision 0.6.4, released 08-07-2026".
- PyPI's JSON for 0.6.4 and for 0.6.5 has an empty `vulnerabilities` list at the time of reading. PyPI shows no advisory, so PyPI alone does not answer this question.
- **Timing caveat.** The GitHub database and OSV records for these IDs show `published` 2026-10-30T23:03Z to 23:07Z, about ten minutes before this lookup. A scan from earlier on 2026-10-30 would have reported 0.6.4 as clean. A lock at 0.6.4 is now flagged by OSV-based scanners.
- **Rule clash.** 0.6.5 was uploaded 2026-10-27T06:32:48Z, so it is not 14 days old until 2026-11-10T06:32:48Z (section F). Any 14-day minimum-age rule therefore keeps the vulnerable 0.6.4 in place until then.

## F. PyPI first-upload dates

Source for every row: `https://pypi.org/pypi/<name>/<version>/json`, field `urls[].upload_time_iso_8601`, the earliest file of the release. Age is measured to 2026-10-31T00:00:00Z. The machine clock read 2026-10-30T23:20Z, which is the same instant as 2026-10-31 00:20 local (CET), and gives the same yes/no for every row. The 14-day cutoff is 2026-10-17T00:00:00Z.

| Release | First file (earliest upload) | Upload time (UTC) | Age | At least 14 days before 2026-10-31? |
|---|---|---|---|---|
| `anthropic==1.15.0` | `anthropic-1.15.0-py3-none-any.whl` (2 files) | 2026-10-15T19:45:32.951798Z | 15.18 days | Yes |
| `openai==3.32.0` | `openai-3.32.0-py3-none-any.whl` (2 files) | 2026-10-16T20:18:12.544531Z | 14.15 days | Yes, by 3 h 42 min |
| `google-genai==2.31.0` | `google_genai-2.31.0-py3-none-any.whl` (2 files) | 2026-10-14T23:52:22.766734Z | 16.01 days | Yes |
| `jiter==0.17.0` | `jiter-0.17.0-cp310-cp310-macosx_10_12_x86_64.whl` (119 files, the last at 2026-09-12T15:14:14Z) | 2026-09-12T15:11:10.440276Z | 48.37 days | Yes |
| `pyasn1==0.6.4` | `pyasn1-0.6.4-py3-none-any.whl` (2 files) | 2026-07-09T01:12:32.920042Z | 113.95 days | Yes |
| `pyasn1==0.6.5` | `pyasn1-0.6.5-py3-none-any.whl` (2 files) | 2026-10-27T06:32:48.045712Z | 3.73 days | **No** (14 days on 2026-11-10T06:32:48Z) |

- None of the six releases is yanked (`info.yanked` is `False` for each).
- `openai==3.32.0` passes any check run from 2026-10-30T20:18:13Z onward. It is the only borderline row.
- GitHub gives a different date for `pyasn1` 0.6.5: release `v0.6.5` was published 2026-10-27T06:47:21Z. PyPI's upload time (06:32:48Z) is the one that counts for F.

## Not found

- **Exact token integers for Sonnet 5.5.** The docs publish "1M tokens" and "128K tokens" only. The exact `max_input_tokens` and `max_tokens` are returned by the Models API (https://platform.claude.com/docs/en/api/models/list), which needs an API key. Not called.
- **A Sonnet 5.5 long-context price tier.** None published. The pricing page says the full 1M window is at standard pricing (section A).
- **A separate maximum-input figure for `gpt-6.1-sol`.** Checked https://developers.openai.com/api/docs/models/gpt-6.1-sol and https://developers.openai.com/api/docs/pricing. Only the combined "1,050,000 context window" and "128,000 max output tokens" appear.
- **A thinking-token ceiling or budget for `gemini-3.8-flash` at `medium`.** Checked the model page, the whats-new page, and the two thinking pages. None gives one. The only `thinkingBudget` ranges are for 2.5-series models.
- **A prompt-size price tier for `gemini-3.8-flash`.** None on the Standard tab of https://ai.google.dev/gemini-api/docs/pricing.
- **Pricing-tab labels in scraper output.** The OpenAI pricing tables and the non-Standard Gemini tabs came back without tab names. Labels in those places are inferred from price ratios and tab order.
- **`pyasn1` changelog on Read the Docs.** https://pyasn1.readthedocs.io/en/latest/changelog.html loaded and lists v0.6.5, but the scraper returned only the version flyout, not the changelog text. The changelog text above comes from `CHANGES.rst` at tag v0.6.5 and the GitHub release.
- **PyPI advisory data.** PyPI's JSON `vulnerabilities` is empty for 0.6.4 and 0.6.5 at the time of reading, although OSV and GitHub list four advisories against 0.6.4.
- **Exact per-page read times for scraped pages.** The scraper returns no timestamp. Windows below are bracketed by `date -u` readings.

## Retrieved

All times UTC on 2026-10-30. Local time was 2026-10-31 (CET, one hour ahead), which is the date this note carries.

| Source | How read | Time (UTC) |
|---|---|---|
| PyPI JSON for the six releases (`anthropic/1.15.0`, `openai/3.32.0`, `google-genai/2.31.0`, `jiter/0.17.0`, `pyasn1/0.6.4`, `pyasn1/0.6.5`) | `curl`, HTTP 200 each | 23:12:11 to 23:12:12; endpoints re-checked 23:16:23 |
| platform.claude.com: models overview, Sonnet 5.5 page, pricing, thinking, context windows | `firecrawl_scrape` | window 23:12:11 to 23:14:59 |
| developers.openai.com: gpt-6.1-sol model page, `pricing.md`, `latest-model.md`, reasoning guide, token-counting guide, `firecrawl_search` for the model | `firecrawl_scrape`, `firecrawl_search` | window 23:12:11 to 23:14:59 |
| ai.google.dev: model page, whats-new-gemini-3.8, pricing, thinking (Interactions), generate-content/thinking; `firecrawl_search` for the model | `firecrawl_scrape`, `firecrawl_search` | window 23:12:11 to 23:14:59 |
| GitHub API: repo `security-advisories`, global `advisories?affects=pyasn1`, `releases`, `tags` for `pyasn1/pyasn1` | `curl` (unauthenticated), HTTP 200 each | started 23:14:59 |
| OSV: `POST /v1/query` for 0.6.4, 0.6.5 and all versions; `GET /v1/vulns/<id>` for the four GHSA IDs | `curl`, HTTP 200 each | 23:15:37 to 23:15:50 |
| `CHANGES.rst` at tag v0.6.5 (raw.githubusercontent.com) | `curl`, HTTP 200 | 23:16:08 |
| blog.google Gemini 3.8 Flash post; Gemini pricing follow-up question; OpenAI Responses API reference (`create.md`) | `firecrawl_scrape` | window 23:16:23 to 23:18:06 |
| pyasn1.readthedocs.io changelog | `firecrawl_scrape` | window 23:17:18 to 23:18:06 |
