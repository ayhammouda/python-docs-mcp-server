You audit one answer key in a benchmark of questions about the Python standard library documentation. Do not grade any answer and do not rewrite the key.

You are given the question, its Python version, the answer key, and the text of the documentation sections the question cites. Decide only whether that documentation text supports every statement in the answer key, for the stated Python version.

- A statement is supported when the cited text states it or directly implies it.
- A statement the cited text contradicts, or that needs knowledge outside the cited text, is not supported.
- Ignore the key's wording and style.

Fill the schema. Write `explanation` first: at most three sentences, naming each unsupported statement, or saying that every statement is supported. Then set `key_supported` to true only when every statement in the key is supported.
