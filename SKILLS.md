# SKILLS.md — aetheris-agents (use-case agents)

End-to-end map of the agentic system as it is **used** in this repo, plus the
procedure for adding or removing a feature (a use case, an agent, an MCP tool, a
sub-agent). This is a navigation + extension spec, not a replacement for
`CLAUDE.md` (authoritative conventions/gates) or `docs/agent-creation-guide.md`
(the detailed how-to this file summarises and points into) — those remain
canonical.

Companion file: [`../aetheris/SKILLS.md`](../aetheris/SKILLS.md) — the harness
side of the same system (what actually executes a run).

---

## 1. What this repo is

A collection of **use-case agent implementations** built on top of the Aetheris
harness (sibling repo `../aetheris/`). No Elixir *source* lives here — only
`.exs` **agent files** that evaluate to a `RunConfig` (solo agent) or `OrbConfig`
(multi-agent orb), which the harness loads and executes. Everything else
(computation, file generation, API calls) is deterministic Python under each use
case's `scripts/`.

**Core principle: scripts do, agents decide.** The LLM reads context and decides
*what* to do (which tool, which employee, which branch); a Python script does the
actual parsing, arithmetic, file writes, and API calls. Never ask the LLM to
construct file content or compute values itself.

## 2. End-to-end flow (one agent run)

```
User/operator intent
  → pick or write an agent file: {use_case}/agents/{name}.exs
      (RunConfig for a single agent, OrbConfig for a multi-agent pipeline)
  → cd ../aetheris && mix aetheris run ../aetheris-agents/{use_case}/agents/{name}.exs
  → harness loads the file, starts the run (see ../aetheris/SKILLS.md §2)
  → LLM step loop:
      - reads system/user prompt + available tools (native `run_command` +
        any discovered MCP tool schemas)
      - calls `run_command` against a script in {use_case}/scripts/*.py
        (stdout = JSON contract; errors to stderr; exit 0/1)
      - or calls a discovered MCP tool (see §6)
      - orchestrates sub-agents via `send_message`/blackboard if it's an orb
  → script(s) do deterministic work, write to {use_case}/output/ (gitignored)
  → LLM synthesises/report back to the operator
  → trajectory recorded by the harness; inspect with
    `mix aetheris inspect <run_id>` / `mix aetheris tree show <run_id>`
```

Integration/regression coverage for the whole chain runs via
`../aetheris/scripts/sprint.sh <case>` (a separate commit from agent-file
changes — "two repos, two commits").

## 3. Repository layout (per use case)

```
{use_case}/
  agents/     .exs files — RunConfig or OrbConfig structs (the ONLY Elixir here)
  scripts/    Python — deterministic logic, one responsibility per file
  tests/      pytest — unit + integration; conftest.py per directory
  data/       input files; .gitignore excludes real data
  docs/       t*-implementation-notes.md, written after each ticket
  output/     gitignored; .gitkeep committed
```
Current use cases (checked registry: `docs/use-cases.md`): `payslip`, `drive`,
`email`, `api/tenant`, `api/gateway`, `boxy-pipeline` (dormant), `cloudcost`,
`docbuilder`, `eduloka`, `provenance`. Read `docs/use-cases.md`, not this list,
for current status — it is the drift-checked source of truth.

## 4. Component map (where to look)

| Concern | Path |
|---|---|
| Use-case registry (source of truth) | `docs/use-cases.md` |
| How to build a new agent, step by step | `docs/agent-creation-guide.md` |
| TAP multi-agent protocol design | `docs/uc-api-agent-design.md`, `protocol/TAP-v0-design.md` |
| Global root agents (cross-use-case) | `agents/*.exs` (e.g. `orchestrator.exs`, `capability_matrix_cloudcost.exs`) |
| MCP server registrations | `mcp/http/<name>/config.exs` (env-gated, one per external MCP server) |
| Python shared plumbing pattern | `{use_case}/scripts/_helper.py` (lazy heavy imports) |
| Sprint / integration harness entries | `../aetheris/scripts/sprint.sh` |
| Drift checker (docs ↔ code sync) | `scripts/drift_check.py` |
| Project-knowledge export (Claude context) | `docs/project-knowledge-manifest.md`, `scripts/repin_manifest.py`, `scripts/assemble_export_bundle.py` |
| Conventions, env vars, gates | `CLAUDE.md` |
| Model/provider defaults | `.env.example` (`AETHERIS_MODEL`, `AETHERIS_PROVIDER`, per-use-case `*_MODEL`) |

## 5. Agent file conventions (the non-negotiables)

```elixir
# ALWAYS __ENV__.file for sandbox_path — never File.cwd!()
agent_root = Path.expand(Path.join(Path.dirname(__ENV__.file), ".."))

%Aetheris.RunConfig{
  run_id:            "{use_case}-orch-#{Aetheris.ID.generate()}",
  mode:              :record,
  provider:          System.get_env("AETHERIS_PROVIDER") || "anthropic",
  model:             System.get_env("AETHERIS_MODEL") || "claude-haiku-4-5-20251001",
  sandbox_path:      agent_root,
  overlay_base_dir:  nil,                 # nil when output must persist
  max_steps:         20,
  max_spawn_depth:   2,
  context_strategy:  :full,               # or :rolling + max_context_steps for long runs
  tools:             [...],
  mcp_servers:       mcp_servers,         # from mcp/http/<name>/config.exs, filtered non-nil
  system_prompt:     "...",
  user_prompt:       "..."
}
```
- `:rolling` truncates old messages and can orphan `tool_use_id` references → HTTP
  400. Use `:full` for anything under ~10–20 steps.
- `System.get_env` calls go into a named variable above the struct literal, never
  inlined inside it.
- Orbs (`OrbConfig`) pre-establish run IDs so `send_message` targets are known at
  prompt-write time, and coordinate via `write_blackboard` / `send_message` /
  `wait_for_event` (see `docs/agent-creation-guide.md` for the exact pattern).

## 6. MCP tool integration (the current worked example)

The **Bank Statement** use case is the template for HTTP MCP integration:

```
agents/bank_statement_agent.exs
  → Code.eval_file("mcp/http/bank-statement/config.exs")
  → config.exs: env-gated (BANK_STATEMENT_MCP_ENABLED=true), validates the URL
    (https, or loopback-only http), forwards BANK_STATEMENT_MCP_HEADER_* env
    vars as HTTP headers (name lower-cased, underscores→hyphens), sets timeout
  → RunConfig.mcp_servers: [%{server_id:, transport: :http, url:, headers:, timeout_ms:}]
  → RunConfig.tools: []   — the agent's OWN tool list is empty; it relies
    entirely on MCP-discovered schemas, never hand-authored tool names
  → at run start the harness performs tools/list discovery against the MCP
    server and exposes exactly what it returns to the LLM
```
Currently exposed tools on that server: `list_openapi_specs`,
`bank_statement_list`, `find_bank_statement`, `bank_statement_to_hledger`. The
MCP server itself additionally requires a base64-encoded `X-User-Context` header
per call (tenant/user/role/permissions) — **this is not yet wired into
`config.exs`'s header-forwarding mechanism**; today it only works when supplied
manually (a direct `tools/call`), not through the LLM-driven agent run. This is
the standing gap to close if the agent path needs to call authenticated MCP
tools (see §9b for the fix shape).

Direct/manual tool call, bypassing the LLM entirely (useful for debugging or for
non-agentic automation):
```bash
curl -s <mcp-url> -X POST -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-User-Context: $(base64 -w0 <<< '{"tenant_id":"...","user_id":"...","role":"...","permissions":[...],"auth_type":"user"}')" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"<tool>","arguments":{...}}}'
```

## 7. Python script conventions

- One responsibility per script; compute → JSON, generate → files; never mixed.
- stdout is the contract (agents parse it); errors to stderr; exit 0 success/
  recoverable-analysis, 1 recoverable error.
- Generation scripts take `--output FILE` / `--spec FILE` rather than relying on
  the LLM to round-trip large stdout through `write_file`.
- Generation scripts also take `--output-dir` (default `output`) so tests can
  redirect to `tmp_path`.
- Stage CLIs degrade, never crash: malformed/empty/null input → a
  `{"status":"partial"}` envelope + exit 1, never an uncaught exception.
- `conftest.py` inserts `scripts/` onto `sys.path`; no `__init__.py` where the
  use-case name collides with a stdlib package.

## 8. Testing / gates

```bash
# whole non-deferred suite
python3 -m pytest -q -m "not integration and not dormant"
# deferred sets, each with its own meaning (see CLAUDE.md for the full contract)
python3 -m pytest -q -m "integration and not dormant"
python3 -m pytest -q -m dormant
# one use case
python3 -m pytest {use_case}/tests/ -v
```
`integration`: outcome depends on state the repo doesn't carry (external tools,
live servers, a sibling repo). `dormant`: the use case's *work* is paused; tests
still collect, just deselected. Neither marker is for "slow" or "currently red" —
a red test stays red and gets a tracked ticket, never silently marked.

---

## 9. Adding a feature — by kind

### 9a. Add a brand-new use case
1. Scaffold the layout in §3 under `{use_case}/`.
2. Write `scripts/` first (pure Python, pytested standalone) — this is where
   almost all the real logic should live.
3. Write the agent `.exs` per §5; keep `tools` to the minimum native set plus
   any MCP servers it needs.
4. Add `conftest.py`, tests, `.gitignore` for `data/`/`output/`.
5. Add the use case to `docs/use-cases.md` (the checked registry — this is what
   `drift_check.py`'s `use_case_registry` check validates, not any prose list).
6. Add a sprint case in `../aetheris/scripts/sprint.sh` before `# Summary`.
7. Write `{use_case}/docs/t1-implementation-notes.md` after the first ticket.

### 9b. Wire a new external MCP server
1. Add `mcp/http/<name>/config.exs` following the Bank Statement template in §6:
   env-gated enable flag, URL scheme/host validation (https, or loopback http),
   `<NAME>_MCP_HEADER_*` → HTTP header forwarding, timeout parsing.
2. **If the server requires a non-trivial auth payload** (like the Bank
   Statement server's base64 `X-User-Context`), add a dedicated env var +
   encode/validate step in `config.exs` rather than relying on the generic
   `_HEADER_*` passthrough to get the encoding right — the generic passthrough
   forwards raw values, and a base64-encoded JSON blob needs its own
   construction step (this is the exact gap currently open on
   `bank-statement/config.exs`; fixing it is the template for any future
   MCP server with structured auth).
3. In the agent `.exs`, `Code.eval_file` the config, filter nils into
   `mcp_servers`, and leave `tools: []` unless the agent also needs native
   tools — never hand-author MCP tool names/descriptions in the prompt; let
   discovery supply them.
4. Add the opt-in integration tests following `tests/test_bank_statement_mcp.py`
   — config evaluation tests run unconditionally; live-server tests gate on an
   explicit `*_TEST_LIVE=true` env var and are marked `integration`.

### 9c. Add a native tool to an agent
1. Confirm the tool exists in the harness (`../aetheris/lib/aetheris/execution/tool/`)
   — add it there first if not (see `../aetheris/SKILLS.md §9c`).
2. Add it to the agent's `tools:` list only if the agent genuinely calls it;
   remove unused tools from the list (smaller tool surface = less room for the
   LLM to improvise, per the `m2b-docbuilder` learning in `CLAUDE.md`).

### 9d. Add a sub-agent to a multi-agent orb
1. Allocate its run ID alongside the orb's in the `OrbConfig` file (IDs must be
   known at prompt-write time for `send_message` targets).
2. Give it its own `agents/<name>.exs` (or inline struct if trivial) with its own
   `system_prompt`/`tools`.
3. Wire the blackboard key convention (`tap:intent:{id}` style) and the
   send/wait signalling steps per `docs/agent-creation-guide.md`.
4. Give each parallel sub-agent its own output directory
   (`data/raw/{slug}/...`) — never a shared flat directory; parallel writers
   silently clobber each other otherwise.

### 9e. Add a new computation/generation script
1. One script, one responsibility; stdout-JSON or `--output FILE`, per §7.
2. Add `--input FILE` if it needs to read JSON too (there is no stdin piping
   from `run_command`).
3. Unit test it standalone before wiring it into any agent prompt.

## 10. Removing a feature

1. **Grep both repos** before deleting anything — an agent `.exs` here can
   reference a harness tool/CLI surface with no compile-time check tying them
   together, and `../aetheris/scripts/sprint.sh` can reference a script/agent
   path here directly.
2. Remove the use case from `docs/use-cases.md` (or mark `dormant` with a date
   and a stated return condition — removing is not the same as pausing; see
   `CLAUDE.md §Definition of done` for the `dormant` marker's full contract).
3. Delete `{use_case}/agents/`, `scripts/`, `tests/`; keep `docs/*-implementation-notes.md`
   as historical record (do not delete implementation notes — they are the
   record of *why*, not current state).
4. Remove its sprint case from `../aetheris/scripts/sprint.sh` in the **same
   commit** that removes the use case here, or the sprint run breaks on a
   missing path with no local signal (two repos, two commits — but still one
   coordinated change).
5. Run `python3 scripts/drift_check.py --strict` — the `use_case_registry`
   check will fail loudly if the registry and the directory disagree.
6. Run the full non-deferred pytest gate to confirm nothing else imported the
   removed scripts.

## 11. Read this before touching anything agentic here

- `CLAUDE.md` — conventions, env vars, gates, and the full learning history;
  authoritative over this file wherever they overlap.
- `docs/agent-creation-guide.md` — the detailed how-to this file summarises.
- `docs/use-cases.md` — the checked registry of what exists and its status.
- `../aetheris/SKILLS.md` — what actually executes an agent run.
