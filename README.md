# toolratchet

**A CI gate for LLM tool definitions.** It tells you, before you merge or upgrade, whether a change to your tools will

1. **break** agents that already use them (schema compatibility, proven, not guessed),
2. **bloat** the context window (token budget that can only go down), or
3. **smuggle in** instructions (hidden characters, "don't tell the user", exfiltration, permission escalation).

It reads **MCP** servers (stdio or Streamable HTTP), and also **OpenAI**, **Anthropic** and **Gemini** tool definitions. No dependencies. No network except the server you point it at. It never calls a tool; it only sends `initialize` and `tools/list`.

```text
$ toolratchet diff issue-tracker.v1.json issue-tracker.v2.json --check-version

tools   4 -> 4
tokens  266 -> 316 (+50, +18.8%)
largest increases: export_data +64, read_file +57, search_issues +18

[BREAKING] get_file -> read_file
    Tool renamed to 'read_file'; callers still using 'get_file' will fail
[BREAKING] list_branches
    Tool removed
[BREAKING] search_issues  limit
    maximum tightened: 100 -> 50
[BREAKING] search_issues  repo
    'repo' is now required
[BREAKING] search_issues  state
    Value(s) ["all"] are no longer accepted

[REVIEW] [SECURITY] create_issue  description
    Text asks the model to hide something from the user
      Do not tell the user
[REVIEW] [SECURITY] export_data  description
    Text appears to direct data to an external destination
[REVIEW] [SECURITY] search_issues  readOnlyHint
    readOnlyHint changed true -> false: the tool is now less restricted
...
required semver bump: major  (declared: patch)
RESULT: FAIL
```

## Install

```bash
pip install toolratchet          # once published; or:
pip install git+https://github.com/devXyi/toolratchet
```

Python 3.10+.

## The ratchet loop

```bash
# 1. Record what your server exposes today and commit the file.
toolratchet lock -- python my_server.py

# 2. In CI, fail the build if the surface drifts in a way that matters.
toolratchet check -- python my_server.py

# 3. Changed on purpose? Review the report, bump the version, re-lock.
toolratchet lock -- python my_server.py
```

`check` exits `1` when the gate fails, `2` on usage or connection errors.

Other ways to point at a surface (anywhere a source is accepted):

| Source | Example |
|---|---|
| stdio server | `-- python server.py` or `cmd:"npx some-mcp-server"` |
| remote server | `https://mcp.example.com/mcp` (add `-H "Authorization: Bearer $TOKEN"`) |
| JSON file | MCP `tools/list` result, lock file, or an OpenAI / Anthropic / Gemini tools array |

Compare anything with anything, for example a vendor-format tools file against a live server:

```bash
toolratchet diff tools.openai.json https://staging.example.com/mcp
```

Reviewing a dependency upgrade? Diff the two versions of someone else's server:

```bash
toolratchet diff "cmd:npx some-mcp-server@1.2.0" "cmd:npx some-mcp-server@1.3.0"
```

## What it checks

| Question | How |
|---|---|
| Will existing callers break? | A conservative JSON Schema engine decides whether *everything the old input schema accepted is still accepted* (and, for output schemas, whether *everything the new one can return was already promised*). It resolves `$ref`, and handles `anyOf` / `oneOf` / `allOf`, enums, bounds, lengths, `required`, `additionalProperties`, arrays and more. |
| Can the engine be sure? | If it cannot prove a change safe (regex inclusion, `not`, `if/then/else`, ...), it reports **REVIEW**, never a silent pass. |
| Was a tool renamed? | Remove + add pairs are matched by schema, description and name similarity and reported as one rename. |
| Did models get a different prompt? | Description and parameter-doc changes are surfaced with a sentence-level diff. |
| Did the token cost grow? | Per-tool and total tokens, with `--max-tokens`, `--max-increase PCT`, `--max-tool-tokens`, and `--ratchet` (never grow). |
| Is something hostile hiding in the new text? | Only *new* text is scanned: invisible Unicode (including "tag" smuggling, decoded for you), instruction overrides, concealment, covert actions, exfiltration, credential-file references, pseudo-system tags, new URLs. |
| Did a tool get riskier? | `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` escalations are flagged. |
| Is the version honest? | `--check-version` compares the declared semver bump with the required one. A major bump *acknowledges* breaking changes. |

Every change is one of **BREAKING**, **REVIEW** or **info**. The full table is in [docs/CLASSIFICATION.md](docs/CLASSIFICATION.md).

## Use it in GitHub Actions

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with: { python-version: "3.12" }
- run: pip install toolratchet
- run: toolratchet check --summary-file "$GITHUB_STEP_SUMMARY" --format github -- python my_server.py
```

`--summary-file` appends a markdown report to the job summary; `--format github` turns each change into an annotation. A composite action is included (`action.yml`), and `--format markdown` output carries a hidden marker so a bot can keep one sticky PR comment up to date.

## Use it as a library

```python
import json
from toolratchet import parse_surface, compare

old = parse_surface(json.load(open("old.json")))
new = parse_surface(json.load(open("new.json")))
for change in compare(old, new):
    print(change.level.name, change.tool, change.path, change.message)
```

`toolratchet.schema.check_subschema(sub, sup)` is usable on its own for any JSON Schema pair.

## Honest limits

- It sees **schemas and text**, not behaviour. A server that changes what a tool *does* behind an identical schema is invisible to any diff. Pair it with tests.
- Compatibility assumes callers send **only properties the old schema declares**. That is what makes "added an optional parameter" compatible, and it is documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Token counts are a **deterministic estimate** (good for before/after deltas). Use `--tokenizer tiktoken` (extra: `pip install 'toolratchet[tiktoken]'`) for exact OpenAI-encoding counts.
- Integrity checks are **heuristics**. A flag means "a human should look", and a clean result is not a security audit. For deep semantic analysis or runtime enforcement, combine it with a scanner or a proxy. See [docs/POSITIONING.md](docs/POSITIONING.md).

## Development

```bash
python -m unittest discover -s tests -v
```

The test-suite includes table-driven compatibility cases and a randomized soundness test that checks the engine never proves a subschema relation that a validator can contradict.

MIT licensed.
