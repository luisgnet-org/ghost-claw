#!/usr/bin/env python3
"""General-purpose MCP client for the ghost daemon.

Usage:
    python3 bin/ghost_mcp.py tools                          # list available tools
    python3 bin/ghost_mcp.py call send_message text="hi" topic="MY_TOPIC"
    python3 bin/ghost_mcp.py call set_active_topic topic="MY_TOPIC"

Creates a fresh MCP session per invocation. Survives daemon restarts.

Environment:
    MCP_PORT — ghost daemon MCP port (default: 7865)
"""
import json
import os
import sys
import urllib.request

MCP_PORT = os.environ.get("MCP_PORT", "7865")
MCP_URL = f"http://localhost:{MCP_PORT}/mcp"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _post(payload, sid=None):
    h = dict(HEADERS)
    if sid:
        h["Mcp-Session-Id"] = sid
    req = urllib.request.Request(MCP_URL, json.dumps(payload).encode(), headers=h)
    resp = urllib.request.urlopen(req, timeout=30)
    body = resp.read().decode()
    return body, resp.headers.get("Mcp-Session-Id")


def _parse(body):
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def _init():
    body, sid = _post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "ghost-mcp-cli", "version": "0.1"}},
    })
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid)
    return sid


def list_tools():
    sid = _init()
    body, _ = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid=sid)
    result = _parse(body)
    tools = result.get("result", {}).get("tools", [])
    for t in tools:
        params = [p for p in t.get("inputSchema", {}).get("properties", {}).keys()]
        req = t.get("inputSchema", {}).get("required", [])
        param_str = ", ".join(f"{p}*" if p in req else p for p in params)
        print(f"  {t['name']}({param_str})")
    print(f"\n{len(tools)} tools available (* = required)")


def call_tool(name, arguments):
    sid = _init()
    body, _ = _post({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }, sid=sid)
    result = _parse(body)
    # Extract text content
    contents = result.get("result", {}).get("content", [])
    for c in contents:
        if c.get("type") == "text":
            print(c["text"])
    if result.get("result", {}).get("isError"):
        sys.exit(1)


def _parse_kwargs(args):
    """Parse key=value pairs, auto-converting ints and bools."""
    d = {}
    for a in args:
        if "=" not in a:
            print(f"Error: argument '{a}' must be key=value", file=sys.stderr)
            sys.exit(1)
        k, v = a.split("=", 1)
        if v.isdigit():
            v = int(v)
        elif v.lower() in ("true", "false"):
            v = v.lower() == "true"
        d[k] = v
    return d


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: ghost_mcp.py tools | call <tool_name> [key=value ...]")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "tools":
        list_tools()
    elif cmd == "call":
        if len(sys.argv) < 3:
            print("Usage: ghost_mcp.py call <tool_name> [key=value ...]", file=sys.stderr)
            sys.exit(1)
        call_tool(sys.argv[2], _parse_kwargs(sys.argv[3:]))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
