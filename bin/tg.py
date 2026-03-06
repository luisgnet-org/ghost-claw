#!/usr/bin/env python3
"""Fallback MCP client for Telegram tools.

Usage:
    python3 bin/tg.py send "message text" --topic "CLAW EXODUS"
    python3 bin/tg.py set-topic "CLAW EXODUS"
    python3 bin/tg.py list-topics
    python3 bin/tg.py react 12345 "👍"

Establishes a short-lived MCP session per call. Use when MCP tools
aren't registered in the Claude Code session.
"""
import argparse
import json
import sys
import urllib.request

MCP_URL = "http://localhost:7865/mcp"


def _post(payload: dict, session_id=None):
    data = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(MCP_URL, data=data, headers=headers)
    resp = urllib.request.urlopen(req, timeout=10)
    return resp.read().decode(), resp.headers.get("Mcp-Session-Id")


def _init_session() :
    body, sid = _post({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "claw-tg-fallback", "version": "0.1"},
        },
    })
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id=sid)
    return sid


def _parse_sse(body: str) :
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {"raw": body}


def _call_tool(name: str, arguments: dict) :
    sid = _init_session()
    body, _ = _post(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        session_id=sid,
    )
    return _parse_sse(body)


def send(text: str, topic=None) :
    args = {"text": text}
    if topic:
        args["topic"] = topic
    return _call_tool("send_message", args)


def set_active_topic(topic: str) :
    return _call_tool("set_active_topic", {"topic": topic})


def list_topics() :
    return _call_tool("list_topics", {})


def react(message_id: int, emoji: str) :
    return _call_tool("react_to_message", {"message_id": message_id, "emoji": emoji})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Telegram tools fallback")
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send", help="Send a message")
    p_send.add_argument("text", help="Message text")
    p_send.add_argument("--topic", "-t", help="Topic name")

    p_topic = sub.add_parser("set-topic", help="Set active topic")
    p_topic.add_argument("topic", help="Topic name")

    p_list = sub.add_parser("list-topics", help="List subscribed topics")

    p_react = sub.add_parser("react", help="React to a message")
    p_react.add_argument("message_id", type=int, help="Message ID")
    p_react.add_argument("emoji", help="Emoji to react with")

    args = parser.parse_args()

    if args.command == "send":
        result = send(args.text, topic=args.topic)
    elif args.command == "set-topic":
        result = set_active_topic(args.topic)
    elif args.command == "list-topics":
        result = list_topics()
    elif args.command == "react":
        result = react(args.message_id, args.emoji)

    print(json.dumps(result, indent=2))
