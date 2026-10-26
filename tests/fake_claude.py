"""A stand-in for Claude Code's `claude -p --output-format stream-json`. It reads the studio's
MCP config from --mcp-config, connects to the studio's /mcp over HTTP exactly as Claude would,
runs its tools, and prints the stream-json events the studio parses. This exercises the whole
chat → /mcp → engine → record path without the real Claude.

Environment (all optional):
- FAKE_CLAUDE_TOOL: the tools to call, comma-separated (default binary_overview; "" calls none).
- FAKE_CLAUDE_ARGS: JSON arguments for every call (an object), or one object per tool (a list).
- FAKE_CLAUDE_SAY: the final text (default "Done.").
- FAKE_CLAUDE_NARRATE: a text block to print before the first tool call.
- FAKE_CLAUDE_SLEEP: seconds to wait before answering (so a test can press Stop).
- FAKE_CLAUDE_OFFLINE: when set, /mcp is never contacted (for tests of the runner alone).
- FAKE_CLAUDE_DUMP: a file to write what this run saw, as JSON: argv, cwd, the prompt, the
  tool names /mcp listed, and each tools/call reply.
With --version it prints a version; with `auth status --json` it prints a signed-in status, so
the Settings checks pass against it too.
"""
import json
import os
import sys
import time
import urllib.request


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def flag(args, name):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def rpc(url, token, message):
    data = json.dumps(message).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as response:
        if response.status == 202:
            return None
        return json.loads(response.read().decode("utf-8"))


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("9.9.9 (Claude Code)")
        return 0
    if "auth" in args and "status" in args:
        print(json.dumps({"loggedIn": True, "email": "tester@example.com", "subscriptionType": "max"}))
        return 0

    prompt = sys.stdin.read()
    config_path = flag(args, "--mcp-config")
    tools = [t for t in os.environ.get("FAKE_CLAUDE_TOOL", "binary_overview").split(",") if t.strip()]
    raw_args = json.loads(os.environ.get("FAKE_CLAUDE_ARGS") or "{}")
    say = os.environ.get("FAKE_CLAUDE_SAY", "Done.")
    seen = {"argv": args, "cwd": os.getcwd(), "prompt": prompt, "listed": [], "replies": []}
    connected = "connected"
    url = token = None
    try:
        config = json.loads(open(config_path, encoding="utf-8").read())
        server = config["mcpServers"]["rea"]
        url = server["url"]
        token = server["headers"]["Authorization"].split(" ", 1)[1]
    except Exception:
        connected = "failed"

    emit({"type": "system", "subtype": "init", "model": "fake-claude",
          "mcp_servers": [{"name": "rea", "status": connected}]})
    if os.environ.get("FAKE_CLAUDE_NARRATE"):
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": os.environ["FAKE_CLAUDE_NARRATE"]}]}})

    ok = True
    if url and token and not os.environ.get("FAKE_CLAUDE_OFFLINE"):
        try:
            rpc(url, token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake"}}})
            rpc(url, token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            listed = rpc(url, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            seen["listed"] = [t.get("name") for t in ((listed or {}).get("result") or {}).get("tools") or []]
            for number, tool in enumerate(tools):
                arguments = raw_args[number] if isinstance(raw_args, list) else raw_args
                emit({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": f"mcp__rea__{tool}", "input": arguments}]}})
                reply = rpc(url, token, {"jsonrpc": "2.0", "id": 10 + number, "method": "tools/call",
                                         "params": {"name": tool, "arguments": arguments}})
                seen["replies"].append(reply)
                ok = ok and bool(reply and "result" in reply)
        except Exception as exc:
            say = f"Could not reach REA: {exc}"
            ok = False

    if os.environ.get("FAKE_CLAUDE_DUMP"):
        with open(os.environ["FAKE_CLAUDE_DUMP"], "w", encoding="utf-8") as fh:
            json.dump(seen, fh)
    time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP") or 0))
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": say}]}})
    emit({"type": "result", "subtype": "success" if ok else "error", "is_error": not ok,
          "result": say, "num_turns": 2, "total_cost_usd": 0.0})
    return 0


if __name__ == "__main__":
    sys.exit(main())
