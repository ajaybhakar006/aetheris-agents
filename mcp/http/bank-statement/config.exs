# mcp/http/bank-statement/config.exs
#
# Builds the Aetheris `mcp_servers` entry for the Bank Statement MCP server, which is
# reached through Echo/Kong at https://echo.trialrun.net/mcp/.
#
# Usage from an agent file:
#   bank_statement = Code.eval_file(Path.join(mcp_dir, "http/bank-statement/config.exs")) |> elem(0)
#   mcp_servers    = [bank_statement] |> Enum.reject(&is_nil/1)
#
# Returns nil when BANK_STATEMENT_MCP_ENABLED != "true". When enabled it either returns
# a complete `type: :http` config or raises (fail closed: never run unauthenticated).
#
# Trust model:
#   * The run's user credential is read ONCE, from this run's own process environment,
#     and becomes the connect-time headers of this run's server entry. One run = one OS
#     process = one credential; nothing is cached on disk or shared between runs.
#   * Echo/Kong authenticates the credential and generates the trusted X-User-Context.
#     Aetheris never builds, decodes or forwards X-User-Context; any attempt to supply it
#     (env, extra headers) is rejected.
#   * The same header map is applied by the harness to `tools/list` and `tools/call`.
#   * The credential is never placed in prompts, tool schemas or tool arguments.

env = fn name ->
  case System.get_env(name) do
    nil -> nil
    value -> if String.trim(value) == "", do: nil, else: String.trim(value)
  end
end

default_url = "https://echo.trialrun.net/mcp/"
forbidden = ["x-user-context"]

if System.get_env("BANK_STATEMENT_MCP_ENABLED") != "true" do
  nil
else
  url = env.("BANK_STATEMENT_MCP_URL") || default_url

  uri = URI.parse(url)

  if uri.scheme not in ["https", "http"] or is_nil(uri.host) do
    raise "BANK_STATEMENT_MCP_URL must be an absolute http(s) URL, got: #{inspect(url)}"
  end

  if uri.scheme == "http" and uri.host not in ["localhost", "127.0.0.1", "::1"] do
    raise "BANK_STATEMENT_MCP_URL must use https for non-local hosts (credentials are sent in headers)"
  end

  for name <- Map.keys(System.get_env()),
      String.starts_with?(name, "BANK_STATEMENT_MCP_HEADER_") do
    raise "#{name} is not supported: static header passthrough was removed. " <>
            "Use BANK_STATEMENT_MCP_AUTH_TOKEN or BANK_STATEMENT_MCP_API_KEY."
  end

  token = env.("BANK_STATEMENT_MCP_AUTH_TOKEN")
  api_key = env.("BANK_STATEMENT_MCP_API_KEY")

  if token && api_key do
    raise "Set only one of BANK_STATEMENT_MCP_AUTH_TOKEN or BANK_STATEMENT_MCP_API_KEY"
  end

  credential_headers =
    cond do
      token ->
        token = String.replace(token, ~r/^Bearer\s+/i, "")
        %{"Authorization" => "Bearer " <> token}

      api_key ->
        %{"X-API-Key" => api_key}

      true ->
        raise "Bank Statement MCP is enabled but no user credential is set " <>
                "(BANK_STATEMENT_MCP_AUTH_TOKEN or BANK_STATEMENT_MCP_API_KEY)"
    end

  for {name, value} <- credential_headers do
    if String.downcase(name) in forbidden or String.match?(value, ~r/[\r\n]/) do
      raise "Invalid credential header"
    end
  end

  timeout_ms =
    case env.("BANK_STATEMENT_MCP_TIMEOUT_MS") do
      nil ->
        30_000

      raw ->
        case Integer.parse(raw) do
          {n, ""} when n > 0 -> n
          _ -> raise "BANK_STATEMENT_MCP_TIMEOUT_MS must be a positive integer"
        end
    end

  %{
    server_id: "bank_statement",
    type: :http,
    url: url,
    headers: credential_headers,
    timeout_ms: timeout_ms
  }
end
