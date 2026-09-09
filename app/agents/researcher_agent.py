"""Researcher agent — bounded tool loop for one sub-question."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, TYPE_CHECKING
from uuid import UUID, uuid4
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.schemas.agent_event import AgentEventType, AgentRole
from app.tools.extract import extract_readable_content
from app.tools.fetch import fetch_page
from app.tools.quality import score_source_quality
from app.tools.search.factory import get_search_provider

if TYPE_CHECKING:
    from app.services.agent_event_service import AgentEventService

logger = get_logger(__name__)

MAX_TOOL_CALLS = 4


@dataclass
class ResearcherEvidence:
    url: str
    title: str
    snippet: str
    extract_text: str
    content_hash: str
    domain: str
    quality_score: float


@dataclass
class ResearcherOutcome:
    task_id: UUID
    section_id: UUID
    sub_question: str
    success: bool
    tool_calls: int
    claim: Optional[str] = None
    confidence: float = 0.0
    evidence: Optional[ResearcherEvidence] = None
    gap_note: Optional[str] = None
    error: Optional[str] = None
    source_payload: Optional[dict[str, Any]] = None
    agent_name: Optional[str] = None


@dataclass
class ResearcherDependencies:
    search: Callable[[str, int], Any]
    fetch: Callable[[str], Any]
    extract: Callable[[str, str], Any]
    score: Callable[..., Any]


def default_dependencies() -> ResearcherDependencies:
    provider = get_search_provider()

    def _search(query: str, max_results: int = 5):
        return provider.search(query, max_results=max_results)

    def _fetch(url: str):
        return fetch_page(url)

    def _extract(html: str, fallback_title: str = ""):
        return extract_readable_content(html, fallback_title=fallback_title)

    return ResearcherDependencies(
        search=_search,
        fetch=_fetch,
        extract=_extract,
        score=score_source_quality,
    )


class ResearcherAgent:
    """
    Loop (max 4 tool calls):
      Search → Evaluate → Fetch → Extract → Decide → (optional) search again
    Tool calls counted for search/fetch/extract only.
    """

    def __init__(
        self,
        deps: ResearcherDependencies | None = None,
        events: "AgentEventService | None" = None,
    ) -> None:
        self._deps = deps or default_dependencies()
        self._events = events

    def research(
        self,
        *,
        task_id: UUID,
        section_id: UUID,
        sub_question: str,
        topic: str,
        run_id: UUID | None = None,
        agent_name: str | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> ResearcherOutcome:
        tool_calls = 0
        best_evidence: ResearcherEvidence | None = None
        last_error: str | None = None
        name = agent_name or f"researcher-{str(task_id)[:8]}"

        def _alive() -> bool:
            if should_continue is None:
                return True
            try:
                return bool(should_continue())
            except Exception:  # noqa: BLE001
                return True

        self._emit(
            run_id,
            name,
            AgentEventType.agent_started,
            {
                "task_id": str(task_id),
                "section_id": str(section_id),
                "sub_question": sub_question,
                "topic": topic,
                "message": f"Researching: {sub_question}",
            },
        )
        self._emit(
            run_id,
            name,
            AgentEventType.thinking,
            {"message": "Planning search strategy", "sub_question": sub_question},
        )

        try:
            while tool_calls < MAX_TOOL_CALLS:
                if not _alive():
                    last_error = "cancelled"
                    break
                # 1) Search
                query = f"{topic}: {sub_question}"
                self._emit(
                    run_id,
                    name,
                    AgentEventType.tool_call,
                    {"tool": "search", "query": query, "max_results": 5},
                )
                search_result = self._deps.search(query, 5)
                tool_calls += 1
                hits = getattr(search_result, "results", []) or []
                self._emit(
                    run_id,
                    name,
                    AgentEventType.tool_result,
                    {
                        "tool": "search",
                        "hit_count": len(hits),
                        "query": query,
                    },
                )
                if not hits:
                    last_error = "No search results"
                    if tool_calls >= MAX_TOOL_CALLS:
                        break
                    continue

                # 2) Evaluate (heuristic ranking — no fabricated sources)
                candidates = self._rank_hits(hits)[:2]
                self._emit(
                    run_id,
                    name,
                    AgentEventType.thinking,
                    {
                        "message": "Evaluating search candidates",
                        "candidates": len(candidates),
                    },
                )

                answered = False
                for hit in candidates:
                    if not _alive():
                        last_error = "cancelled"
                        break
                    if tool_calls >= MAX_TOOL_CALLS:
                        break
                    url = getattr(hit, "url", None) or hit.get("url")
                    title = getattr(hit, "title", None) or hit.get("title") or ""
                    snippet = getattr(hit, "snippet", None) or hit.get("snippet") or ""
                    if not url:
                        continue
                    try:
                        # 3) Fetch
                        self._emit(
                            run_id,
                            name,
                            AgentEventType.tool_call,
                            {"tool": "fetch", "url": url},
                        )
                        fetched = self._deps.fetch(url)
                        tool_calls += 1
                        html = getattr(fetched, "html", "") or ""
                        final_url = getattr(fetched, "final_url", url) or url
                        self._emit(
                            run_id,
                            name,
                            AgentEventType.tool_result,
                            {
                                "tool": "fetch",
                                "url": final_url,
                                "html_bytes": len(html),
                            },
                        )

                        if tool_calls >= MAX_TOOL_CALLS and not html:
                            break

                        if not _alive():
                            last_error = "cancelled"
                            break

                        # 4) Extract
                        self._emit(
                            run_id,
                            name,
                            AgentEventType.tool_call,
                            {"tool": "extract", "url": final_url},
                        )
                        extracted = self._deps.extract(html, title)
                        tool_calls += 1
                        text = getattr(extracted, "text", "") or ""
                        ext_title = getattr(extracted, "title", "") or title
                        self._emit(
                            run_id,
                            name,
                            AgentEventType.tool_result,
                            {
                                "tool": "extract",
                                "url": final_url,
                                "word_count": len(text.split()),
                                "title": ext_title,
                            },
                        )

                        if len(text.split()) < 40:
                            continue

                        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                        domain = urlparse(final_url).netloc.lower().removeprefix("www.")
                        quality = self._deps.score(
                            url=final_url,
                            domain=domain,
                            title=ext_title,
                            extract_text=text,
                        )
                        evidence = ResearcherEvidence(
                            url=final_url,
                            title=ext_title,
                            snippet=snippet[:500],
                            extract_text=text,
                            content_hash=content_hash,
                            domain=domain,
                            quality_score=float(getattr(quality, "score", 50)),
                        )
                        if best_evidence is None or evidence.quality_score > best_evidence.quality_score:
                            best_evidence = evidence

                        # 5) Decide whether answered
                        if self._is_answered(sub_question, text, snippet):
                            answered = True
                            best_evidence = evidence
                            break
                    except Exception as exc:  # noqa: BLE001 - isolate per-source failures
                        last_error = f"{type(exc).__name__}: {exc}"
                        logger.warning(
                            "Researcher tool failure task=%s error=%s",
                            task_id,
                            type(exc).__name__,
                        )
                        self._emit(
                            run_id,
                            name,
                            AgentEventType.error,
                            {
                                "error": last_error,
                                "phase": "source_tools",
                                "url": url,
                            },
                        )
                        continue

                if last_error == "cancelled":
                    break

                if answered and best_evidence is not None:
                    claim = self._paraphrase_claim(sub_question, best_evidence)
                    confidence = min(0.95, 0.45 + (best_evidence.quality_score / 200.0))
                    outcome = ResearcherOutcome(
                        task_id=task_id,
                        section_id=section_id,
                        sub_question=sub_question,
                        success=True,
                        tool_calls=tool_calls,
                        claim=claim,
                        confidence=round(confidence, 3),
                        evidence=best_evidence,
                        source_payload={
                            "id": str(uuid4()),
                            "url": best_evidence.url,
                            "domain": best_evidence.domain,
                            "title": best_evidence.title,
                            "quality_score": best_evidence.quality_score,
                            "content_hash": best_evidence.content_hash,
                            "extract_text": best_evidence.extract_text,
                        },
                        agent_name=name,
                    )
                    self._emit(
                        run_id,
                        name,
                        AgentEventType.agent_finished,
                        {
                            "success": True,
                            "tool_calls": tool_calls,
                            "confidence": outcome.confidence,
                            "message": "Finding produced",
                        },
                    )
                    return outcome

                # Not answered — loop to search again if tool budget remains
                if tool_calls >= MAX_TOOL_CALLS:
                    break

            if last_error == "cancelled":
                gap = f"Cancelled while researching: {sub_question}"
                outcome = ResearcherOutcome(
                    task_id=task_id,
                    section_id=section_id,
                    sub_question=sub_question,
                    success=False,
                    tool_calls=tool_calls,
                    gap_note=gap,
                    error="cancelled",
                    evidence=best_evidence,
                    agent_name=name,
                )
                self._emit(
                    run_id,
                    name,
                    AgentEventType.agent_finished,
                    {"success": False, "cancelled": True, "tool_calls": tool_calls},
                )
                return outcome

            gap = (
                f"Insufficient evidence for sub-question after {tool_calls} tool calls: "
                f"{sub_question}"
            )
            outcome = ResearcherOutcome(
                task_id=task_id,
                section_id=section_id,
                sub_question=sub_question,
                success=False,
                tool_calls=tool_calls,
                gap_note=gap,
                error=last_error or "Research incomplete",
                evidence=best_evidence,
                agent_name=name,
            )
            self._emit(
                run_id,
                name,
                AgentEventType.error,
                {"error": outcome.error, "gap_note": gap},
            )
            self._emit(
                run_id,
                name,
                AgentEventType.agent_finished,
                {"success": False, "tool_calls": tool_calls, "gap": True},
            )
            return outcome
        except Exception as exc:  # noqa: BLE001 - never crash the whole run
            logger.exception("Researcher crashed task=%s", task_id)
            outcome = ResearcherOutcome(
                task_id=task_id,
                section_id=section_id,
                sub_question=sub_question,
                success=False,
                tool_calls=tool_calls,
                gap_note=f"Researcher failed for: {sub_question}",
                error=f"{type(exc).__name__}: {exc}",
                agent_name=name,
            )
            self._emit(
                run_id,
                name,
                AgentEventType.error,
                {"error": outcome.error},
            )
            self._emit(
                run_id,
                name,
                AgentEventType.agent_finished,
                {"success": False, "crashed": True},
            )
            return outcome

    def _emit(
        self,
        run_id: UUID | None,
        agent_name: str,
        event_type: AgentEventType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._events is None or run_id is None:
            return
        try:
            self._events.emit(
                run_id=run_id,
                agent_name=agent_name,
                agent_role=AgentRole.researcher,
                event_type=event_type,
                payload=payload or {},
            )
        except Exception:  # noqa: BLE001 - observability must not break research
            logger.warning(
                "Failed to emit researcher event type=%s agent=%s",
                event_type.value,
                agent_name,
            )

    @staticmethod
    def _rank_hits(hits: list[Any]) -> list[Any]:
        def score(hit: Any) -> float:
            url = (getattr(hit, "url", None) or hit.get("url") or "").lower()
            s = 0.0
            for token, pts in (
                (".gov", 30),
                (".edu", 25),
                ("nih.gov", 28),
                ("who.int", 26),
                ("arxiv.org", 20),
                ("nature.com", 18),
                ("reuters.com", 12),
            ):
                if token in url:
                    s += pts
            return s

        return sorted(hits, key=score, reverse=True)

    @staticmethod
    def _is_answered(sub_question: str, text: str, snippet: str) -> bool:
        hay = f"{snippet}\n{text}".lower()
        tokens = [t for t in re.findall(r"[a-z0-9]{4,}", sub_question.lower()) if t not in {
            "what", "when", "where", "which", "does", "have", "with", "from", "this", "that", "about"
        }]
        if not tokens:
            return len(text.split()) >= 120
        hits = sum(1 for t in tokens[:8] if t in hay)
        return hits >= max(2, min(3, len(tokens[:8]) // 2)) and len(text.split()) >= 60

    @staticmethod
    def _paraphrase_claim(sub_question: str, evidence: ResearcherEvidence) -> str:
        """
        Produce a paraphrased claim grounded in extracted text.
        Never invent citations — ties claim to observed evidence only.
        """
        sentences = re.split(r"(?<=[.!?])\s+", evidence.extract_text.strip())
        useful = [s.strip() for s in sentences if 40 <= len(s.strip()) <= 280][:2]
        if useful:
            joined = " ".join(useful)
            return (
                f"Regarding “{sub_question.strip()}”, available evidence indicates: "
                f"{joined}"
            )[:4000]
        snippet = evidence.snippet.strip() or evidence.extract_text[:240].strip()
        return (
            f"Regarding “{sub_question.strip()}”, source material from "
            f"{evidence.domain} suggests: {snippet}"
        )[:4000]
