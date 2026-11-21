# Structured Python answer pilot

The full comparison stopped at its pilot gate. Eight model–tool conditions scored 30/30. Gemini 3.8 Flash with official-docs fetch produced 22/30 usable replies, below the registered minimum of 27. Five replies enclosed the answer in Markdown fences, and three trajectories reached the model-call limit. All 262 usable replies chose the documented answer. The full 1,350-answer run did not start.

The pilot used ten questions about Python 3.11–3.13, two from each of five categories. Each question had four choices. A locally checked JSON answer earned one point for the documented choice. Each question was asked three times under each condition. All planned replies are included in accuracy. The models used their hosted APIs, while orchestration, tool execution, scoring and analysis ran locally.

| Model | Documentation available | Correct / planned | Accuracy | Valid wrong | Invalid format | Unsuccessful or missing | Median wall time |
|---|---|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5-5 | Official docs fetch | 30/30 | 100.0% | 0 | 0 | 0 | 8.4 s |
| claude-sonnet-5-5 | None | 30/30 | 100.0% | 0 | 0 | 0 | 4.4 s |
| claude-sonnet-5-5 | Python Docs MCP | 30/30 | 100.0% | 0 | 0 | 0 | 8.3 s |
| gemini-3.8-flash | Official docs fetch | 22/30 | 73.3% | 0 | 5 | 3 | 21.1 s |
| gemini-3.8-flash | None | 30/30 | 100.0% | 0 | 0 | 0 | 9.5 s |
| gemini-3.8-flash | Python Docs MCP | 30/30 | 100.0% | 0 | 0 | 0 | 21.4 s |
| gpt-6.1-sol | Official docs fetch | 30/30 | 100.0% | 0 | 0 | 0 | 10.3 s |
| gpt-6.1-sol | None | 30/30 | 100.0% | 0 | 0 | 0 | 4.6 s |
| gpt-6.1-sol | Python Docs MCP | 30/30 | 100.0% | 0 | 0 | 0 | 11.9 s |

Median elapsed time uses retained trajectories with a timing record. It includes waiting for the shared provider reservation. The machine-readable summary gives timing and token coverage, plus the number of trajectories that used a tool and received a non-error tool response.

| Provider | This stage (USD) | Cumulative booked (USD) | Cap (USD) |
|---|---:|---:|---:|
| Anthropic | 1.1475 | 16.7008 | 180 |
| Google | 0.4941 | 1.3314 | 180 |
| Openai | 0.4293 | 0.9336 | 65 |

The pilot booked USD 2.0708 in total, with zero outstanding reservations. Costs use the shared benchmark ledger and its frozen rate card. Provider-native token totals count reported calls and can differ in meaning across providers.

These results describe factual recognition on the selected Python documentation questions. The design does not measure the completeness of an open-ended explanation or the quality of a citation.

[Per-answer scores](answers.csv) · [Scores by category](categories.csv) · [Run identity, usage and denominators](summary.json)

## Reproducibility

The server source and dependency lockfile match v0.5.0. The study commit pins the model matrix, question and option order, official documentation keys, parser, call limits and scoring rules. The 90/90 score for the no-tool condition leaves no room for an accuracy improvement on these ten questions. The full 50-question comparison remains unmeasured.

[Pre-collection registration](https://github.com/ayhammouda/python-docs-mcp-server/issues/63#issuecomment-7070237745) · [Frozen protocol](https://github.com/ayhammouda/python-docs-mcp-server/blob/e2b699c78160115d312bf6038cfe68b9ed907253/docs/benchmarks/structured/PROTOCOL.md) · [Questions and keys](https://github.com/ayhammouda/python-docs-mcp-server/blob/e2b699c78160115d312bf6038cfe68b9ed907253/docs/benchmarks/structured/items.json) · [Renderer and local scorer](https://github.com/ayhammouda/python-docs-mcp-server/blob/e2b699c78160115d312bf6038cfe68b9ed907253/benchmarks/structured.py)
