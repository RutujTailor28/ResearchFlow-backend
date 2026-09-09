"""Report generation service — Writer Agent orchestration (Phase 7)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID, uuid4

from app.agents.writer_agent import WriterAgent, WrittenSection, writer_agent
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.core.run_control import RunAborted, StopReason, run_control
from app.repositories.findings_repository import FindingsRepository, findings_repository
from app.repositories.plan_sections_repository import (
    PlanSectionsRepository,
    plan_sections_repository,
)
from app.repositories.reports_repository import ReportsRepository, reports_repository
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.agent_event import AgentEventType, AgentRole
from app.schemas.finding import FindingResponse
from app.schemas.plan import SectionStatus
from app.schemas.report import (
    RegenerateSectionRequest,
    ReportGenerateResponse,
    ReportResponse,
    UpdateReportRequest,
)
from app.schemas.run import RunStatus
from app.schemas.section import SectionResponse
from app.schemas.source import SourceResponse
from app.services.agent_event_service import AgentEventService, agent_event_service
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)

WRITER_NAME = "writer-01"
REPORTABLE_STATUSES = {
    RunStatus.completed,
    RunStatus.partial,
    RunStatus.approved,  # allow draft write if research not finished but findings exist
    RunStatus.running,
}


class ReportService:
    def __init__(
        self,
        runs: RunService | None = None,
        sections: PlanSectionsRepository | None = None,
        findings: FindingsRepository | None = None,
        sources: SourcesRepository | None = None,
        reports: ReportsRepository | None = None,
        writer: WriterAgent | None = None,
        events: AgentEventService | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._sections = sections or plan_sections_repository
        self._findings = findings or findings_repository
        self._sources = sources or sources_repository
        self._reports = reports or reports_repository
        self._writer = writer or writer_agent
        self._events = events or agent_event_service

    def get_report(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> ReportResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        return self._reports.get_for_run(run.id)

    def update_report(
        self,
        run_id: UUID,
        payload: UpdateReportRequest,
        *,
        user_id: Optional[UUID] = None,
    ) -> ReportResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        self._reports.get_for_run(run.id)
        return self._reports.update_for_run(
            run.id,
            {"markdown": payload.markdown},
        )

    def generate_report(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> ReportGenerateResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        if run.status not in REPORTABLE_STATUSES and run.status != RunStatus.plan_ready:
            # Still allow if there are findings (partial research path).
            findings_probe = self._findings.list_for_run(run.id)
            if not findings_probe and run.status not in {
                RunStatus.completed,
                RunStatus.partial,
            }:
                raise ConflictError(
                    f"Cannot generate report when run status is '{run.status.value}'",
                    detail={"run_id": str(run_id), "status": run.status.value},
                )

        sections = self._sections.list_records_for_run(run.id)
        if not sections:
            raise ValidationAppError(
                "Cannot generate report without plan sections",
                detail={"run_id": str(run_id)},
            )

        all_findings = self._findings.list_for_run(run.id)
        all_sources = {s.id: s for s in self._sources.list_for_run(run.id)}
        citation_map = self._build_citation_map(all_findings, all_sources)

        self._emit(
            run.id,
            AgentEventType.writer_started,
            {"message": "Starting full report generation", "sections": len(sections)},
        )

        written: list[WrittenSection] = []
        unverified: list[dict[str, Any]] = []
        gaps_total = 0
        tokens_in = tokens_out = 0
        cost = 0.0
        stop_reason: str | None = None

        run_control.begin(
            run.id,
            cost_limit_usd=run.cost_limit_usd,
        )
        if run.cost_usd:
            run_control.add_cost(run.id, float(run.cost_usd))

        try:
            for section in sections:
                try:
                    run_control.assert_can_continue(
                        run.id,
                        db_cancel=self._runs.is_cancel_requested(run.id),
                    )
                except RunAborted as exc:
                    stop_reason = exc.reason.value
                    logger.info(
                        "Report generation stopped run_id=%s reason=%s",
                        run.id,
                        stop_reason,
                    )
                    break
                block, ti, to, c = self._write_one_section(
                    run=run,
                    section=section,
                    all_findings=all_findings,
                    all_sources=all_sources,
                    citation_map=citation_map,
                    instructions=None,
                )
                written.append(block)
                tokens_in += ti
                tokens_out += to
                cost += c
                gaps_total += len(block.gap_notes)
                for claim in block.unverified_claims:
                    unverified.append(
                        {
                            "section_id": str(section.id),
                            "claim": claim,
                        }
                    )
                try:
                    self._sections.update_fields(
                        run_id=run.id,
                        section_id=section.id,
                        fields={"status": SectionStatus.completed.value},
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("Could not mark section completed id=%s", section.id)

            # Executive summary + conclusion (no fabricated citations).
            summaries = [
                {
                    "title": w.title,
                    "excerpt": w.markdown[:400],
                    "confidence": w.confidence,
                    "gaps": w.gap_notes,
                }
                for w in written
            ]
            exec_md = ""
            conclusion_md = ""
            if written and stop_reason is None:
                try:
                    run_control.assert_can_continue(run.id)
                except RunAborted as exc:
                    stop_reason = exc.reason.value
            if written and stop_reason is None:
                try:
                    self._emit(
                        run.id,
                        AgentEventType.section_started,
                        {"section": "executive_summary"},
                    )
                    exec_block, exec_result = self._writer.write_executive_summary(
                        topic=run.topic,
                        audience=str(run.audience),
                        tone=str(run.tone),
                        section_summaries=summaries,
                    )
                    ti, to, c = self._usage(exec_result.usage)
                    tokens_in += ti
                    tokens_out += to
                    cost += c
                    exec_md = exec_block.markdown
                    self._emit(
                        run.id,
                        AgentEventType.section_completed,
                        {
                            "section": "executive_summary",
                            "model": exec_result.model,
                        },
                        tokens_in=ti,
                        tokens_out=to,
                        cost_usd=c,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Executive summary failed: %s", type(exc).__name__)
                    exec_md = (
                        f"This report examines **{run.topic}** across "
                        f"{len(written)} sections based on available research findings."
                    )

            if written and stop_reason is None:
                try:
                    self._emit(
                        run.id,
                        AgentEventType.section_started,
                        {"section": "conclusion"},
                    )
                    all_gaps = [g for w in written for g in w.gap_notes]
                    concl, concl_result = self._writer.write_conclusion(
                        topic=run.topic,
                        audience=str(run.audience),
                        tone=str(run.tone),
                        section_summaries=summaries,
                        gap_notes=all_gaps,
                    )
                    ti, to, c = self._usage(concl_result.usage)
                    tokens_in += ti
                    tokens_out += to
                    cost += c
                    conclusion_md = concl.markdown
                    self._emit(
                        run.id,
                        AgentEventType.section_completed,
                        {
                            "section": "conclusion",
                            "model": concl_result.model,
                        },
                        tokens_in=ti,
                        tokens_out=to,
                        cost_usd=c,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Conclusion failed: %s", type(exc).__name__)
                    conclusion_md = (
                        "Further research may be needed where findings were incomplete."
                    )
            elif written and not exec_md:
                exec_md = (
                    f"This report examines **{run.topic}** across "
                    f"{len(written)} sections based on available research findings."
                )
                conclusion_md = (
                    "Further research may be needed where findings were incomplete."
                )

            if not written:
                self._emit(
                    run.id,
                    AgentEventType.writer_finished,
                    {
                        "success": False,
                        "stop_reason": stop_reason or "no_sections_written",
                    },
                )
                raise ConflictError(
                    "Report generation stopped before any section was written",
                    detail={
                        "run_id": str(run.id),
                        "stop_reason": stop_reason,
                    },
                )

            markdown = self._assemble_markdown(
                title=run.topic,
                executive_summary=exec_md or "",
                sections=written,
                conclusion=conclusion_md or "",
                citation_map=citation_map,
                all_sources=all_sources,
            )
            confidence_json = {
                str(w.section_id): {
                    "title": w.title,
                    "confidence": w.confidence,
                    "citation_indices": w.citation_indices,
                }
                for w in written
            }
            sections_json = [self._section_dict(w) for w in written]
            report = self._persist(
                run.id,
                markdown=markdown,
                sections_json=sections_json,
                sections_confidence_json=confidence_json,
                unverified_claims_json=unverified,
                citation_map_json={str(k): str(v) for k, v in citation_map.items()},
            )
            if stop_reason in {
                StopReason.cost_limit.value,
                StopReason.timeout.value,
            }:
                try:
                    self._runs.update_run_status(
                        run.id,
                        RunStatus.partial,
                        error=f"Report stopped: {stop_reason}",
                        stop_reason=stop_reason,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("Could not mark run partial after report abort")
            spent = run_control.current_cost(run.id)
            if spent > 0:
                try:
                    self._runs.update_fields(run.id, {"cost_usd": spent})
                except Exception:  # noqa: BLE001
                    pass
            self._emit(
                run.id,
                AgentEventType.writer_finished,
                {
                    "success": stop_reason is None,
                    "sections_written": len(written),
                    "citations_used": len(citation_map),
                    "gaps_noted": gaps_total,
                    "stop_reason": stop_reason,
                    "partial": stop_reason is not None,
                },
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            )
            return ReportGenerateResponse(
                report=report,
                sections_written=len(written),
                citations_used=len(
                    {i for w in written for i in w.citation_indices}
                ),
                gaps_noted=gaps_total,
            )
        except Exception as exc:
            self._emit(
                run.id,
                AgentEventType.error,
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            self._emit(
                run.id,
                AgentEventType.writer_finished,
                {"success": False},
            )
            raise
        finally:
            run_control.end(run.id)

    def generate_section(
        self,
        run_id: UUID,
        section_id: UUID,
        *,
        user_id: Optional[UUID] = None,
        instructions: str | None = None,
    ) -> ReportResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        section = self._sections.get_for_run(run.id, section_id)
        all_findings = self._findings.list_for_run(run.id)
        all_sources = {s.id: s for s in self._sources.list_for_run(run.id)}
        citation_map = self._build_citation_map(all_findings, all_sources)

        self._emit(
            run.id,
            AgentEventType.writer_started,
            {
                "message": "Generating single section",
                "section_id": str(section_id),
            },
        )
        try:
            written, _, _, _ = self._write_one_section(
                run=run,
                section=section,
                all_findings=all_findings,
                all_sources=all_sources,
                citation_map=citation_map,
                instructions=instructions,
            )
            report = self._merge_section_into_report(
                run=run,
                written=written,
                citation_map=citation_map,
                all_sources=all_sources,
            )
            try:
                self._sections.update_fields(
                    run_id=run.id,
                    section_id=section.id,
                    fields={"status": SectionStatus.completed.value},
                )
            except Exception:  # noqa: BLE001
                pass
            self._emit(
                run.id,
                AgentEventType.writer_finished,
                {"success": True, "section_id": str(section_id)},
            )
            return report
        except Exception as exc:
            self._emit(
                run.id,
                AgentEventType.error,
                {"error": f"{type(exc).__name__}: {exc}", "section_id": str(section_id)},
            )
            self._emit(
                run.id,
                AgentEventType.writer_finished,
                {"success": False, "section_id": str(section_id)},
            )
            raise

    def regenerate_section(
        self,
        run_id: UUID,
        section_id: UUID,
        payload: RegenerateSectionRequest | None = None,
        *,
        user_id: Optional[UUID] = None,
    ) -> ReportResponse:
        instructions = payload.instructions if payload else None
        # Ensure a report exists or create scaffold from this section only.
        existing = self._reports.find_for_run(run_id)
        if existing is None:
            return self.generate_section(
                run_id, section_id, user_id=user_id, instructions=instructions
            )
        return self.generate_section(
            run_id, section_id, user_id=user_id, instructions=instructions
        )

    def _write_one_section(
        self,
        *,
        run,
        section: SectionResponse,
        all_findings: list[FindingResponse],
        all_sources: dict[UUID, SourceResponse],
        citation_map: dict[int, UUID],
        instructions: str | None,
    ) -> tuple[WrittenSection, int, int, float]:
        self._emit(
            run.id,
            AgentEventType.section_started,
            {
                "section_id": str(section.id),
                "title": section.title,
            },
        )
        try:
            self._sections.update_fields(
                run_id=run.id,
                section_id=section.id,
                fields={"status": SectionStatus.writing.value},
            )
        except Exception:  # noqa: BLE001
            pass

        section_findings = [f for f in all_findings if f.section_id == section.id]
        findings_payload, sources_payload, allowed = self._section_context(
            section_findings, all_sources, citation_map
        )

        written, result = self._writer.write_section(
            topic=run.topic,
            audience=str(run.audience),
            tone=str(run.tone),
            section_id=section.id,
            section_title=section.title,
            section_description=section.description or "",
            sub_questions=list(section.sub_questions or []),
            findings=findings_payload,
            sources=sources_payload,
            instructions=instructions,
            allowed_citation_indices=allowed,
        )
        ti, to, c = self._usage(result.usage)
        self._emit(
            run.id,
            AgentEventType.section_completed,
            {
                "section_id": str(section.id),
                "title": written.title,
                "confidence": written.confidence,
                "citations": written.citation_indices,
                "gaps": written.gap_notes,
                "model": result.model,
            },
            tokens_in=ti,
            tokens_out=to,
            cost_usd=c,
        )
        return written, ti, to, c

    def _section_context(
        self,
        section_findings: list[FindingResponse],
        all_sources: dict[UUID, SourceResponse],
        citation_map: dict[int, UUID],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
        # Reverse map source_id -> citation index
        source_to_idx = {sid: idx for idx, sid in citation_map.items()}
        findings_payload: list[dict[str, Any]] = []
        used_indices: set[int] = set()

        for finding in section_findings:
            if finding.is_gap:
                findings_payload.append(
                    {
                        "is_gap": True,
                        "claim": finding.claim,
                        "confidence": finding.confidence,
                    }
                )
                continue
            citation_index = None
            if finding.source_id and finding.source_id in source_to_idx:
                citation_index = source_to_idx[finding.source_id]
                used_indices.add(citation_index)
            findings_payload.append(
                {
                    "is_gap": False,
                    "claim": finding.claim,
                    "confidence": finding.confidence,
                    "citation_index": citation_index,
                    "source_id": str(finding.source_id) if finding.source_id else None,
                }
            )

        sources_payload: list[dict[str, Any]] = []
        for idx in sorted(used_indices):
            source = all_sources.get(citation_map[idx])
            if source is None:
                continue
            sources_payload.append(
                {
                    "citation_index": idx,
                    "source_id": str(source.id),
                    "title": source.title,
                    "url": source.url,
                    "domain": source.domain,
                    "quality_score": source.quality_score,
                }
            )
        return findings_payload, sources_payload, used_indices

    @staticmethod
    def _build_citation_map(
        findings: list[FindingResponse],
        sources: dict[UUID, SourceResponse],
    ) -> dict[int, UUID]:
        """Stable 1-based citation indices for real stored sources only."""
        ordered: list[UUID] = []
        seen: set[UUID] = set()
        for finding in findings:
            if finding.is_gap or finding.source_id is None:
                continue
            if finding.source_id not in sources:
                continue
            if finding.source_id in seen:
                continue
            seen.add(finding.source_id)
            ordered.append(finding.source_id)
        return {i: sid for i, sid in enumerate(ordered, start=1)}

    def _merge_section_into_report(
        self,
        *,
        run,
        written: WrittenSection,
        citation_map: dict[int, UUID],
        all_sources: dict[UUID, SourceResponse],
    ) -> ReportResponse:
        existing = self._reports.find_for_run(run.id)
        sections_list: list[dict[str, Any]] = []
        if existing and isinstance(existing.sections_json, list):
            sections_list = list(existing.sections_json)

        replaced = False
        for i, block in enumerate(sections_list):
            if str(block.get("section_id")) == str(written.section_id):
                sections_list[i] = self._section_dict(written)
                replaced = True
                break
        if not replaced:
            sections_list.append(self._section_dict(written))

        # Keep plan order when possible.
        order = {
            str(s.id): s.order_index
            for s in self._sections.list_records_for_run(run.id)
        }
        sections_list.sort(
            key=lambda b: order.get(str(b.get("section_id")), 10_000)
        )

        confidence = dict(existing.sections_confidence_json or {}) if existing else {}
        confidence[str(written.section_id)] = {
            "title": written.title,
            "confidence": written.confidence,
            "citation_indices": written.citation_indices,
        }

        unverified = list(existing.unverified_claims_json or []) if existing else []
        # Drop prior unverified claims for this section, then append fresh.
        unverified = [
            u
            for u in unverified
            if str((u or {}).get("section_id")) != str(written.section_id)
        ]
        for claim in written.unverified_claims:
            unverified.append(
                {"section_id": str(written.section_id), "claim": claim}
            )

        written_objs = [
            WrittenSection(
                section_id=UUID(str(b["section_id"])),
                title=str(b.get("title") or "Section"),
                markdown=str(b.get("markdown") or ""),
                confidence=float(b.get("confidence") or 0.5),
                citation_indices=list(b.get("citation_indices") or []),
                gap_notes=list(b.get("gap_notes") or []),
            )
            for b in sections_list
            if b.get("section_id")
        ]

        exec_md = ""
        conclusion_md = ""
        if existing and existing.markdown:
            exec_md = self._extract_named_block(existing.markdown, "Executive Summary")
            conclusion_md = self._extract_named_block(existing.markdown, "Conclusion")

        markdown = self._assemble_markdown(
            title=run.topic,
            executive_summary=exec_md
            or f"Draft report on **{run.topic}** (section update in progress).",
            sections=written_objs,
            conclusion=conclusion_md
            or "Conclusion will be refreshed on full report generation.",
            citation_map=citation_map,
            all_sources=all_sources,
        )
        return self._persist(
            run.id,
            markdown=markdown,
            sections_json=sections_list,
            sections_confidence_json=confidence,
            unverified_claims_json=unverified,
            citation_map_json={str(k): str(v) for k, v in citation_map.items()},
            report_id=existing.id if existing else None,
        )

    def _persist(
        self,
        run_id: UUID,
        *,
        markdown: str,
        sections_json: list[dict[str, Any]],
        sections_confidence_json: dict[str, Any],
        unverified_claims_json: list[Any],
        citation_map_json: dict[str, Any],
        report_id: UUID | None = None,
    ) -> ReportResponse:
        row = {
            "id": str(report_id or uuid4()),
            "run_id": str(run_id),
            "markdown": markdown,
            "sections_json": sections_json,
            "sections_confidence_json": sections_confidence_json,
            "unverified_claims_json": unverified_claims_json,
            "citation_map_json": citation_map_json,
        }
        # Preserve existing id on upsert by run_id when present.
        existing = self._reports.find_for_run(run_id)
        if existing is not None:
            row["id"] = str(existing.id)
        return self._reports.upsert(row)

    @staticmethod
    def _section_dict(written: WrittenSection) -> dict[str, Any]:
        return {
            "section_id": str(written.section_id),
            "title": written.title,
            "markdown": written.markdown,
            "confidence": written.confidence,
            "citation_indices": written.citation_indices,
            "gap_notes": written.gap_notes,
            "unverified_claims": written.unverified_claims,
        }

    @staticmethod
    def _assemble_markdown(
        *,
        title: str,
        executive_summary: str,
        sections: list[WrittenSection],
        conclusion: str,
        citation_map: dict[int, UUID],
        all_sources: dict[UUID, SourceResponse],
    ) -> str:
        parts: list[str] = [f"# {title}", ""]
        if executive_summary.strip():
            parts.extend(["## Executive Summary", "", executive_summary.strip(), ""])
        for section in sections:
            parts.extend(
                [f"## {section.title}", "", section.markdown.strip(), ""]
            )
            if section.gap_notes:
                parts.append("> Research gaps:")
                for gap in section.gap_notes:
                    parts.append(f"> - {gap}")
                parts.append("")
        if conclusion.strip():
            parts.extend(["## Conclusion", "", conclusion.strip(), ""])

        parts.extend(["## Sources", ""])
        if not citation_map:
            parts.append("_No verified sources were linked to findings._")
        else:
            for idx in sorted(citation_map):
                source = all_sources.get(citation_map[idx])
                if source is None:
                    continue
                parts.append(
                    f"[{idx}] {source.title or source.domain} — {source.url} "
                    f"(source_id: `{source.id}`)"
                )
        parts.append("")
        return "\n".join(parts).strip() + "\n"

    @staticmethod
    def _extract_named_block(markdown: str, heading: str) -> str:
        marker = f"## {heading}"
        if marker not in markdown:
            return ""
        after = markdown.split(marker, 1)[1]
        # Stop at next ## heading
        chunks = after.split("\n## ")
        body = chunks[0]
        return body.strip()

    def _emit(
        self,
        run_id: UUID,
        event_type: AgentEventType,
        payload: dict[str, Any] | None = None,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        try:
            self._events.emit(
                run_id=run_id,
                agent_name=WRITER_NAME,
                agent_role=AgentRole.writer,
                event_type=event_type,
                payload=payload or {},
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to emit writer event type=%s", event_type.value)

    @staticmethod
    def _usage(usage: dict[str, Any] | None) -> tuple[int, int, float]:
        if not usage:
            return 0, 0, 0.0
        return (
            int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            float(usage.get("cost") or usage.get("total_cost") or 0.0),
        )

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


report_service = ReportService()
