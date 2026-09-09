"""Transparent heuristic source quality scoring."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class QualityBreakdown:
    score: float
    reasons: list[str]


_GOV_SUFFIXES = (".gov", ".gov.uk", ".gc.ca", ".europa.eu")
_EDU_SUFFIXES = (".edu", ".ac.uk", ".edu.au")
_ORG_HINTS = (
    "who.int",
    "unicef.org",
    "oecd.org",
    "worldbank.org",
    "imf.org",
    "nasa.gov",
    "nih.gov",
    "cdc.gov",
    "fda.gov",
    "ieee.org",
    "acm.org",
)
_ACADEMIC_HINTS = (
    "arxiv.org",
    "ssrn.com",
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
)
_REPUTABLE_PUBS = (
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "nytimes.com",
    "theguardian.com",
    "ft.com",
    "economist.com",
    "wsj.com",
)
_DOCS_HINTS = (
    "docs.",
    "developer.",
    "developers.",
    "learn.microsoft.com",
    "cloud.google.com",
    "aws.amazon.com",
    "kubernetes.io",
)
_LOW_TRUST_HINTS = (
    "blogspot.",
    "wordpress.com",
    "medium.com",
    "buzzfeed.",
    "clickhole.",
    "content-farm",
    "listicle",
)


def score_source_quality(
    *,
    url: str,
    domain: str,
    title: str,
    extract_text: str,
) -> QualityBreakdown:
    """
    Heuristic score 0–100. Transparent and evidence-based on URL/domain signals.
    Does not claim authority without supporting domain evidence.
    """
    score = 40.0
    reasons: list[str] = ["Base score for a successfully fetched page"]
    host = (domain or urlparse(url).netloc).lower().removeprefix("www.")

    def bump(points: float, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{reason} ({points:+.0f})")

    if any(host.endswith(sfx) or host == sfx.lstrip(".") for sfx in _GOV_SUFFIXES) or host.endswith(".gov"):
        bump(30, "Government domain")
    elif any(host.endswith(sfx) or sfx in host for sfx in _EDU_SUFFIXES) or host.endswith(".edu"):
        bump(25, "Academic/education domain")
    elif any(h in host for h in _ORG_HINTS):
        bump(22, "Recognized official organization domain")
    elif any(h in host for h in _ACADEMIC_HINTS):
        bump(20, "Academic/research publication venue")
    elif any(h in host for h in _REPUTABLE_PUBS):
        bump(12, "Reputable news publication")
    elif any(h in host or h in url for h in _DOCS_HINTS):
        bump(15, "Official documentation-style host")
    elif host.endswith(".org"):
        bump(8, "Organization domain (.org) — verify independence")

    if url.lower().endswith(".pdf") or "/pdf" in url.lower():
        bump(5, "PDF/document URL pattern")

    words = len(extract_text.split()) if extract_text else 0
    if words >= 800:
        bump(8, "Substantial extracted text length")
    elif words >= 250:
        bump(4, "Moderate extracted text length")
    elif words < 80:
        bump(-10, "Very short extracted text")

    title_l = (title or "").lower()
    if any(k in title_l for k in ("sponsored", "advertorial", "buy now")):
        bump(-15, "Promotional title signals")

    if any(h in host for h in _LOW_TRUST_HINTS):
        bump(-18, "Low-trust / content-farm style host")

    # HTTPS is baseline hygiene, not authority.
    if urlparse(url).scheme == "https":
        bump(3, "HTTPS transport")

    score = max(0.0, min(100.0, round(score, 2)))
    reasons.append(f"Final heuristic score={score}")
    return QualityBreakdown(score=score, reasons=reasons)
