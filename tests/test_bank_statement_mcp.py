"""Bank Statement MCP integration: config, request-scoped auth, and a mock Echo/Kong flow.

The mock gateway stands in for Echo/Kong + the Bank Statement backend: it is the only
component that derives X-User-Context, from the credential. Aetheris-side code
(mcp/http/bank-statement/config.exs) must only supply credentials.
"""
import base64
import json
import os
import shutil
import subprocess
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "mcp/http/bank-statement/config.exs"
AGENT = ROOT / "agents/bank_statement_agent.exs"
REGISTRY = ROOT / "mcp/mcp_servers.json"

pytestmark = pytest.mark.skipif(shutil.which("elixir") is None, reason="elixir not installed")

TOOLS = ["bank_statement_list", "find_bank_statement", "bank_statement_to_hledger"]

USERS = {  # credential -> trusted identity (owned by the gateway, not Aetheris)
    "tok-alice": {"tenant_id": "t1", "user_id": "alice", "permissions": ["bank_statement.read"]},
    "tok-bob": {"tenant_id": "t2", "user_id": "bob", "permissions": ["bank_statement.read"]},
    "tok-noperm": {"tenant_id": "t1", "user_id": "eve", "permissions": []},
}
DATA = {"t1": ["t1-jan.pdf"], "t2": ["t2-feb.pdf"]}


class Gateway(BaseHTTPRequestHandler):
    log = []

    def log_message(self, *a):
        pass

    def _reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cred = (self.headers.get("Authorization") or "").removeprefix("Bearer ") or self.headers.get("X-API-Key")
        user = USERS.get(cred)
        # Echo/Kong drops any client-supplied copy and sets its own.
        if user is None:
            return self._reply(401, {"error": "unauthenticated"})
        ctx = base64.b64encode(json.dumps(user).encode()).decode()
        Gateway.log.append((req["method"], user["user_id"], self.headers.get("X-User-Context")))
        if req["method"] == "tools/list":
            return self._reply(200, {"jsonrpc": "2.0", "id": req["id"],
                                     "result": {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in TOOLS]}})
        if "bank_statement.read" not in json.loads(base64.b64decode(ctx))["permissions"]:
            return self._reply(403, {"error": "forbidden"})
        name = req["params"]["name"]
        files = DATA[user["tenant_id"]]
        out = {"bank_statement_list": files,
               "find_bank_statement": [f for f in files if req["params"]["arguments"].get("q", "") in f],
               "bank_statement_to_hledger": ["2026-01-01 " + f for f in files]}[name]
        self._reply(200, {"jsonrpc": "2.0", "id": req["id"], "result": {"content": out, "tenant": user["tenant_id"]}})


@pytest.fixture(scope="module")
def gateway():
    srv = HTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp/"
    srv.shutdown()


def eval_config(env):
    """Evaluate config.exs in a fresh process with exactly `env` (one process = one run)."""
    # Jason is a harness dep, so use a tiny encoder to keep the test dependency-free.
    code = (
        'r = Code.eval_file("%s") |> elem(0)\n'
        'enc = fn nil -> "null"; m -> '
        '"{\\"server_id\\":\\"#{m.server_id}\\",\\"type\\":\\"#{m.type}\\",\\"url\\":\\"#{m.url}\\",'
        '\\"timeout_ms\\":#{m.timeout_ms},\\"headers\\":{" <> '
        'Enum.map_join(m.headers, ",", fn {k, v} -> "\\"#{k}\\":\\"#{v}\\"" end) <> "}}" end\n'
        'IO.puts(enc.(r))' % CONFIG
    )
    p = subprocess.run(["elixir", "-e", code], env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env},
                       capture_output=True, text=True, timeout=60)
    return p


def config(env):
    p = eval_config(env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def rpc(url, headers, method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def server_for(token, url):
    return config({"BANK_STATEMENT_MCP_ENABLED": "true", "BANK_STATEMENT_MCP_AUTH_TOKEN": token, "BANK_STATEMENT_MCP_URL": url})


# --- configuration -----------------------------------------------------------

def test_disabled_returns_nil():
    assert config({}) is None


def test_default_endpoint_and_shape():
    c = config({"BANK_STATEMENT_MCP_ENABLED": "true", "BANK_STATEMENT_MCP_AUTH_TOKEN": "tok-alice"})
    assert c == {"server_id": "bank_statement", "type": "http", "url": "https://echo.trialrun.net/mcp/",
                 "timeout_ms": 30000, "headers": {"Authorization": "Bearer tok-alice"}}


def test_api_key_credential_and_bearer_prefix_normalised():
    c = config({"BANK_STATEMENT_MCP_ENABLED": "true", "BANK_STATEMENT_MCP_API_KEY": "k1"})
    assert c["headers"] == {"X-API-Key": "k1"}
    c = config({"BANK_STATEMENT_MCP_ENABLED": "true", "BANK_STATEMENT_MCP_AUTH_TOKEN": "Bearer abc"})
    assert c["headers"] == {"Authorization": "Bearer abc"}


@pytest.mark.parametrize("env,msg", [
    ({}, "no user credential"),
    ({"BANK_STATEMENT_MCP_AUTH_TOKEN": "  "}, "no user credential"),
    ({"BANK_STATEMENT_MCP_AUTH_TOKEN": "a", "BANK_STATEMENT_MCP_API_KEY": "b"}, "only one"),
    ({"BANK_STATEMENT_MCP_AUTH_TOKEN": "a", "BANK_STATEMENT_MCP_HEADER_X_USER_CONTEXT": "forged"}, "not supported"),
    ({"BANK_STATEMENT_MCP_AUTH_TOKEN": "a", "BANK_STATEMENT_MCP_URL": "http://evil.example/mcp"}, "https"),
    ({"BANK_STATEMENT_MCP_AUTH_TOKEN": "a", "BANK_STATEMENT_MCP_URL": "nonsense"}, "absolute"),
    ({"BANK_STATEMENT_MCP_AUTH_TOKEN": "a", "BANK_STATEMENT_MCP_TIMEOUT_MS": "0"}, "positive integer"),
])
def test_enabled_fails_closed(env, msg):
    p = eval_config({"BANK_STATEMENT_MCP_ENABLED": "true", **env})
    assert p.returncode != 0 and msg in p.stderr


def test_no_hardcoded_context_or_tools():
    def code(path):
        return "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))

    for f in (CONFIG, AGENT):
        text = code(f)
        assert "tenant_id" not in text and "permissions" not in text
        assert not any(t in text for t in TOOLS)
    assert "X-User-Context" not in code(AGENT)


def test_registry_entry():
    servers = {s["id"]: s for s in json.loads(REGISTRY.read_text())["servers"]}
    assert servers["bank-statement"]["url"] == "https://echo.trialrun.net/mcp/"
    assert {"google-drive", "github"} <= set(servers)  # existing integrations untouched
    assert servers["github"]["transport"] == "stdio"


# --- end-to-end against the mock Echo/Kong ----------------------------------

def test_discovery_and_all_tools_authenticated(gateway):
    c = server_for("tok-alice", gateway)
    status, body = rpc(c["url"], c["headers"], "tools/list")
    assert status == 200 and [t["name"] for t in body["result"]["tools"]] == TOOLS
    for name in TOOLS:
        status, body = rpc(c["url"], c["headers"], "tools/call", {"name": name, "arguments": {"q": "jan"}})
        assert status == 200 and body["result"]["tenant"] == "t1"


def test_context_comes_from_gateway_not_client(gateway):
    Gateway.log.clear()
    c = server_for("tok-alice", gateway)
    assert not any(k.lower() == "x-user-context" for k in c["headers"])
    rpc(c["url"], c["headers"], "tools/list")
    assert Gateway.log == [("tools/list", "alice", None)]


@pytest.mark.parametrize("headers,code", [({}, 401), ({"Authorization": "Bearer bogus"}, 401)])
def test_auth_failures_for_list_and_call(gateway, headers, code):
    assert rpc(gateway, headers, "tools/list")[0] == code
    assert rpc(gateway, headers, "tools/call", {"name": TOOLS[0], "arguments": {}})[0] == code


def test_authorization_failure_without_read_permission(gateway):
    c = server_for("tok-noperm", gateway)
    assert rpc(c["url"], c["headers"], "tools/list")[0] == 200
    status, _ = rpc(c["url"], c["headers"], "tools/call", {"name": TOOLS[0], "arguments": {}})
    assert status == 403


def test_tenant_isolation_across_concurrent_runs(gateway):
    def run(tok):
        c = server_for(tok, gateway)
        return [rpc(c["url"], c["headers"], "tools/call", {"name": "bank_statement_list", "arguments": {}})[1]["result"]
                for _ in range(3)]

    with ThreadPoolExecutor(4) as ex:
        a, b = ex.submit(run, "tok-alice"), ex.submit(run, "tok-bob")
        ra, rb = a.result(), b.result()
    assert all(r == {"content": ["t1-jan.pdf"], "tenant": "t1"} for r in ra)
    assert all(r == {"content": ["t2-feb.pdf"], "tenant": "t2"} for r in rb)


def test_credential_not_inherited_between_evaluations():
    a = config({"BANK_STATEMENT_MCP_ENABLED": "true", "BANK_STATEMENT_MCP_AUTH_TOKEN": "tok-alice"})
    b = config({"BANK_STATEMENT_MCP_ENABLED": "true", "BANK_STATEMENT_MCP_AUTH_TOKEN": "tok-bob"})
    assert a["headers"] != b["headers"] and "alice" not in json.dumps(b)
