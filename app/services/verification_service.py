"""Verification service — Verifier Agent orchestration (Phase 8)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.agents.verifier_agent import VerifierAgent, verifier_agent
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.repositories.findings_repository import FindingsRepository, findings_repository
from app.repositories.reports_repository import ReportsRepository, reports_repository
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.agent_event import AgentEventType, AgentRole
from app.schemas.verification import (
    ConflictListResponse,
    SectionConfidenceLabel,
    VerificationResponse,
    VerifyResultResponse,
)
from app.services.agent_event_service import AgentEventService, agent_event_service
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)

VERIFIER_NAME = "verifier-01"


class VerificationService:
    def __init__(
        self,
        runs: RunService | None = None,
        reports: ReportsRepository | None = None,
        findings: FindingsRepository | None = None,
        sources: SourcesRepository | None = None,
        verifier: VerifierAgent | None = None,
        events: AgentEventService | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._reports = reports or reports_repository
        self._findings = findings or findings_repository
        self._sources = sources or sources_repository
        self._verifier = verifier or verifier_agent
        self._events = events or agent_event_service

    def verify_run(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> VerifyResultResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        report = self._reports.get_for_run(run.id)

        if not (report.markdown or "").strip() and not report.sections_json:
            raise ConflictError(
                "Cannot verify an empty report. Generate a report first.",
                detail={"run_id": str(run_id)},
            )

        findings = self._findings.list_for_run(run.id)
        sources = {s.id: s for s in self._sources.list_for_run(run.id)}
        citation_map: dict[int, UUID] = {}
        for key, value in (report.citation_map_json or {}).items():
            try:
                citation_map[int(key)] = UUID(str(value))
            except (TypeError, ValueError):
                continue

        self._emit(
            run.id,
            AgentEventType.verifier_started,
            {
                "message": "Starting report verification",
                "report_id": str(report.id),
            },
        )
        try:
            bundle = self._verifier.verify(
                markdown=report.markdown,
                sections_json=list(report.sections_json or []),
                findings=findings,
                sources=sources,
                citation_map=citation_map,
            )

            now = datetime.now(timezone.utc)
            confidence_json = {
                str(s.section_id or s.section_title): {
                    "title": s.section_title,
                    "confidence": s.confidence.value,
                    "verified_claims": s.verified_claims,
                    "unverified_claims": s.unverified_claims,
                    "supporting_source_ids": s.supporting_source_ids,
                    "notes": s.notes,
                }
                for s in bundle.sections
            }
            unverified_json = [
                {
                    "claim": u.claim,
                    "section_id": str(u.section_id) if u.section_id else None,
                    "section_title": u.section_title,
                    "reason": u.reason,
                    "suggested_evidence_gap": u.suggested_evidence_gap,
                }
                for u in bundle.unverified_claims
            ]
            conflicts_json = [
                c.model_dump(mode="json") for c in bundle.conflicts
            ]
            verification_json = {
                "status": "completed",
                "overall_confidence": bundle.overall_confidence.value,
                "summary": bundle.summary,
                "repetitions": [r.model_dump(mode="json") for r in bundle.repetitions],
                "sections": [s.model_dump(mode="json") for s in bundle.sections],
            }

            updated = self._reports.update_for_run(
                run.id,
                {
                    "sections_confidence_json": confidence_json,
                    "unverified_claims_json": unverified_json,
                    "conflicts_json": conflicts_json,
                    "verification_json": verification_json,
                    "verified_at": now.isoformat(),
                },
            )

            # Do not silently remove unsupported claims from markdown.
            response = VerificationResponse(
                run_id=run.id,
                report_id=updated.id,
                status="completed",
                verified_at=updated.verified_at or now,
                overall_confidence=bundle.overall_confidence,
                sections=bundle.sections,
                unverified_claims=bundle.unverified_claims,
                conflicts=bundle.conflicts,
                repetitions=bundle.repetitions,
                summary=bundle.summary,
            )
            self._emit(
                run.id,
                AgentEventType.verifier_finished,
                {
                    "success": True,
                    "overall_confidence": bundle.overall_confidence.value,
                    **bundle.summary,
                },
            )
            return VerifyResultResponse(verification=response, report_updated=True)
        except Exception as exc:
            self._emit(
                run.id,
                AgentEventType.error,
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            self._emit(
                run.id,
                AgentEventType.verifier_finished,
                {"success": False},
            )
            raise

    def get_verification(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> VerificationResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        report = self._reports.get_for_run(run.id)
        if not report.verification_json:
            raise NotFoundError(
                "Verification not found for this run. Run POST /verify first.",
                detail={"run_id": str(run_id)},
            )
        return self._to_verification(run.id, report)

    def get_conflicts(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> ConflictListResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        report = self._reports.get_for_run(run.id)
        items = []
        if report.conflicts_json:
            from app.schemas.verification import SourceConflict

            for raw in report.conflicts_json:
                if isinstance(raw, dict):
                    items.append(SourceConflict.model_validate(raw))
        elif report.verification_json:
            # Fallback empty if verify never stored conflicts separately
            items = []
        return ConflictListResponse(items=items, total=len(items))

    def _to_verification(self, run_id: UUID, report) -> VerificationResponse:
        from app.schemas.verification import (
            RepeatedContent,
            SectionVerification,
            SourceConflict,
            UnverifiedClaim,
        )

        v = report.verification_json or {}
        sections = [
            SectionVerification.model_validate(s)
            for s in (v.get("sections") or [])
            if isinstance(s, dict)
        ]
        unverified = [
            UnverifiedClaim.model_validate(u)
            for u in (report.unverified_claims_json or [])
            if isinstance(u, dict) and "reason" in u
        ]
        # Backward compat: writer-only unverified entries without reason
        for u in report.unverified_claims_json or []:
            if isinstance(u, dict) and "reason" not in u and u.get("claim"):
                unverified.append(
                    UnverifiedClaim(
                        claim=str(u["claim"]),
                        section_id=UUID(str(u["section_id"]))
                        if u.get("section_id")
                        else None,
                        section_title=u.get("section_title"),
                        reason="Flagged during writing (not yet re-verified)",
                        suggested_evidence_gap="Re-run verification after research refresh",
                    )
                )

        conflicts = [
            SourceConflict.model_validate(c)
            for c in (report.conflicts_json or [])
            if isinstance(c, dict)
        ]
        repetitions = [
            RepeatedContent.model_validate(r)
            for r in (v.get("repetitions") or [])
            if isinstance(r, dict)
        ]
        overall_raw = v.get("overall_confidence") or "medium"
        try:
            overall = SectionConfidenceLabel(overall_raw)
        except ValueError:
            overall = SectionConfidenceLabel.medium

        return VerificationResponse(
            run_id=run_id,
            report_id=report.id,
            status=str(v.get("status") or "completed"),
            verified_at=report.verified_at,
            overall_confidence=overall,
            sections=sections,
            unverified_claims=unverified,
            conflicts=conflicts,
            repetitions=repetitions,
            summary=dict(v.get("summary") or {}),
        )

    def _emit(
        self,
        run_id: UUID,
        event_type: AgentEventType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._events.emit(
                run_id=run_id,
                agent_name=VERIFIER_NAME,
                agent_role=AgentRole.verifier,
                event_type=event_type,
                payload=payload or {},
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to emit verifier event type=%s", event_type.value)

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


verification_service = VerificationService()
