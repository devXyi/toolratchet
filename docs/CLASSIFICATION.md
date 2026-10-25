# Change classification

Every difference between two tool surfaces becomes a `Change` with a **level**:

| Level | Meaning |
|---|---|
| `BREAKING` | Proven (or, for a few constraints, assumed) to break callers that worked before. |
| `REVIEW` | Cannot be proven safe: behavioural, semantic, or beyond what the engine can reason about. |
| `info` | Compatible (for example, a new tool or optional parameter). |

Changes can also carry a **security** flag (`high` or `low`). A `high` flag fails the gate unless `--allow-security` is passed.

## Tools

| Kind | Level | Notes |
|---|---|---|
| `tool.removed` | BREAKING | |
| `tool.renamed` | BREAKING | Matched from remove + add when similarity is at least `--rename-threshold` (default 0.75). Field-level changes are still listed under `old -> new`. |
| `tool.added` | info | New text is scanned for integrity flags. |
| `title.changed` | info | |
| `description.changed` | REVIEW | Models choose and call tools from this text. Includes a sentence diff. |

## Input schema (callers send what the old schema allowed, so `old ⊆ new`)

| Kind | Level | Example |
|---|---|---|
| `input.required` | BREAKING | A parameter became required. |
| `input.type` | BREAKING | `string` or `integer` narrowed to `string`. |
| `input.enum` | BREAKING | An enum value was removed, or a free value became an enum. |
| `input.bound` | BREAKING | `maximum`, `minimum`, `minLength`, `maxLength`, `minItems`, `maxItems`, ... tightened or added. |
| `input.closed` | BREAKING | `additionalProperties: false` now rejects a declared property. |
| `input.pattern` | BREAKING / REVIEW | BREAKING when a pattern is added. REVIEW when it changes (regex inclusion cannot be proven). |
| `input.format` | REVIEW | Validators may or may not enforce `format`. |
| `input.unique`, `input.multiple` | BREAKING | `uniqueItems` / `multipleOf` added or changed. |
| `input.unsupported` | REVIEW | `not`, `if/then/else`, `patternProperties`, unresolved `$ref`, a union that cannot be proven covered, ... |
| `input.param-added` | info | Optional parameter. |
| `input.param-removed` | REVIEW | Still accepted by an open schema, but may be ignored. (Closed schemas report `input.closed`.) |
| `input.default-changed` | REVIEW | Callers that rely on the default now get different behaviour. |
| `input.param-description-changed` | REVIEW | Models read parameter docs. |
| `input.param-deprecated` | info | |

## Output schema (consumers rely on what the old schema promised, so `new ⊆ old`)

| Kind | Level | Example |
|---|---|---|
| `output.schema-added` | info | |
| `output.schema-removed` | BREAKING | The structured-output guarantee is gone. |
| `output.required` | BREAKING | A previously guaranteed property is no longer guaranteed. |
| `output.type`, `output.enum`, `output.bound`, `output.closed`, ... | BREAKING / REVIEW | The output may now contain something the old schema did not allow. |

## Annotations

| Kind | Level | Notes |
|---|---|---|
| `annotations.escalated` | REVIEW + security `high` | `readOnlyHint` true to false, `destructiveHint` false to true, `idempotentHint` true to false, `openWorldHint` false to true. Missing hints use the MCP defaults, so dropping `readOnlyHint: true` counts. |
| `annotations.relaxed` | info | The opposite direction. |

## Integrity flags (`security.*`)

Only text that is **new** in the change is scanned (descriptions, parameter docs, enum values, string defaults).

| Kind | Severity |
|---|---|
| `hidden-unicode` (zero-width, bidi controls, Unicode tag characters) | high |
| `instruction-override`, `concealment`, `covert-action`, `exfiltration`, `sensitive-path`, `system-tag` | high |
| `imperative-before-use`, `url`, `encoded-blob` | low |

## Semver verdict

| Required bump | When |
|---|---|
| `major` | Any BREAKING change. |
| `minor` | New tool, new optional parameter, or new output schema. |
| `patch` | Any other change. |
| `none` | No changes. |

For `0.y.z` versions, one level shifts down: a minor bump may carry breaking changes and a patch bump may carry features. With `--check-version`, breaking changes covered by a sufficient bump are reported as a note instead of a failure.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Gate passed. |
| 1 | Gate failed (breaking changes, integrity flags, budget, or version check). |
| 2 | Usage error, unreadable input, or the server could not be reached. |
