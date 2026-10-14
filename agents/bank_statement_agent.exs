# agents/bank_statement_agent.exs
#
# LLM-driven agent over the Bank Statement MCP server (Echo/Kong). MCP tools
# (bank_statement_list, find_bank_statement, bank_statement_to_hledger) are discovered
# at run time via tools/list; none are hardcoded here.
#
# Prerequisites: see mcp/http/bank-statement/README.md
#   export BANK_STATEMENT_MCP_ENABLED=true
#   export BANK_STATEMENT_MCP_AUTH_TOKEN=<this user's token>
#   mix aetheris run ../aetheris-agents/agents/bank_statement_agent.exs

agent_root = Path.expand(Path.join(Path.dirname(__ENV__.file), ".."))

{bank_statement, _} =
  Code.eval_file(Path.join(agent_root, "mcp/http/bank-statement/config.exs"))

if is_nil(bank_statement) do
  raise "Bank Statement MCP is not enabled. Set BANK_STATEMENT_MCP_ENABLED=true and a user credential."
end

model    = System.get_env("AETHERIS_MODEL") || "claude-haiku-4-5-20251001"
provider = System.get_env("AETHERIS_PROVIDER") || "anthropic"

%Aetheris.RunConfig{
  run_id:           "bank-statement-#{Aetheris.ID.generate()}",
  mode:             :record,
  provider:         provider,
  model:            model,
  label:            "Bank statement agent",
  sandbox_path:     agent_root,
  overlay_base_dir: nil,
  max_steps:        12,
  tools:            [],
  mcp_servers:      [bank_statement],
  system_prompt:    """
  You help the user with their bank statements using the bank_statement MCP tools
  that are available in this run. Discover what each tool does from its description
  and schema, then call the one that fits the request.

  If a tool returns an authentication or permission error, report it to the user as
  is. Do not retry with different identities, and never guess tenant or user values.
  If no bank_statement tools are available, say the Bank Statement MCP is not connected.
  """,
  user_prompt:      System.get_env("BANK_STATEMENT_PROMPT") || "List my bank statements."
}
