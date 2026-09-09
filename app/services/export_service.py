"""Report export: Markdown, PDF, DOCX (Phase 9)."""

from __future__ import annotations

import io
import re
from typing import Optional
from uuid import UUID

from fastapi.responses import Response

from app.core.exceptions import ConflictError, ForbiddenError, ValidationAppError
from app.core.logging import get_logger
from app.repositories.reports_repository import ReportsRepository, reports_repository
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.usage import ExportFormat
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)


class ExportService:
    def __init__(
        self,
        runs: RunService | None = None,
        reports: ReportsRepository | None = None,
        sources: SourcesRepository | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._reports = reports or reports_repository
        self._sources = sources or sources_repository

    def export(
        self,
        run_id: UUID,
        *,
        format: ExportFormat | str,
        user_id: Optional[UUID] = None,
    ) -> Response:
        run = self._authorized_run(run_id, user_id=user_id)
        fmt = ExportFormat(format) if isinstance(format, str) else format
        report = self._reports.find_for_run(run.id)
        if report is None or not (report.markdown or "").strip():
            raise ConflictError(
                "No report available to export. Generate a report first.",
                detail={"run_id": str(run_id)},
            )

        markdown = report.markdown
        safe_topic = re.sub(r"[^a-zA-Z0-9_-]+", "_", run.topic)[:60] or "report"
        filename_base = f"researchflow_{safe_topic}_{str(run.id)[:8]}"

        if fmt == ExportFormat.md:
            return Response(
                content=markdown.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename_base}.md"'
                },
            )
        if fmt == ExportFormat.pdf:
            data = self._build_pdf(title=run.topic, markdown=markdown)
            return Response(
                content=data,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename_base}.pdf"'
                },
            )
        if fmt == ExportFormat.docx:
            data = self._build_docx(title=run.topic, markdown=markdown)
            return Response(
                content=data,
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                headers={
                    "Content-Disposition": f'attachment; filename="{filename_base}.docx"'
                },
            )
        raise ValidationAppError(
            "Unsupported export format",
            detail={"format": str(format), "allowed": [f.value for f in ExportFormat]},
        )

    def _build_pdf(self, *, title: str, markdown: str) -> bytes:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            ListFlowable,
            ListItem,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=0.85 * inch,
            rightMargin=0.85 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title=title[:200],
        )
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("RFH1", parent=styles["Heading1"], spaceAfter=12, fontSize=18)
        h2 = ParagraphStyle("RFH2", parent=styles["Heading2"], spaceBefore=14, spaceAfter=8)
        body = ParagraphStyle("RFBody", parent=styles["BodyText"], spaceAfter=8, leading=14)
        story: list = []

        for block in self._parse_markdown_blocks(markdown):
            kind = block["kind"]
            text = self._escape_xml(block["text"])
            if kind == "h1":
                story.append(Paragraph(text, h1))
            elif kind == "h2":
                story.append(Paragraph(text, h2))
            elif kind == "li":
                story.append(
                    ListFlowable(
                        [ListItem(Paragraph(text, body))],
                        bulletType="bullet",
                        leftIndent=18,
                    )
                )
            elif kind == "p":
                story.append(Paragraph(text, body))
            elif kind == "spacer":
                story.append(Spacer(1, 8))

        if not story:
            story.append(Paragraph(self._escape_xml(title), h1))
            story.append(Paragraph("(Empty report)", body))

        doc.build(story)
        return buffer.getvalue()

    def _build_docx(self, *, title: str, markdown: str) -> bytes:
        from docx import Document

        document = Document()
        document.core_properties.title = title[:200]

        for block in self._parse_markdown_blocks(markdown):
            kind = block["kind"]
            text = block["text"]
            if kind == "h1":
                document.add_heading(text, level=1)
            elif kind == "h2":
                document.add_heading(text, level=2)
            elif kind == "li":
                document.add_paragraph(text, style="List Bullet")
            elif kind == "p":
                document.add_paragraph(text)

        if not document.paragraphs:
            document.add_heading(title, level=1)
            document.add_paragraph("(Empty report)")

        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    def _parse_markdown_blocks(self, markdown: str) -> list[dict[str, str]]:
        blocks: list[dict[str, str]] = []
        for raw_line in markdown.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                blocks.append({"kind": "spacer", "text": ""})
                continue
            if line.startswith("# "):
                blocks.append({"kind": "h1", "text": line[2:].strip()})
            elif line.startswith("## "):
                blocks.append({"kind": "h2", "text": line[3:].strip()})
            elif line.startswith("### "):
                blocks.append({"kind": "h2", "text": line[4:].strip()})
            elif re.match(r"^[-*]\s+", line):
                blocks.append({"kind": "li", "text": re.sub(r"^[-*]\s+", "", line).strip()})
            elif line.startswith("> "):
                blocks.append({"kind": "p", "text": line[2:].strip()})
            else:
                # Strip simple markdown emphasis for safe plain export
                cleaned = re.sub(r"[*_`]+", "", line)
                blocks.append({"kind": "p", "text": cleaned})
        return blocks

    @staticmethod
    def _escape_xml(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


export_service = ExportService()
