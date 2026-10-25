# Architecture

```
            sources                         core                          output
  ┌─────────────────────────┐   ┌──────────────────────────────┐   ┌────────────────┐
  │ JSON file / lock file   │   │                              │   │ text           │
  │ MCP stdio  (capture.py) ├──►│  ingest.py  → Surface        │   │ markdown (PR)  │
  │ MCP HTTP   (capture.py) │   │  diff.py    → [Change]       ├──►│ json           │
  │ OpenAI/Anthropic/Gemini │   │    ├ schema.py   (compat)    │   │ github annot.  │
  └─────────────────────────┘   │    ├ integrity.py(new text)  │   │ step summary   │
                                │    └ rename matching         │   └────────────────┘
                                │  budget.py  → token costs    │
                                │  report.py  → pass / fail    │
                                │  semver.py  → required bump  │
                                └──────────────────────────────┘
                                         cli.py wires it together
```

## Modules

| Module | Responsibility |
|---|---|
| `model.py` | `ToolDef`, `Surface`, `Change`, `Level`. Vendor-neutral. |
| `ingest.py` | Parses MCP, OpenAI (chat and flat), Anthropic and Gemini tool definitions, JSON-RPC wrappers and lock files into a `Surface`. Lower-cases Gemini's `STRING`/`OBJECT` types. |
| `capture.py` | Minimal MCP client over stdio and Streamable HTTP (JSON or SSE). Sends `initialize`, `notifications/initialized` and `tools/list` (paginated) only. Ignores non-JSON noise on stdout. |
| `sources.py` | Resolves a source spec (file, URL, `cmd:`, argv) and writes lock files. |
| `schema.py` | The compatibility engine: `check_subschema`, plus a tri-state `validate` used for enum reasoning and the soundness test. |
| `diff.py` | Compares tools; maps schema issues to wording per direction; param-level walk; annotations; rename matching. |
| `integrity.py` | Heuristics over **new** text only; collects every model-visible string in a schema. |
| `budget.py`, `tokens.py` | Token accounting and limits. |
| `semver.py` | Version parsing, required vs declared bump, the `0.x` shift rule. |
| `report.py` | Applies a `Policy` to produce a `Result` (failures and notes). |
| `render.py` | text, markdown, JSON, GitHub annotation renderers. |
| `cli.py` | `lock`, `check`, `diff`, `inspect`. |

## Design decisions

### 1. Tri-state, sound compatibility

`check_subschema(sub, sup)` returns issues:

- **no issues**: proven (`sub ⊆ sup`)
- **definite issue**: incompatible (or, for a few constraints such as "pattern added", assumed incompatible)
- **non-definite issue**: unknown (shown as REVIEW)

The invariant is *never prove what you did not prove*. A false alarm costs a minute of review; a false "compatible" costs an outage. Anything outside the supported keyword set (`not`, `if`/`then`/`else`, `patternProperties`, `contains`, dynamic refs, ...) yields unknown unless the two sides are identical.

How the hard cases work:

- **Unions in the subschema** (`anyOf`/`oneOf`): every branch must fit inside the superschema.
- **Unions in the superschema**: the subschema must fit one branch. If it fits none, the result is *definite* only when every branch fails on type grounds; otherwise unknown (the union of branches might still cover it).
- **`allOf`**: merged into one schema where safe (type intersection honours `integer ⊂ number`, `required` union, bounds tightened); unmergeable leftovers stay as a conjunction, which can only be proven by one member alone.
- **Finite sets** (`enum`, `const`, booleans, `null`): checked exactly by validating each value against the superschema.
- **`$ref`**: resolved locally; recursion is handled coinductively (a pair already being compared is assumed to hold).

### 2. Direction matters

| Schema | Rule | Why |
|---|---|---|
| Input | `old ⊆ new` | Callers send what the old schema allowed. |
| Output | `new ⊆ old` | Consumers rely on what the old schema promised. |

One engine, two directions. Wording is chosen per direction in `diff._phrase`.

### 3. The one deliberate relaxation

> Callers only send properties the old schema declares.

So adding an optional, constrained parameter to an open schema is compatible, as in every mainstream API-diff tool. The randomized soundness test encodes the same assumption (it closes the old schema before comparing against a validator).

### 4. Why it never calls a tool

Only `initialize` and `tools/list` are sent. A compatibility gate should be safe to point at production.

### 5. Why zero dependencies

It runs in a bare CI job, an air-gapped runner, or a pre-commit hook without resolving anything. The MCP client is ~150 lines and deliberately minimal.

### 6. Integrity scanning only looks at new text

Flags are computed against a baseline (the old text), sentence by sentence. An unchanged legacy description never re-triggers. New tools are scanned in full. This keeps the signal high in review.

## Extending

| To add | Where |
|---|---|
| A tool-definition format | `ingest._tool_from` (map its keys), add a test in `tests/test_ingest.py`. |
| An integrity pattern | `integrity._PATTERNS` (kind, severity, regex) and a message in `diff._SEC_MSG`. |
| An output format | A function in `render.py`, register it in `cli._gate`'s renderer map. |
| A schema keyword | `schema._leaf` (or the helpers it calls), plus table cases in `tests/test_schema.py`. The randomized test will tell you if the new logic is unsound. |

## Testing strategy

- **Table-driven cases** for proven-compatible, proven-breaking and unknown relations.
- **Randomized soundness**: random schema pairs; whenever the engine proves `a ⊆ b`, no sample value may be valid in `a` and invalid in `b`. This caught a real bug during development (`integer ∩ number` was treated as empty in `allOf` merging).
- **Integration tests** drive the real CLI against a stdlib mini MCP server (stdio) and a local HTTP server (JSON and SSE), including pagination, stdout noise, timeouts, 401 handling and exit codes.
