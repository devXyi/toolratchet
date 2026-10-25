# Positioning: what is unique here, and what is not

*Landscape snapshot: 24 Oct 2026, from public package pages and READMEs. Re-check before announcing; this space moves weekly.*

## The honest starting point

MCP tooling is crowded. Before writing a line of code we looked at four obvious ideas, and each already has several entrants:

| Idea | Examples found | Verdict |
|---|---|---|
| Security scanner for MCP servers | mcp-scan (Invariant Labs, now part of Snyk), mcplens, mcp-scrutiny, mcpguard, mcp-audit, Bawbel scanner | Crowded. Several already do hash pinning, SARIF and active probes. |
| Policy firewall / proxy | mcpwall, AgentWard, tool-use-firewall, McpVanguard, mcp-firewall, watchgate, agent-firewall | Crowded. Allow/deny lists, argument rules, dry-run and audit logs are table stakes. |
| Token compression | Atlassian mcp-compressor, mcp-optimizer, terse-mcp | Crowded, and terse-mcp in particular looks mature (dozens of releases). |
| Tool-schema diff / lockfile | mcp-diff, mcp-tools-diff, ToolDiff, assorted scripts | Present, but **shallow** (see below). |

A fifth scanner or firewall would be the twentieth of its kind. So this project does not compete there.

## The gap

The diff tools exist, but as far as their public documentation shows they compare schemas **structurally**: tool removed, required parameter added, type string changed. Some state their limits openly: one published script notes it does not resolve `$ref` or `oneOf` / `anyOf` / `allOf` and that rename detection is a heuristic; another reports `anyOf` changes as breaking without analysing them. None of the ones we read describes reasoning about *whether the new schema still accepts everything the old one did*.

That is the question a maintainer actually needs answered in a pull request, and it is a well-defined problem (subschema checking). `toolratchet` answers it **soundly**: it never says "compatible" unless it proved it, and it says "REVIEW" when it cannot.

## What toolratchet does differently

1. **A sound compatibility engine, not a field-by-field comparison.** Tri-state result (proven, proven-breaking, unknown). Direction-aware: inputs must widen, outputs must narrow. Handles `$ref`, `anyOf`/`oneOf`/`allOf`, enums, numeric and length bounds, `required`, closed objects, arrays, `nullable`. Unknowns surface as REVIEW.
2. **One gate for three questions.** *Does it break callers? Does it bloat context? Does it smuggle in instructions?* Most tools answer one.
3. **Vendor-neutral.** MCP (stdio and HTTP), OpenAI, Anthropic and Gemini tool definitions, and cross-format diffs. Tool definitions are the same JSON Schema problem everywhere.
4. **Renames, not remove + add.** Matched by schema, description and name similarity.
5. **Semver that means something.** Computes the required bump, compares it with the declared one, and lets a sufficient major bump *acknowledge* breaking changes.
6. **A token ratchet.** Per-tool and total token cost with a "may never grow" mode, shown in every report.
7. **Supply-chain review.** `diff "cmd:server@1.2" "cmd:server@1.3"` answers "what changed in the tool surface when I upgraded?", including newly added hidden text and permission escalations (`readOnlyHint` flipped).
8. **CI-native.** Job summary, GitHub annotations, PR-comment markdown with a sticky marker, stable exit codes.
9. **Zero dependencies, deterministic, offline.** No LLM calls, no telemetry, never invokes a tool.

## What it deliberately does not do

- **No runtime enforcement.** Use a firewall or proxy for that. toolratchet tells you *before* the change ships.
- **No deep semantic analysis of descriptions.** The integrity pass is regex-and-Unicode heuristics on *new* text. A dedicated scanner will catch more subtle poisoning.
- **No behaviour diffing.** A schema-identical server that does something different is invisible to any schema diff.
- **No guarantees about regex inclusion.** Pattern changes are REVIEW.

## How to talk about it

> `oasdiff` / `buf breaking` for LLM tool definitions, plus a token budget and a poisoning check.

Headline demo: two JSON files, one command, a red report in a pull request.

## Roadmap

| Version | Item |
|---|---|
| 0.2 | SARIF output; ready-made PR-comment action; `.toolratchet.toml` for per-tool overrides and allowed changes. |
| 0.3 | `--base-ref` (build and snapshot the base branch automatically); OpenAPI / FastMCP / `@tool` decorators as sources; resources and prompts surfaces. |
| 0.4 | Optional watch mode for scheduled drift checks of third-party servers; baseline of accepted changes with expiry. |

## Success criteria

- A maintainer can adopt it in under five minutes (`lock`, `check`, one CI step).
- Zero false "compatible" verdicts, enforced by the randomized soundness test.
- Reports read well on a phone, in a PR comment, and in a job summary.
