"""
services/news_api.py — Advanced layered query expansion for article generation.

Provides an alternative multi-layer query expansion strategy that produces
more diverse and targeted search queries compared to the simple expand_query()
in ai.py. Uses a 3-layer approach:

  1. Universal layer    — entities, temporal context, paraphrases
  2. Per-section layer  — targeted queries for each article section
  3. Consolidation layer— deduplication and limiting

This module is NOT imported by the main application by default. It serves
as an upgrade path for expand_query() in ai.py when more granular query
control is needed.
"""

import json
from datetime import datetime
from typing import Any

from core.logger import logger_ai
from core.exceptions import AIServiceError


def layered_query_expansion(topic: str, llm: Any) -> dict[str, list[str]]:
    """
    Expand a topic into structured search queries across article sections.

    Produces a dictionary with keys ``universal``, ``latar_belakang``,
    ``ringkasan``, ``tokoh``, ``konflik``, and ``prediksi``, each
    containing a list of search queries tailored to that section.

    The expansion happens in three layers:

    **Layer 1 — Universal**:
      Ask the LLM to extract entity names, temporal variants, and
      paraphrases of the topic.  These are broad-coverage queries
      useful across all article sections.

    **Layer 2 — Per-section**:
      Ask the LLM to generate focused queries for each journalistic
      section (background, summary, figures, conflict, prediction).

    **Layer 3 — Consolidation**:
      Merge universal and per-section queries into a single flat list,
      remove duplicates and single-word entries, then re-split into
      the structured dictionary (first 5 universal, plus all
      per-section queries).

    Args:
        topic:  Article topic / main subject.
        llm:    LangChain-compatible LLM instance used for generation.
                Must support ``ainvoke()`` with a plain-text prompt.

    Returns:
        Dictionary mapping section names to lists of search query strings::

            {
                "universal":      ["Gaza March 2026", ...],
                "latar_belakang": ["history of Israel-Palestine", ...],
                "ringkasan":      ["current situation Gaza", ...],
                "tokoh":          ["netanyahu hamas leaders", ...],
                "konflik":        ["military strikes Gaza", ...],
                "prediksi":       ["Gaza conflict resolution 2026", ...],
            }

    Raises:
        AIServiceError: If LLM response cannot be parsed as JSON.
    """
    month_year = datetime.now().strftime("%B %Y")

    # ── Layer 1: Universal queries ────────────────────────────────────────
    universal_prompt = f"""
Topik: "{topic}"
Bulan sekarang: {month_year}
Buat tiga kategori query pencarian dalam JSON:
- "entities": sinonim, alias, atau istilah terkait topik
- "temporal": kombinasi topik dengan konteks waktu (bulan/tahun)
- "paraphrases": variasi bahasa Inggris/Indonesia dari topik
Balas HANYA dengan JSON: {{"entities":[], "temporal":[], "paraphrases":[]}}
"""
    universal = _parse_llm_json(llm.invoke(universal_prompt))

    # ── Layer 2: Per-section queries ──────────────────────────────────────
    section_prompt = f"""
Topik: "{topic}"
Buat sub-query pencarian untuk tiap seksi artikel dalam JSON:
- "latar_belakang": query untuk sejarah dan konteks
- "ringkasan":      query untuk situasi terkini
- "tokoh":          query untuk tokoh dan organisasi terkait
- "konflik":        query untuk peristiwa dan dinamika
- "prediksi":       query untuk analisis dan proyeksi masa depan
Balas HANYA dengan JSON. Tiap nilai adalah list of strings.
"""
    per_section = _parse_llm_json(llm.invoke(section_prompt))

    # ── Layer 3: Consolidation ────────────────────────────────────────────
    all_queries_flat: list[str] = (
        universal.get("entities", [])
        + universal.get("temporal", [])
        + universal.get("paraphrases", [])
        + [q for section_queries in per_section.values() for q in section_queries]
    )

    seen: set[str] = set()
    deduped: list[str] = []
    for q in all_queries_flat:
        key = q.lower().strip()
        if key not in seen and len(key.split()) > 1:
            seen.add(key)
            deduped.append(q)

    result: dict[str, list[str]] = {
        "universal": deduped[:5],
        **per_section,
    }

    logger_ai.debug(
        "layered_query_expansion: %d universal + %d section queries "
        "generated for '%s'",
        len(result["universal"]),
        sum(len(v) for k, v in result.items() if k != "universal"),
        topic[:50],
    )

    return result


def _parse_llm_json(response: Any) -> dict[str, Any]:
    """
    Parse an LLM response object into a JSON dictionary.

    Handles both LangChain ``AIMessage`` objects and raw strings.
    Raises ``AIServiceError`` if parsing fails.

    Args:
        response: LLM response (``AIMessage`` with ``.content`` or raw str).

    Returns:
        Parsed JSON dictionary.

    Raises:
        AIServiceError: If the content cannot be parsed as JSON.
    """
    text = response.content if hasattr(response, "content") else str(response)
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        import re

        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger_ai.error(
            "Failed to parse LLM JSON response: %s. Raw: %.200s",
            exc,
            text,
        )
        raise AIServiceError(
            "Gagal memproses data dari LLM: response bukan JSON valid"
        ) from exc
