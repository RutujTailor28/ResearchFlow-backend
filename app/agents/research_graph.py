"""LangGraph multi-researcher orchestration.

Approved plan → fan-out → parallel researcher agents → collect findings.
Supports cooperative cancel / timeout / cost abort (Phase 10).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.agents.researcher_agent import (
    MAX_TOOL_CALLS,
    ResearcherAgent,
    ResearcherOutcome,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.run_control import RunAborted, StopReason, run_control

logger = get_logger(__name__)


class ResearchGraphState(TypedDict, total=False):
    run_id: str
    topic: str
    tasks: list[dict[str, Any]]
    outcomes: list[ResearcherOutcome]
    max_workers: int
    abort_reason: str


def build_research_graph(
    researcher: ResearcherAgent | None = None,
) -> Any:
    """
    Build a LangGraph where a fan-out node dispatches parallel researchers
    and a collect node aggregates outcomes. Individual researcher failures
    do not abort the graph.
    """
    agent = researcher or ResearcherAgent()

    def prepare(state: ResearchGraphState) -> ResearchGraphState:
        tasks = state.get("tasks") or []
        logger.info(
            "Research graph prepare run_id=%s tasks=%s max_tool_calls=%s",
            state.get("run_id"),
            len(tasks),
            MAX_TOOL_CALLS,
        )
        return {"outcomes": [], "abort_reason": ""}

    def fanout_researchers(state: ResearchGraphState) -> ResearchGraphState:
        tasks = state.get("tasks") or []
        topic = state.get("topic") or ""
        run_id_raw = state.get("run_id")
        run_id = UUID(str(run_id_raw)) if run_id_raw else None
        settings = get_settings()
        default_workers = int(settings.research_max_workers or 4)
        max_workers = int(
            state.get("max_workers") or min(default_workers, max(1, len(tasks) or 1))
        )
        outcomes: list[ResearcherOutcome] = []
        abort_reason = ""

        def _should_stop() -> StopReason:
            if run_id is None:
                return StopReason.none
            return run_control.check(run_id)

        def _run_one(task: dict[str, Any]) -> ResearcherOutcome:
            if run_id is not None:
                reason = _should_stop()
                if reason != StopReason.none:
                    return ResearcherOutcome(
                        task_id=UUID(str(task["id"])),
                        section_id=UUID(str(task["section_id"])),
                        sub_question=str(task["sub_question"]),
                        success=False,
                        tool_calls=0,
                        gap_note=f"Skipped ({reason.value}): {task['sub_question']}",
                        error=reason.value,
                        agent_name=task.get("agent_name"),
                    )
            return agent.research(
                task_id=UUID(str(task["id"])),
                section_id=UUID(str(task["section_id"])),
                sub_question=str(task["sub_question"]),
                topic=topic,
                run_id=run_id,
                agent_name=task.get("agent_name"),
                should_continue=(
                    (lambda: _should_stop() == StopReason.none) if run_id else None
                ),
            )

        if not tasks:
            return {"outcomes": outcomes}

        # Parallel execution — one failure must not cancel siblings.
        # Global abort cancels pending futures but keeps finished outcomes.
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one, task): task for task in tasks}
            for fut in as_completed(futures):
                task = futures[fut]
                try:
                    outcomes.append(fut.result())
                except RunAborted as exc:
                    abort_reason = exc.reason.value
                    outcomes.append(
                        ResearcherOutcome(
                            task_id=UUID(str(task["id"])),
                            section_id=UUID(str(task["section_id"])),
                            sub_question=str(task["sub_question"]),
                            success=False,
                            tool_calls=0,
                            gap_note=f"Aborted ({exc.reason.value})",
                            error=exc.reason.value,
                            agent_name=task.get("agent_name"),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Parallel researcher future failed")
                    outcomes.append(
                        ResearcherOutcome(
                            task_id=UUID(str(task["id"])),
                            section_id=UUID(str(task["section_id"])),
                            sub_question=str(task["sub_question"]),
                            success=False,
                            tool_calls=0,
                            gap_note=f"Researcher crashed for: {task['sub_question']}",
                            error=f"{type(exc).__name__}: {exc}",
                            agent_name=task.get("agent_name"),
                        )
                    )

                if run_id is not None:
                    reason = _should_stop()
                    if reason != StopReason.none:
                        abort_reason = reason.value
                        for pending in futures:
                            if not pending.done():
                                pending.cancel()
                        # Mark not-yet-collected cancelled tasks as skipped.
                        done_ids = {str(o.task_id) for o in outcomes}
                        for ptask in tasks:
                            if str(ptask["id"]) in done_ids:
                                continue
                            outcomes.append(
                                ResearcherOutcome(
                                    task_id=UUID(str(ptask["id"])),
                                    section_id=UUID(str(ptask["section_id"])),
                                    sub_question=str(ptask["sub_question"]),
                                    success=False,
                                    tool_calls=0,
                                    gap_note=f"Skipped ({reason.value})",
                                    error=reason.value,
                                    agent_name=ptask.get("agent_name"),
                                )
                            )
                        break

        return {"outcomes": outcomes, "abort_reason": abort_reason}

    def collect(state: ResearchGraphState) -> ResearchGraphState:
        outcomes = state.get("outcomes") or []
        ok = sum(1 for o in outcomes if o.success)
        failed = len(outcomes) - ok
        logger.info(
            "Research graph collect run_id=%s ok=%s failed=%s abort=%s",
            state.get("run_id"),
            ok,
            failed,
            state.get("abort_reason") or "",
        )
        return {}

    graph = StateGraph(ResearchGraphState)
    graph.add_node("prepare", prepare)
    graph.add_node("fanout_researchers", fanout_researchers)
    graph.add_node("collect", collect)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "fanout_researchers")
    graph.add_edge("fanout_researchers", "collect")
    graph.add_edge("collect", END)
    return graph.compile()


def run_research_graph(
    *,
    run_id: UUID,
    topic: str,
    tasks: list[dict[str, Any]],
    researcher: ResearcherAgent | None = None,
    max_workers: int | None = None,
) -> list[ResearcherOutcome]:
    settings = get_settings()
    workers = max_workers if max_workers is not None else int(settings.research_max_workers)
    compiled = build_research_graph(researcher=researcher)
    final_state = compiled.invoke(
        {
            "run_id": str(run_id),
            "topic": topic,
            "tasks": tasks,
            "outcomes": [],
            "max_workers": workers,
            "abort_reason": "",
        }
    )
    return list(final_state.get("outcomes") or [])
