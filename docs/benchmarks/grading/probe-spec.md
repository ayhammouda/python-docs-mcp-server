# Probe spec

Agent-written probes for the grading gates (design §9.3, PR 5 plan Task 5).
Each probe is a synthetic answer to one benchmark question whose expected
grade is known in advance. A generator model writes the probe; a model from
another provider checks it against this spec. The code that builds the
requests reads the sections below by their headings.

## Generator

You write one synthetic answer, a probe, to a question about the Python standard library documentation. You are given the question, its Python version, the answer key and the two criteria. Follow the rules for the probe type below exactly.

- Write plain text only, at most 120 words.
- Include no URLs and no links.
- Never name a tool, a product, a model or any system that might have produced an answer.
- Write only the probe: no preamble, no notes, no explanation of what you changed.
- Put the probe in the `probe_text` field.

## PARA

Restate the answer key in new wording. Keep every fact the key states, so both criteria stay met, and add nothing new.

## DROP1

Rewrite the answer key with only the content of criterion 1 removed. Keep criterion 2's content, and add nothing false.

## DROP2

Rewrite the answer key with only the content of criterion 2 removed. Keep criterion 1's content, and add nothing false.

## VER

Rewrite the answer key so that it makes a version error, and keep everything else. For a question about a pair of versions, swap the two versions. For a question about one version, say that the behaviour is new in a Python version later than the question's version, so the probe denies that it holds in the version asked about. The version claim must be false for the version asked about: never name a version, or an "as of" or "starting in" version, under which the statement would still be true there.

## FACT

Rewrite the answer key with its core fact altered so that the statement is wrong, and state it confidently, without hedging.

## KEYX

Copy the answer key, then add one sentence that contradicts it.

## SPLIT

Offer two alternatives, one correct and one wrong, without committing to either of them.

## Checker

You check one probe against its type's rules in the probe spec. You are given the question, its Python version, the answer key, the two criteria, the probe and the rules for its type. Decide only whether the probe meets those rules and the generator rules (plain text, at most 120 words, no URLs, no system names, the probe only). Do not grade the probe as an answer.

Fill the schema. Write `explanation` first, in at most two sentences, then set `meets_spec`.
