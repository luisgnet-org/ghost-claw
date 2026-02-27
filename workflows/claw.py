"""
claw — autonomous agent workflow (daemon side).

Telegram shuttle: the daemon polls Telegram and writes files to the
inbox directory. A separate launchd-managed session launcher
(bin/claw-session.py) watches the inbox and runs Claude Code sessions.

This module handles:
1. Shuttle Telegram messages from subscribed topics → inbox/msg_*.json
2. Write heartbeat files → inbox/heartbeat_*.json when due
3. Write trigger files → inbox/trigger_*.json on manual trigger
4. Send typing indicator when a session is alive
5. Provide topic_ids to the daemon-managed MCP server

Multi-topic routing: subscribes to multiple Telegram topics (stored in
state.json). Each shuttled message includes topic name + id so the agent
knows which project context to load.

Registration:
    Drop this file into ghost's workflows/ directory. Add to config.yaml:

    - name: claw
      workflow: claw
      schedule: "every 5s"
      run_while_sleeping: true
      config:
        default_topics: ["my-agent"]
        agent_dir: ~/ghost/agents/claw
        heartbeat_interval_minutes: 30
"""

import json
import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path

from ..config import get_shared, set_shared

logger = logging.getLogger("ghost")

# Defaults — overridden by job config in config.yaml
DEFAULT_TOPICS = ["ghost-agent"]
HEARTBEAT_INTERVAL_MINUTES = 30
JSONL_STALENESS_THRESHOLD = 15  # seconds — skip typing if no tool call in this window

# Paths — derived from agent_dir config or sensible defaults
AGENT_DIR = Path.home() / "ghost" / "agents" / "claw"
WORKSPACE = AGENT_DIR / "workspace"
INBOX = WORKSPACE / "inbox"
RUNS_DIR = Path.home() / "ghost" / "ghost_run_dir" / "workflows" / "claw"
LOCKFILE = RUNS_DIR / ".claude.pid"
SESSIONS_DIR = AGENT_DIR / "sessions"

# Topic icon emoji IDs (free Telegram forum topic icons)
try:
    from ..services.telegram_topic_icons import TOPIC_ICONS as _ICONS
    ICON_FIRE = _ICONS["🔥"]       # active topic
    ICON_EYES = _ICONS["👀"]       # watching (alive but working on different topic)
    ICON_INCOMING = _ICONS["💬"]   # message received, session pending
    ICON_ROBOT = _ICONS["🤖"]      # sleeping (no session)
except ImportError:
    ICON_FIRE = ICON_EYES = ICON_INCOMING = ICON_ROBOT = None

# Module-level state. Seeded on first run, survives across scheduled invocations.
_topic_ids: dict[str, int] = {}      # {topic_name: topic_id} — resolved from Telegram
_topic_cursors: dict[str, int] = {}  # {topic_name: update_id} — persisted to state
_bot_user_id = None
_mcp_server = None  # Set by daemon via set_mcp_server()
_config: dict = {}  # Populated from job config on first run


def set_mcp_server(server):
    """Set the daemon-managed MCP server reference."""
    global _mcp_server
    _mcp_server = server


def _init_config(config: dict):
    """Initialize module paths from job config (called once)."""
    global AGENT_DIR, WORKSPACE, INBOX, RUNS_DIR, LOCKFILE, SESSIONS_DIR
    global DEFAULT_TOPICS, HEARTBEAT_INTERVAL_MINUTES, _config

    if _config:
        return  # Already initialized

    _config = config

    agent_dir = config.get("agent_dir")
    if agent_dir:
        AGENT_DIR = Path(os.path.expanduser(agent_dir))
        WORKSPACE = AGENT_DIR / "workspace"
        INBOX = WORKSPACE / "inbox"
        SESSIONS_DIR = AGENT_DIR / "sessions"

    runs_dir = config.get("runs_dir")
    if runs_dir:
        RUNS_DIR = Path(os.path.expanduser(runs_dir))
        LOCKFILE = RUNS_DIR / ".claude.pid"

    topics = config.get("default_topics")
    if topics and isinstance(topics, list):
        DEFAULT_TOPICS = topics

    hb = config.get("heartbeat_interval_minutes")
    if hb:
        HEARTBEAT_INTERVAL_MINUTES = int(hb)


def get_subscribed_topics() -> list[str]:
    """Get list of subscribed topic names from state. Used by MCP tools."""
    topics = get_shared("claw_subscribed_topics")
    if topics and isinstance(topics, list):
        return topics
    return list(DEFAULT_TOPICS)


def subscribe_topic(name: str) -> None:
    """Add a topic to subscriptions. Used by MCP tools."""
    topics = get_subscribed_topics()
    if name not in topics:
        topics.append(name)
        set_shared("claw_subscribed_topics", topics)
        logger.info(f"claw: subscribed to topic '{name}'")


def unsubscribe_topic(name: str) -> bool:
    """Remove a topic from subscriptions. Returns False if it was the last one."""
    topics = get_subscribed_topics()
    if name not in topics:
        return True
    if len(topics) <= 1:
        return False
    topics.remove(name)
    set_shared("claw_subscribed_topics", topics)
    _topic_ids.pop(name, None)
    _topic_cursors.pop(name, None)
    logger.info(f"claw: unsubscribed from topic '{name}'")
    return True


def get_topic_ids() -> dict[str, int]:
    """Get resolved topic_name → topic_id map. Used by MCP server."""
    return dict(_topic_ids)


def kill_session() -> str:
    """Kill a running session. Returns status message."""
    if not LOCKFILE.exists():
        return "No active session (no lockfile)."
    try:
        pid = int(LOCKFILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                break
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        LOCKFILE.unlink(missing_ok=True)
        return f"Session killed (pid {pid})."
    except ValueError:
        LOCKFILE.unlink(missing_ok=True)
        return "Stale lockfile removed (bad PID)."
    except ProcessLookupError:
        LOCKFILE.unlink(missing_ok=True)
        return f"Process {pid} already dead. Lockfile cleaned."


async def run(tg_client, llm_client, config: dict):
    """Main entry point — called by daemon on schedule.

    Shuttles messages from all subscribed topics to inbox and writes
    trigger/heartbeat files. Session lifecycle is handled by the
    standalone launcher (bin/claw-session.py).
    """
    global _bot_user_id

    # Initialize config on first run
    _init_config(config)

    manual = config.get("manual", False)
    store = tg_client.store

    # Load subscribed topics
    topic_names = get_subscribed_topics()

    # Resolve topic IDs (cached in module state)
    for name in topic_names:
        if name not in _topic_ids:
            _topic_ids[name] = await tg_client.get_or_create_topic(name)
            logger.info(f"claw topic resolved: {name} → {_topic_ids[name]}")

    # Push topic map to MCP server
    if _mcp_server is not None:
        _mcp_server.set_topic_ids(_topic_ids)

    # Restore per-topic cursors from state on first run
    if not _topic_cursors:
        saved = get_shared("claw_topic_cursors")
        if saved and isinstance(saved, dict):
            _topic_cursors.update(saved)
            logger.info(f"claw cursors restored: {list(_topic_cursors.keys())}")
        else:
            old_cursor = get_shared("claw_telegram_cursor")
            if old_cursor is not None:
                for name in topic_names:
                    _topic_cursors[name] = old_cursor
                logger.info(f"claw: migrated single cursor {old_cursor} to per-topic")

    # Seed cursors for any topics that don't have one yet
    for name in topic_names:
        if name not in _topic_cursors:
            all_recent = await store.query_events(since_update_id=0, limit=1000)
            _topic_cursors[name] = all_recent[-1]["update_id"] if all_recent else 0
            logger.info(f"claw: seeded cursor for '{name}' at {_topic_cursors[name]}")
    _save_cursors()

    # Cache bot user ID
    if _bot_user_id is None:
        bot_info = await tg_client.bot.get_me()
        _bot_user_id = bot_info.id

    # 1. Shuttle new messages from all subscribed topics
    all_new_ids = []
    topics_with_new = []
    for name in topic_names:
        topic_id = _topic_ids[name]
        new_ids = await _shuttle_messages(tg_client, topic_id, name)
        if new_ids:
            all_new_ids.extend(new_ids)
            topics_with_new.append(name)

    # 2. If session alive, send typing indicator
    if _session_alive():
        if _session_recently_active():
            active_topic = None
            if _mcp_server is not None and hasattr(_mcp_server, "_active_topic"):
                active_topic = _mcp_server._active_topic
            typing_topics = [active_topic] if active_topic and active_topic in _topic_ids else list(topic_names)
            for name in typing_topics:
                try:
                    await tg_client.bot.send_chat_action(
                        chat_id=tg_client.chat_id,
                        action="typing",
                        message_thread_id=_topic_ids[name],
                    )
                except Exception:
                    pass
        return

    # 3. Signal "message pending" on topics that received new messages
    if topics_with_new and ICON_INCOMING:
        for name in topic_names:
            icon = ICON_INCOMING if name in topics_with_new else ICON_ROBOT
            try:
                await tg_client.bot.edit_forum_topic(
                    chat_id=tg_client.chat_id,
                    message_thread_id=_topic_ids[name],
                    icon_custom_emoji_id=icon,
                )
            except Exception:
                pass

    # 4. If heartbeat due, write heartbeat file
    has_pending = INBOX.exists() and (
        any(INBOX.glob("msg_*.json"))
        or any(INBOX.glob("heartbeat_*.json"))
        or any(INBOX.glob("trigger_*.json"))
    )
    if not manual and not has_pending and config.get("heartbeats_enabled", True) and _heartbeat_due():
        INBOX.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        hb_path = INBOX / f"heartbeat_{ts}.json"
        hb_path.write_text(json.dumps({"type": "heartbeat", "timestamp": ts}))
        set_shared("claw_last_session_end", datetime.now())
        logger.info("claw: wrote heartbeat trigger to inbox")

    # 5. If manual trigger, write trigger file
    if manual:
        INBOX.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        trig_path = INBOX / f"trigger_{ts}.json"
        trig_path.write_text(json.dumps({"type": "manual", "timestamp": ts}))
        logger.info("claw: wrote manual trigger to inbox")


MEDIA_DIR = INBOX / "media"


async def _download_media(tg_client, media: dict, update_id: int) -> str | None:
    """Download media file, return path relative to workspace."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext_map = {"photo": "jpg", "voice": "ogg", "audio": "mp3", "video_note": "mp4"}
    ext = ext_map.get(media["type"])
    if not ext and media.get("file_name"):
        ext = media["file_name"].rsplit(".", 1)[-1] if "." in media["file_name"] else "bin"
    fname = media.get("file_name") or f"{update_id}_{media['type']}.{ext or 'bin'}"
    dest = MEDIA_DIR / fname
    try:
        await tg_client.download_file(media["file_id"], dest)
        return f"inbox/media/{fname}"
    except Exception as e:
        logger.warning(f"claw: media download failed: {e}")
        return None


def _transcribe_audio(file_path: Path) -> str | None:
    """Transcribe audio via Whisper-compatible API. Returns text or None."""
    from ..config import get_transcription_config
    import urllib.request

    cfg = get_transcription_config()
    if not cfg["api_key"]:
        logger.warning("claw: no transcription API key, skipping")
        return None

    boundary = f"----boundary{int(time.time())}"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{cfg['model']}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\ntext\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.ogg\"\r\n"
        f"Content-Type: audio/ogg\r\n\r\n"
    ).encode()
    body += file_path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        cfg["endpoint"],
        data=body,
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode().strip()
    except Exception as e:
        logger.warning(f"claw: transcription failed: {e}")
        return None


async def _shuttle_messages(tg_client, topic_id: int, topic_name: str) -> list[int]:
    """Poll store for new messages in a topic, write to inbox/.

    Downloads media files and transcribes voice/audio messages.
    Injects topic name and topic_id into the message JSON for
    downstream context routing.
    """
    store = tg_client.store
    cursor = _topic_cursors.get(topic_name, 0)

    events = await store.query_events(
        event_type="message",
        topic_id=topic_id,
        since_update_id=cursor,
        limit=10,
    )

    new_message_ids = []
    for event in events:
        _topic_cursors[topic_name] = event["update_id"]

        if event.get("user_id") == _bot_user_id:
            continue

        text = event.get("text", "")
        media = json.loads(event.get("media_json") or "null")

        if not text and not media:
            continue

        if text.startswith("/"):
            continue

        media_payload = None
        if media:
            file_path = await _download_media(tg_client, media, event["update_id"])
            if file_path:
                media_payload = {
                    "type": media["type"],
                    "file_path": file_path,
                    "mime_type": media.get("mime_type"),
                }

                if media["type"] in ("voice", "audio"):
                    abs_path = WORKSPACE / file_path
                    transcription = _transcribe_audio(abs_path)
                    if transcription:
                        media_payload["transcription"] = transcription
                        if not text:
                            text = transcription

        INBOX.mkdir(parents=True, exist_ok=True)
        msg_path = INBOX / f"msg_{event['update_id']}.json"
        msg_data = {
            "from": event.get("user_name", "user"),
            "text": text,
            "topic": topic_name,
            "topic_id": topic_id,
            "timestamp": event.get("timestamp", ""),
            "message_id": event.get("message_id"),
            "update_id": event["update_id"],
        }
        if media_payload:
            msg_data["media"] = media_payload
        msg_path.write_text(json.dumps(msg_data))
        new_message_ids.append(event["update_id"])

    if new_message_ids:
        _save_cursors()
        logger.info(
            f"claw: shuttled {len(new_message_ids)} messages "
            f"from topic '{topic_name}' to inbox"
        )
    return new_message_ids


def _save_cursors():
    """Persist per-topic cursors to state."""
    set_shared("claw_topic_cursors", dict(_topic_cursors))


def _session_alive() -> bool:
    """Check if a Claude session is currently running via PID lock."""
    if not LOCKFILE.exists():
        return False
    try:
        pid = int(LOCKFILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        LOCKFILE.unlink(missing_ok=True)
        return False


def _session_recently_active() -> bool:
    """Check if the session jsonl was modified recently.

    Returns False when the session is idle (e.g. blocked in wait_for_message),
    suppressing the typing indicator during that period.
    """
    try:
        latest = max(SESSIONS_DIR.rglob("session_*.jsonl"), key=lambda p: p.stat().st_mtime)
        age = time.time() - latest.stat().st_mtime
        return age < JSONL_STALENESS_THRESHOLD
    except (ValueError, OSError):
        return False


def _heartbeat_due() -> bool:
    """Check if enough time has passed since last session for a heartbeat."""
    last_end = get_shared("claw_last_session_end")
    if not last_end:
        return True
    try:
        last_dt = datetime.fromisoformat(last_end)
        elapsed = (datetime.now() - last_dt).total_seconds() / 60
        return elapsed >= HEARTBEAT_INTERVAL_MINUTES
    except (ValueError, TypeError):
        return True
