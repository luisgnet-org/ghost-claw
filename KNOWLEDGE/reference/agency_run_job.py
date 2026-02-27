"""
Reference: Agency integration for _run_job().

This code was originally in ghost/daemon.py. It allowed the daemon to
manage agency agent lifecycles directly — wiring up LLM clients, session
logging, and tool callbacks on behalf of workflow modules.

It was removed from ghost core to eliminate the agency dependency.
Workflows that use agency should implement this pattern inside their
own run() function instead.

Usage pattern for a workflow that uses agency:

    # ghost/workflows/my_agent_workflow.py

    from agency import Agent, AgentCallbacks, run_agent
    from agency.plugins.session_log import (
        finalize_session,
        with_session_logging,
        with_tool_logging,
    )

    async def run(tg, llm_client, config):
        # Build agent state (your workflow's responsibility)
        state, callbacks = create_my_agent(tg, config)
        state.client = llm_client

        # Session logging
        sd = Path(f"sessions/{datetime.now():%Y-%m-%d_%H%M%S}")
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "calls").mkdir(exist_ok=True)
        state.callbacks = AgentCallbacks(
            llm_call=with_session_logging(sd)(callbacks.llm_call),
            execute_tool=with_tool_logging(sd)(callbacks.execute_tool),
            should_continue=callbacks.should_continue,
        )

        # Run
        result = await run_agent(state)
        finalize_session(sd, status=result.exit_reason or "completed")
"""

# ── Original daemon code (for reference) ──────────────────────────────────

# This was the create_agent code path in GhostAgencyDaemon._run_job():
#
#     # Standard jobs: create_agent + run_agent
#     state, callbacks = job_module.create_agent(self.tg, config)
#     state.client = self.llm_client
#
#     # Session logging — workflows/<name>/agency_sessions/YYYY-MM-DD_HHMMSS/
#     now_ts = datetime.now()
#     sd = workflow_dir(name) / "agency_sessions" / now_ts.strftime("%Y-%m-%d_%H%M%S")
#     sd.mkdir(parents=True, exist_ok=True)
#     (sd / "calls").mkdir(exist_ok=True)
#     state.callbacks = AgentCallbacks(
#         llm_call=with_session_logging(sd)(callbacks.llm_call),
#         execute_tool=with_tool_logging(sd)(callbacks.execute_tool),
#         should_continue=callbacks.should_continue,
#     )
#
#     # Run agent
#     result = await run_agent(state)
#
#     # Finalize session log
#     exit_reason = result.exit_reason or "completed"
#     finalize_session(sd, status=exit_reason)
#
# The daemon also had these top-level imports:
#     from agency import Agent, AgentCallbacks, run_agent
#     from agency.plugins.session_log import (
#         finalize_session,
#         with_session_logging,
#         with_tool_logging,
#     )
#
# And the MCP server startup:
#     from .services.mcp import AgentMCPServer
#     self._mcp_server = AgentMCPServer(self.tg)
#     await self._mcp_server.start()
