# Bank Statement MCP (via Echo/Kong)

HTTP MCP server at `https://echo.trialrun.net/mcp/`. Tools are discovered at run time
(`tools/list`): `bank_statement_list`, `find_bank_statement`, `bank_statement_to_hledger`.
Agents never hardcode tool logic.

## Request flow

```
User request → Aetheris agent run → HTTP MCP client (tools/list, tools/call)
  → Echo/Kong authenticates the user credential, generates trusted X-User-Context
  → Echo MCP authorization (bank_statement.read) → Bank Statement backend (tenant-scoped)
  → tool result → agent → user response
```

Full Kong edge flow with sequence diagrams: [echo-user-context.md](echo-user-context.md).

Aetheris only supplies a credential header. Echo/Kong owns authentication, RBAC and
`X-User-Context`. Aetheris never creates, decodes or forwards it; tenant IDs, roles and
permissions are never set here.

## Configuration (`config.exs`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `BANK_STATEMENT_MCP_ENABLED` | Yes | `true` to enable; otherwise the server is omitted |
| `BANK_STATEMENT_MCP_AUTH_TOKEN` | One of | User token, sent as `Authorization: Bearer …` |
| `BANK_STATEMENT_MCP_API_KEY` | One of | User API key, sent as `X-API-Key` (confirm Echo accepts it) |
| `BANK_STATEMENT_MCP_URL` | No | Default `https://echo.trialrun.net/mcp/`; non-local hosts must be https |
| `BANK_STATEMENT_MCP_TIMEOUT_MS` | No | Default `30000` |

Fail-closed: when enabled, a missing or duplicate credential, a bad URL, or any
`BANK_STATEMENT_MCP_HEADER_*` variable (old static passthrough, removed) raises before the run starts.

## Credential scope

The credential comes from the environment of the single `mix aetheris run` process for
that user's run. It becomes the connect-time headers of that run's `type: :http` server
entry, which the harness applies to both `tools/list` and `tools/call` (stateless HTTP,
no session). Runs are separate OS processes, so credentials cannot mix. Do not export a
user token in a shared shell, service-wide env or Rig Settings: the launcher must set it
per run. Never put it in prompts or tool arguments.

**Harness limitation:** a server-side service starting runs for many users (e.g. the playground
API) cannot do this yet, because `mcp_servers` is a forbidden client field and `RunConfig` has no
per-run context. See `../aetheris/docs/aetheris/user-context-propagation-contract.md`. Until the
harness supports it, launch one process per user run.

## Verify

Local (no network): `python3 -m pytest -q tests/test_bank_statement_mcp.py` — covers config,
discovery, all three tools against a mock Echo/Kong, 401/403 behaviour, and tenant isolation.

Deployed (run from `../aetheris`):

```bash
export BANK_STATEMENT_MCP_ENABLED=true BANK_STATEMENT_MCP_AUTH_TOKEN=<user token>
curl -s https://echo.trialrun.net/mcp/ -H "Authorization: Bearer $BANK_STATEMENT_MCP_AUTH_TOKEN" \
  -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
mix aetheris run ../aetheris-agents/agents/bank_statement_agent.exs
```

Expect: no/invalid token → 401; user without `bank_statement.read` → tools/call denied;
two users with different tenants see only their own statements.
