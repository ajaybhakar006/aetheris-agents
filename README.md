# aetheris-agents

Use-case agent implementations built on the [Aetheris](../aetheris) harness.
Each use case is a self-contained directory with Python scripts, Elixir agent
files, tests, and docs.

No Elixir source lives here — only `.exs` agent scripts that the harness
evaluates. All sprint and agent commands run from `../aetheris/`.

---

## How context reaches Claude

Project knowledge holds a small kernel. The rest of the corpus stays in git and is
fetched on demand through the GitHub connector.

- [`docs/project-knowledge-manifest.md`](docs/project-knowledge-manifest.md) — the
  surface column says where each document lives; the connector notice says how to
  reach the fetched ones.
- [`../aetheris/docs/aetheris/research/hybrid-context-design-2026-08.md`](../aetheris/docs/aetheris/research/hybrid-context-design-2026-08.md)
  — the design and its measurements, in the harness repo.
- [`prompts/bl-002-refresh-project-knowledge.md`](prompts/bl-002-refresh-project-knowledge.md)
  — run this to refresh the store.

---

## Use cases

**`docs/use-cases.md` is the registry** — the committed declaration of what the use cases are
and which are dormant. This table's first column is checked against it by `drift_check.py`'s
`use_case_registry` check; the Status column below is a reader's summary of progress, not the
status the registry declares.

| Directory | What it does | Status |
|-----------|-------------|--------|
| [`payslip/`](payslip/) | Generate monthly payslips from payroll CSV; parallel sub-agents per employee | ✅ |
| [`drive/`](drive/) | Download payroll CSV from Google Drive; upload generated PDFs back | ✅ |
| [`email/`](email/) | Email each employee their payslip PDF | ✅ |
| [`api/tenant/`](api/tenant/) | TAP protocol, tenant side — agent-to-agent enrollment via ct.stu API | ✅ T1–T4 |
| [`api/gateway/`](api/gateway/) | TAP protocol, gateway side — intent execution against ct.stu | ✅ T1–T4 |
| [`boxy-pipeline/`](boxy-pipeline/) | Catalog and sales-order extraction; pure Python, no Aetheris agent | ⏸ dormant since 2026-08-16 |
| [`cloudcost/`](cloudcost/) | Multi-provider cloud spend collection and reporting | ✅ |
| [`docbuilder/`](docbuilder/) | Branded document generation — invoices, offer letters (PDF + DOCX) | ✅ |
| [`eduloka/`](eduloka/) | Discovery milestone | ✅ |
| [`provenance/`](provenance/) | Corpus classification, migration and search over client documents | ✅ |

### Payslip pipeline

```
drive (download)  →  payslip (generate)  →  drive (upload)  →  email (deliver)
```

### TAP protocol track

```
TAP v0 design  →  uc-api-agent T1-T4  →  T5 durable state (planned)
```

---

## Getting started

```bash
# Install Python deps (mise-managed Python 3.12)
python3 -m pip install pytest boto3 pika

# Run tests for a use case
python3 -m pytest api/tenant/tests/ api/gateway/tests/ -v

# Run an agent (from the aetheris sibling repo)
cd ../aetheris
mix aetheris run ../aetheris-agents/api/tenant/agents/at1cmd.exs
```

---

## Bank statement agent — local setup and end-to-end run

An LLM agent (`agents/bank_statement_agent.exs`) that calls the Bank Statement MCP server
through Echo/Kong. Tools (`bank_statement_list`, `find_bank_statement`,
`bank_statement_to_hledger`) are discovered at run time. Details:
[`mcp/http/bank-statement/README.md`](mcp/http/bank-statement/README.md).

### 1. Prerequisites

- [mise](https://mise.jdx.dev/) with the sibling harness checked out at `../aetheris`
  (`~/sandbox/elixirws/aetheris`). The harness pins Elixir 1.17.2 / OTP 27 / Node 22 in its
  `.mise.toml`; a system Elixir older than 1.17 fails with
  *"supports only Elixir ~> 1.17"*, so run harness commands through `mise exec --`.
- Python 3.12 (`mise.toml` here) with `pytest` for the local tests.
- An LLM provider key and a **per-user** Echo credential (token or API key).

### 2. Install

```bash
# this repo
python3 -m pip install pytest

# harness (once)
cd ../aetheris
mise install
mise exec -- sh -c 'mix deps.get && mix compile'
```

### 3. Configure (environment of the run process)

```bash
export BANK_STATEMENT_MCP_ENABLED=true
export BANK_STATEMENT_MCP_AUTH_TOKEN=<user token>     # or BANK_STATEMENT_MCP_API_KEY=<key> (set exactly one)
export AETHERIS_PROVIDER=openrouter                   # default: anthropic
export AETHERIS_MODEL=<model id>                      # default: claude-haiku-4-5-20251001
export OPENROUTER_API_KEY=<key>                       # or ANTHROPIC_API_KEY for the anthropic provider
export BANK_STATEMENT_PROMPT="List my bank statements."   # optional
```

Optional: `BANK_STATEMENT_MCP_URL` (default `https://echo.trialrun.net/mcp/`),
`BANK_STATEMENT_MCP_TIMEOUT_MS` (default `30000`). See
[`.env.example`](mcp/http/bank-statement/.env.example). Never commit credentials; set them
per run, not in a shared shell or service-wide env.

### 4. Verify, then run

```bash
# a. Offline: config, discovery, all three tools, 401/403, tenant isolation (mock Echo/Kong)
cd ~/sandbox/elixirws/aetheris-agents
python3 -m pytest -q tests/test_bank_statement_mcp.py      # 19 passed

# b. Live reachability: expect HTTP 200 and the three tools
curl -s https://echo.trialrun.net/mcp/ -H "Authorization: Bearer $BANK_STATEMENT_MCP_AUTH_TOKEN" \
  -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# (for an API key use:  -H "X-API-Key: $BANK_STATEMENT_MCP_API_KEY")

# c. Run the agent end to end (from the harness repo)
cd ../aetheris
mise exec -- mix aetheris run ../aetheris-agents/agents/bank_statement_agent.exs
```

Success prints `%{label: "Bank statement agent", status: :done, run_id: "bank-statement-…"}`.
The `[sandbox] namespace entry failed` / `cgroup delegation unavailable` lines are
non-fatal on hosts without user namespaces or cgroup delegation.

### 5. Inspect the run

```bash
mise exec -- mix aetheris inspect <run_id>     # prompt → llm → tool_called (mcp) → tool_result → run_complete
mise exec -- mix aetheris list --limit 20
```

A healthy run shows a `tool_called` event with `server_id: "bank_statement"`, `source: "mcp"`,
followed by a `tool_result`, then `run_complete` (`reason: agent_finished`).

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `supports only Elixir ~> 1.17` | Not using the harness toolchain; use `mise exec --` |
| `Bank Statement MCP is not enabled` | `BANK_STATEMENT_MCP_ENABLED` is not `true` |
| `no user credential is set` / `Set only one of …` | Export exactly one of token or API key |
| 401 from Echo | Missing/invalid credential |
| Tool denied (403) | User lacks `bank_statement.read`; report it, do not retry other identities |

---

## Repository conventions

See [`CLAUDE.md`](CLAUDE.md) for the full reference. Key points:

**Scripts do; agents decide.** Python handles computation, file I/O, and API
calls. Elixir agent files contain only the `RunConfig` or `OrbConfig` struct.

**`__ENV__.file` for sandbox_path.** Always resolve paths relative to the
agent file, not `File.cwd!()`.

**`context_strategy: :full` for short-lived agents.** `:rolling` truncates old
messages and can leave orphaned `tool_use_id` references, causing HTTP 400.
Use `:full` for any agent that runs fewer than ~20 steps.

**Two repos, two commits.** Sprint cases in `../aetheris/scripts/sprint.sh` are
a separate commit from the agent files here. Do not merge without review.

---

## Docs

| Document | Contents |
|----------|---------|
| [`CLAUDE.md`](CLAUDE.md) | Conventions, commands, critical patterns |
| [`docs/agent-creation-guide.md`](docs/agent-creation-guide.md) | How to build a new use case |
| [`docs/uc-api-agent-design.md`](docs/uc-api-agent-design.md) | TAP protocol architecture |
| [`protocol/TAP-v0-design.md`](protocol/TAP-v0-design.md) | TAP v0 spec |
| [`ROADMAP.md`](ROADMAP.md) | What's next |
