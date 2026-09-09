"""Verifier Agent — report quality checks against findings/sources (Phase 8)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID, uuid4

from app.core.logging import get_logger
from app.schemas.finding import FindingResponse
from app.schemas.source import SourceResponse
from app.schemas.verification import (
    RepeatedContent,
    SectionConfidenceLabel,
    SectionVerification,
    SourceConflict,
    UnverifiedClaim,
)

logger = get_logger(__name__)

_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "are",
    "was", "were", "been", "being", "into", "than", "then", "also", "only",
    "over", "under", "about", "after", "before", "between", "through", "their",
    "there", "these", "those", "which", "while", "where", "when", "what",
}


@dataclass
class VerificationBundle:
    sections: list[SectionVerification] = field(default_factory=list)
    unverified_claims: list[UnverifiedClaim] = field(default_factory=list)
    conflicts: list[SourceConflict] = field(default_factory=list)
    repetitions: list[RepeatedContent] = field(default_factory=list)
    overall_confidence: SectionConfidenceLabel = SectionConfidenceLabel.medium
    summary: dict[str, Any] = field(default_factory=dict)


class VerifierAgent:
    """
    Deterministic verifier:
    1) factual claims vs findings
    2) unsupported claims
    3) conflicting sources
    4) repetition
    5) section confidence (high|medium|low)

    Does not delete unsupported claims — flags them with reason + evidence gap.
    """

    def verify(
        self,
        *,
        markdown: str,
        sections_json: list[dict[str, Any]],
        findings: list[FindingResponse],
        sources: dict[UUID, SourceResponse],
        citation_map: dict[int, UUID],
    ) -> VerificationBundle:
        section_blocks = self._section_blocks(markdown, sections_json)
        findings_by_section: dict[str, list[FindingResponse]] = {}
        for finding in findings:
            key = str(finding.section_id)
            findings_by_section.setdefault(key, []).append(finding)

        unverified: list[UnverifiedClaim] = []
        section_results: list[SectionVerification] = []
        all_claim_sentences: list[tuple[str, str, Optional[str]]] = []
        # (sentence, section_title, section_id)

        for block in section_blocks:
            sid = block.get("section_id")
            title = str(block.get("title") or "Section")
            body = str(block.get("markdown") or "")
            section_findings = findings_by_section.get(str(sid), []) if sid else []
            # Also match findings by title fallback when ids differ
            if not section_findings:
                section_findings = [
                    f
                    for f in findings
                    if title.lower() in (getattr(f, "claim", "") or "").lower()
                ][:0]  # don't invent — keep empty

            sentences = self._claim_sentences(body)
            verified_count = 0
            unverified_count = 0
            supporting: set[str] = set()
            notes: list[str] = []

            gap_findings = [f for f in section_findings if f.is_gap]
            evidence_findings = [f for f in section_findings if not f.is_gap]

            if not section_findings:
                notes.append("No findings linked to this section")
            if gap_findings and not evidence_findings:
                notes.append("Only research gaps recorded for this section")

            for sentence in sentences:
                all_claim_sentences.append((sentence, title, str(sid) if sid else None))
                citation_ids = self._citations_in_text(sentence)
                missing_citations = [
                    n for n in citation_ids if n not in citation_map
                ]
                matched = self._match_finding(sentence, evidence_findings)

                if missing_citations:
                    unverified_count += 1
                    unverified.append(
                        UnverifiedClaim(
                            claim=sentence,
                            section_id=UUID(str(sid)) if sid else None,
                            section_title=title,
                            reason=(
                                "Citation marker(s) "
                                f"{missing_citations} are not mapped to stored sources"
                            ),
                            suggested_evidence_gap=(
                                "Add or correct citations that reference persisted "
                                "source IDs for this claim"
                            ),
                        )
                    )
                    continue

                if matched is not None:
                    verified_count += 1
                    if matched.source_id:
                        supporting.add(str(matched.source_id))
                    for n in citation_ids:
                        src = citation_map.get(n)
                        if src:
                            supporting.add(str(src))
                    continue

                # Cited but not aligned to findings, or bare claim with no support
                if citation_ids:
                    # Citations exist in map but claim not grounded in findings text
                    for n in citation_ids:
                        src = citation_map.get(n)
                        if src:
                            supporting.add(str(src))
                    # Still require finding alignment for "verified"
                    unverified_count += 1
                    unverified.append(
                        UnverifiedClaim(
                            claim=sentence,
                            section_id=UUID(str(sid)) if sid else None,
                            section_title=title,
                            reason=(
                                "Claim is cited but does not align with stored findings "
                                "for this section"
                            ),
                            suggested_evidence_gap=(
                                "Capture a finding that substantiates this claim, "
                                "or revise the claim to match existing findings"
                            ),
                        )
                    )
                else:
                    # No citation and no finding match
                    if self._looks_factual(sentence):
                        unverified_count += 1
                        unverified.append(
                            UnverifiedClaim(
                                claim=sentence,
                                section_id=UUID(str(sid)) if sid else None,
                                section_title=title,
                                reason="No matching finding or source citation supports this claim",
                                suggested_evidence_gap=(
                                    f"Research additional sources for: {sentence[:180]}"
                                ),
                            )
                        )
                    else:
                        # Soft narrative / transition — ignore
                        pass

            # Missing sources entirely for section with claims
            if sentences and not supporting and evidence_findings:
                # Findings exist but report didn't cite/use them well
                notes.append("Findings exist but report claims lack clear source linkage")
            if sentences and not supporting and not evidence_findings:
                notes.append("Missing sources for section claims")

            confidence = self._section_confidence(
                verified=verified_count,
                unverified=unverified_count,
                evidence_findings=len(evidence_findings),
                gap_findings=len(gap_findings),
                supporting_sources=len(supporting),
            )
            section_results.append(
                SectionVerification(
                    section_id=UUID(str(sid)) if sid else None,
                    section_title=title,
                    confidence=confidence,
                    verified_claims=verified_count,
                    unverified_claims=unverified_count,
                    supporting_source_ids=sorted(supporting),
                    notes=notes,
                )
            )

        conflicts = self._detect_conflicts(findings, sources, section_blocks)
        repetitions = self._detect_repetitions(all_claim_sentences)
        overall = self._overall_confidence(section_results)

        summary = {
            "sections_checked": len(section_results),
            "verified_claims": sum(s.verified_claims for s in section_results),
            "unverified_claims": len(unverified),
            "conflicts": len(conflicts),
            "repetitions": len(repetitions),
            "overall_confidence": overall.value,
        }
        return VerificationBundle(
            sections=section_results,
            unverified_claims=unverified,
            conflicts=conflicts,
            repetitions=repetitions,
            overall_confidence=overall,
            summary=summary,
        )

    def _section_blocks(
        self, markdown: str, sections_json: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if sections_json:
            return [
                {
                    "section_id": b.get("section_id"),
                    "title": b.get("title") or "Section",
                    "markdown": b.get("markdown") or "",
                }
                for b in sections_json
                if isinstance(b, dict)
            ]
        # Fallback: parse ## headings from markdown (skip exec/conclusion/sources)
        blocks: list[dict[str, Any]] = []
        parts = re.split(r"\n##\s+", markdown)
        for part in parts[1:]:
            lines = part.strip().splitlines()
            if not lines:
                continue
            title = lines[0].strip()
            if title.lower() in {"executive summary", "conclusion", "sources"}:
                continue
            blocks.append(
                {
                    "section_id": None,
                    "title": title,
                    "markdown": "\n".join(lines[1:]).strip(),
                }
            )
        return blocks

    def _claim_sentences(self, text: str) -> list[str]:
        cleaned = re.sub(r">\s*Research gaps:.*", "", text, flags=re.I | re.S)
        cleaned = re.sub(r"^#+\s+.*$", "", cleaned, flags=re.M)
        parts = re.split(r"(?<=[.!?])\s+", cleaned.strip())
        out: list[str] = []
        for part in parts:
            s = part.strip()
            if len(s) < 40:
                continue
            if s.startswith(">") or s.startswith("-") and len(s) < 60:
                continue
            out.append(s)
        return out[:40]

    def _looks_factual(self, sentence: str) -> bool:
        lower = sentence.lower()
        hedges = ("may ", "might ", "could ", "possibly", "suggests that further")
        if any(h in lower for h in hedges) and len(sentence) < 80:
            return False
        # Prefer sentences with numbers, named entities-ish tokens, or strong verbs
        if re.search(r"\d", sentence):
            return True
        strong = ("is ", "are ", "was ", "were ", "shows ", "found ", "reports ", "increases ", "reduces ")
        return any(v in lower for v in strong)

    def _citations_in_text(self, text: str) -> list[int]:
        return [int(x) for x in re.findall(r"\[(\d+)\]", text)]

    def _tokens(self, text: str) -> set[str]:
        return {
            t
            for t in re.findall(r"[a-z0-9]{4,}", text.lower())
            if t not in _STOP
        }

    def _jaccard(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _match_finding(
        self, sentence: str, findings: list[FindingResponse]
    ) -> Optional[FindingResponse]:
        sent_tokens = self._tokens(sentence)
        best: Optional[FindingResponse] = None
        best_score = 0.0
        for finding in findings:
            score = self._jaccard(sent_tokens, self._tokens(finding.claim))
            if score > best_score:
                best_score = score
                best = finding
        if best is not None and best_score >= 0.28:
            return best
        return None

    def _section_confidence(
        self,
        *,
        verified: int,
        unverified: int,
        evidence_findings: int,
        gap_findings: int,
        supporting_sources: int,
    ) -> SectionConfidenceLabel:
        total = verified + unverified
        if evidence_findings == 0 and supporting_sources == 0:
            return SectionConfidenceLabel.low
        if total == 0:
            return (
                SectionConfidenceLabel.medium
                if evidence_findings > 0
                else SectionConfidenceLabel.low
            )
        ratio = verified / total
        if ratio >= 0.7 and supporting_sources >= 1 and gap_findings == 0:
            return SectionConfidenceLabel.high
        if ratio >= 0.4 and (supporting_sources >= 1 or evidence_findings >= 1):
            return SectionConfidenceLabel.medium
        return SectionConfidenceLabel.low

    def _overall_confidence(
        self, sections: list[SectionVerification]
    ) -> SectionConfidenceLabel:
        if not sections:
            return SectionConfidenceLabel.low
        score = {
            SectionConfidenceLabel.high: 3,
            SectionConfidenceLabel.medium: 2,
            SectionConfidenceLabel.low: 1,
        }
        avg = sum(score[s.confidence] for s in sections) / len(sections)
        if avg >= 2.5:
            return SectionConfidenceLabel.high
        if avg >= 1.75:
            return SectionConfidenceLabel.medium
        return SectionConfidenceLabel.low

    def _detect_conflicts(
        self,
        findings: list[FindingResponse],
        sources: dict[UUID, SourceResponse],
        section_blocks: list[dict[str, Any]],
    ) -> list[SourceConflict]:
        conflicts: list[SourceConflict] = []
        by_section: dict[str, list[FindingResponse]] = {}
        for finding in findings:
            if finding.is_gap or finding.source_id is None:
                continue
            by_section.setdefault(str(finding.section_id), []).append(finding)

        title_by_id = {
            str(b.get("section_id")): str(b.get("title") or "Section")
            for b in section_blocks
        }

        negation_pairs = (
            ("increase", "decrease"),
            ("increases", "decreases"),
            ("higher", "lower"),
            ("improve", "worsen"),
            ("improves", "worsens"),
            ("safe", "unsafe"),
            ("effective", "ineffective"),
            ("supports", "opposes"),
            ("benefit", "harm"),
            ("positive", "negative"),
        )

        for section_id, items in by_section.items():
            if len(items) < 2:
                continue
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    if a.source_id == b.source_id:
                        continue
                    tokens_a = self._tokens(a.claim)
                    tokens_b = self._tokens(b.claim)
                    overlap = self._jaccard(tokens_a, tokens_b)
                    if overlap < 0.2:
                        continue
                    lower_a = a.claim.lower()
                    lower_b = b.claim.lower()
                    disagreed = False
                    for pos, neg in negation_pairs:
                        if (pos in lower_a and neg in lower_b) or (
                            neg in lower_a and pos in lower_b
                        ):
                            disagreed = True
                            break
                    # Explicit conflict marker in either claim
                    if "conflict" in lower_a or "conflict" in lower_b:
                        disagreed = True
                    if "disagree" in lower_a or "disagree" in lower_b:
                        disagreed = True
                    if not disagreed:
                        continue

                    src_a = sources.get(a.source_id) if a.source_id else None
                    src_b = sources.get(b.source_id) if b.source_id else None
                    conflicts.append(
                        SourceConflict(
                            id=str(uuid4()),
                            section_id=UUID(section_id) if section_id else None,
                            section_title=title_by_id.get(section_id),
                            statement=(
                                "Credible sources present opposing claims on related evidence"
                            ),
                            source_a={
                                "source_id": str(a.source_id) if a.source_id else None,
                                "name": (src_a.title if src_a else "Source A"),
                                "url": (src_a.url if src_a else None),
                                "value": a.claim,
                            },
                            source_b={
                                "source_id": str(b.source_id) if b.source_id else None,
                                "name": (src_b.title if src_b else "Source B"),
                                "url": (src_b.url if src_b else None),
                                "value": b.claim,
                            },
                            explanation=(
                                "Findings linked to different sources disagree. "
                                "The verifier does not choose a single fact; both "
                                "positions are retained for reviewer judgment."
                            ),
                            finding_ids=[str(a.id), str(b.id)],
                        )
                    )
        return conflicts

    def _detect_repetitions(
        self, sentences: list[tuple[str, str, Optional[str]]]
    ) -> list[RepeatedContent]:
        reps: list[RepeatedContent] = []
        for i in range(len(sentences)):
            for j in range(i + 1, len(sentences)):
                text_a, sec_a, _ = sentences[i]
                text_b, sec_b, _ = sentences[j]
                if sec_a == sec_b:
                    continue
                sim = self._jaccard(self._tokens(text_a), self._tokens(text_b))
                if sim >= 0.72:
                    reps.append(
                        RepeatedContent(
                            text_a=text_a,
                            text_b=text_b,
                            section_a=sec_a,
                            section_b=sec_b,
                            similarity=round(sim, 3),
                        )
                    )
        # Cap
        return reps[:20]


verifier_agent = VerifierAgent()
