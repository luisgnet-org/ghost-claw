#!/usr/bin/env python3
"""General-purpose MCP client for the ghost daemon.

Usage:
    python3 bin/ghost_mcp.py tools                          # list available tools
    python3 bin/ghost_mcp.py call send_message text="hi" topic="MY_TOPIC"
    python3 bin/ghost_mcp.py call set_active_topic topic="MY_TOPIC"
    python3 bin/ghost_mcp.py quota                          # direct quota check (no daemon needed)

Creates a fresh MCP session per invocation. Survives daemon restarts.
The `quota` command is a direct fallback that reads credentials and poll
data without requiring the ghost daemon to be running.

Environment:
    MCP_PORT — ghost daemon MCP port (default: 7865)
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Direct quota command (no daemon required)
# ---------------------------------------------------------------------------

# HOME is the claw isolated home; ghost root is three levels up
_CLAW_HOME = Path.home()
_GHOST_HOME = _CLAW_HOME.parent.parent.parent
_CLAW_CREDENTIALS = _CLAW_HOME / ".claude" / ".credentials.json"
_QUOTA_START_FILE = _GHOST_HOME / "ghost_run_dir" / "workflows" / "claw" / "quota_start.json"
_QUOTA_POLL_FILE  = _GHOST_HOME / "ghost_run_dir" / "workflows" / "claw" / "quota_poll.jsonl"
_OAUTH_USAGE_URL  = "https://api.anthropic.com/api/oauth/usage"
_QUOTA_TIERS      = ("five_hour", "seven_day", "seven_day_sonnet")


def _quota_fetch_live():
    try:
        data = json.loads(_CLAW_CREDENTIALS.read_text())
        token = data.get("claudeAiOauth", {}).get("accessToken")
    except Exception:
        token = None
    if not token:
        return None
    try:
        req = urllib.request.Request(
            _OAUTH_USAGE_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "ghost-mcp-cli/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"quota: API fetch failed: {e}", file=sys.stderr)
        return None


def _quota_instantaneous_rates(session_id, window_minutes=5.0):
    if not _QUOTA_POLL_FILE.exists():
        return {}
    now_utc = datetime.now(timezone.utc)
    window_secs = window_minutes * 60
    try:
        lines = _QUOTA_POLL_FILE.read_text().splitlines()
        samples = []
        for line in reversed(lines[-120:]):
            if not line.strip():
                continue
            try:
                s = json.loads(line)
            except Exception:
                continue
            if session_id and s.get("session_id") != session_id:
                break
            ts = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
            if (now_utc - ts).total_seconds() > window_secs:
                break
            samples.append(s)
        if len(samples) < 2:
            return {}
        newest, oldest = samples[0], samples[-1]
        dur_h = (
            datetime.fromisoformat(newest["ts"].replace("Z", "+00:00"))
            - datetime.fromisoformat(oldest["ts"].replace("Z", "+00:00"))
        ).total_seconds() / 3600
        if dur_h <= 0:
            return {}
        return {
            k: round((newest[k] - oldest[k]) / dur_h, 3)
            for k in _QUOTA_TIERS
            if k in newest and k in oldest and newest[k] >= oldest[k]
        }
    except Exception:
        return {}


def _eta_str(minutes):
    if not minutes or minutes <= 0:
        return None
    if minutes < 60:
        return f"{minutes:.0f}m"
    if minutes < 1440:
        return f"{minutes / 60:.1f}h"
    return f"{minutes / 1440:.1f}d"


def quota_direct():
    """Print quota status directly without the ghost daemon."""
    now_utc = datetime.now(timezone.utc)

    # Load session start snapshot
    start_quota, session_id, session_start_ts, session_hours = {}, None, None, None
    try:
        snap = json.loads(_QUOTA_START_FILE.read_text())
        start_quota = snap.get("quota") or {}
        session_id = snap.get("session_id")
        session_start_ts = snap.get("ts")
        if session_start_ts:
            start_dt = datetime.fromisoformat(session_start_ts.replace("Z", "+00:00"))
            session_hours = (now_utc - start_dt).total_seconds() / 3600
    except Exception:
        pass

    current = _quota_fetch_live()
    if not current:
        print("ERROR: could not fetch quota (credentials missing or expired)")
        sys.exit(1)

    instant = _quota_instantaneous_rates(session_id)

    tiers = {}
    for key in _QUOTA_TIERS:
        bucket = current.get(key)
        if not bucket:
            continue
        util = bucket.get("utilization", 0.0)
        tier = {"utilization_pct": round(util, 2)}

        # Reset time
        resets_at = bucket.get("resets_at")
        if resets_at:
            try:
                dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
                tier["resets_at_local"] = dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")
                tier["minutes_until_reset"] = round((dt - now_utc).total_seconds() / 60, 1)
            except Exception:
                pass

        # Session delta
        start_util = start_quota.get(key, {}).get("utilization")
        if start_util is not None:
            tier["delta_since_session_start_pct"] = round(util - start_util, 2)

        # Burn rate (instantaneous preferred)
        burn = None
        if key in instant:
            burn = instant[key]
            tier["burn_rate_pct_per_hour"] = burn
            tier["burn_rate_source"] = "instantaneous_5min"
        elif start_util is not None and session_hours and session_hours > 0:
            delta = util - start_util
            if delta > 0:
                burn = delta / session_hours
                tier["burn_rate_pct_per_hour"] = round(burn, 3)
                tier["burn_rate_source"] = "session_average"

        if burn and burn > 0:
            eta_mins = (100.0 - util) / burn * 60
            tier["estimated_time_to_100pct"] = _eta_str(eta_mins)

        tiers[key] = tier

    # Interpretation
    critical, high = [], []
    for name, t in tiers.items():
        pct = t.get("utilization_pct", 0)
        eta = t.get("estimated_time_to_100pct", "")
        tag = f"{name} {pct:.0f}%" + (f", ~{eta} left" if eta else "")
        if pct >= 95:
            critical.append(tag)
        elif pct >= 75:
            high.append(tag)

    if critical:
        interpretation = "CRITICAL — " + "; ".join(critical) + ". Stop spawning subagents. Finish current task and idle."
    elif high:
        interpretation = "HIGH — " + "; ".join(high) + ". Be conservative. Prefer wait_for_message over proactive work."
    else:
        interpretation = "Quota healthy — no throttling needed."

    result = {
        "ts": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": session_id,
        "session_start_ts": session_start_ts,
        "session_duration_hours": round(session_hours, 2) if session_hours else None,
        "tiers": tiers,
        "interpretation": interpretation,
    }
    print(json.dumps(result, indent=2))


def cost_direct():
    """Print API-equivalent cost summary without the ghost daemon."""
    now = datetime.now()
    sessions_root = _GHOST_HOME / "agents" / "claw" / "sessions"

    # Get current session_id from quota_start snapshot
    session_id = None
    try:
        snap = json.loads(_QUOTA_START_FILE.read_text())
        session_id = snap.get("session_id")
    except Exception:
        pass

    week = {"cost_usd": 0.0, "session_count": 0}
    month = {"cost_usd": 0.0, "session_count": 0}
    current = {}

    month_dir = sessions_root / now.strftime("%Y/%m")
    if month_dir.exists():
        for day_dir in sorted(month_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            try:
                day_num = int(day_dir.name)
            except ValueError:
                continue
            days_ago = (now.date() - now.replace(day=day_num).date()).days
            in_week = days_ago < 7

            for session_dir in day_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for jsonl in session_dir.glob("*.jsonl"):
                    try:
                        cost = input_tok = output_tok = turns = duration_ms = 0
                        for line in jsonl.read_text().splitlines():
                            if not line.strip():
                                continue
                            msg = json.loads(line)
                            if msg.get("type") == "result":
                                cost = msg.get("total_cost_usd", 0.0)
                                usage = msg.get("usage", {})
                                input_tok = usage.get("input_tokens", 0)
                                output_tok = usage.get("output_tokens", 0)
                                turns = msg.get("num_turns", 0)
                                duration_ms = msg.get("duration_ms", 0)
                        if not cost:
                            continue
                        month["cost_usd"] += cost
                        month["session_count"] += 1
                        if in_week:
                            week["cost_usd"] += cost
                            week["session_count"] += 1
                        if session_id and session_dir.name == session_id:
                            current = {
                                "session_id": session_id,
                                "cost_usd": round(cost, 4),
                                "input_tokens": input_tok,
                                "output_tokens": output_tok,
                                "total_tokens": input_tok + output_tok,
                                "num_turns": turns,
                                "duration_minutes": round(duration_ms / 60_000, 1) if duration_ms else None,
                            }
                    except Exception:
                        continue

    # Fall back to latest symlink for current session if not found above
    if session_id and not current:
        latest = sessions_root / "latest"
        if latest.exists():
            for jsonl in latest.glob("*.jsonl"):
                try:
                    cost = input_tok = output_tok = turns = duration_ms = 0
                    for line in jsonl.read_text().splitlines():
                        if not line.strip():
                            continue
                        msg = json.loads(line)
                        if msg.get("type") == "result":
                            cost = msg.get("total_cost_usd", 0.0)
                            usage = msg.get("usage", {})
                            input_tok = usage.get("input_tokens", 0)
                            output_tok = usage.get("output_tokens", 0)
                            turns = msg.get("num_turns", 0)
                            duration_ms = msg.get("duration_ms", 0)
                    if cost:
                        current = {
                            "session_id": session_id,
                            "cost_usd": round(cost, 4),
                            "input_tokens": input_tok,
                            "output_tokens": output_tok,
                            "total_tokens": input_tok + output_tok,
                            "num_turns": turns,
                            "duration_minutes": round(duration_ms / 60_000, 1) if duration_ms else None,
                        }
                except Exception:
                    pass

    result = {
        "current_session": current,
        "week": {"cost_usd": round(week["cost_usd"], 4), "session_count": week["session_count"]},
        "month": {"cost_usd": round(month["cost_usd"], 4), "session_count": month["session_count"]},
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: ghost_mcp.py tools | call <tool_name> [key=value ...] | quota | cost")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "tools":
        list_tools()
    elif cmd == "call":
        if len(sys.argv) < 3:
            print("Usage: ghost_mcp.py call <tool_name> [key=value ...]", file=sys.stderr)
            sys.exit(1)
        call_tool(sys.argv[2], _parse_kwargs(sys.argv[3:]))
    elif cmd == "quota":
        quota_direct()
    elif cmd == "cost":
        cost_direct()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
