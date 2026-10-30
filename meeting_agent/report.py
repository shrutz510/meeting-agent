"""Render a stored meeting (transcript + insights) as Markdown."""

from __future__ import annotations

from typing import Any


def _fmt(item: Any) -> str:
    if isinstance(item, dict):
        return "; ".join(f"**{k}**: {_fmt(v)}" for k, v in item.items() if v not in (None, "", [], {}))
    if isinstance(item, list):
        return ", ".join(_fmt(v) for v in item)
    return str(item)


def _section(title: str, value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    out = [f"## {title}", ""]
    if isinstance(value, list):
        out += [f"- {_fmt(v)}" for v in value]
    elif isinstance(value, dict):
        out += [f"- **{k.replace('_', ' ').title()}**: {_fmt(v)}" for k, v in value.items()
                if v not in (None, "", [], {})]
    else:
        out.append(str(value))
    return out + [""]


def _questions_table(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    out = ["## Questions", "", "| Time | Question | Answer | Quality |", "|---|---|---|---|"]
    for q in rows:
        cells = [str(q.get(k, "") or "").replace("|", "\\|").replace("\n", " ")
                 for k in ("timestamp", "question", "answer_summary", "quality")]
        out.append("| " + " | ".join(cells) + " |")
    return out + [""]


def _competencies(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    out = ["## Competencies", ""]
    for c in rows:
        out.append(f"- **{c.get('name')}**: {c.get('rating', '?')}/5")
        for ev in c.get("evidence") or []:
            out.append(f"  - {ev}")
    return out + [""]


def render_markdown(record: dict[str, Any], include_transcript: bool = True) -> str:
    ins = record.get("insights") or {}
    mode = record.get("mode", "general")
    lines = [f"# {ins.get('title') or record.get('title') or record['id']}", ""]
    meta = [f"**Mode:** {mode}", f"**Recorded:** {record.get('created_at', '?')}"]
    if record.get("source"):
        meta.append(f"**Source:** {record['source']}")
    if ins.get("_meta"):
        meta.append(f"**Model:** {ins['_meta'].get('model')}")
    lines += [" · ".join(meta), ""]

    lines += _section("Summary", ins.get("summary"))
    if mode == "interview":
        lines += _section("Candidate profile", ins.get("candidate_profile"))
        lines += _section("Recommendation (advisory)", ins.get("recommendation"))
        lines += _competencies(ins.get("competencies") or [])
        lines += _section("Strengths", ins.get("strengths"))
        lines += _section("Concerns", ins.get("concerns"))
        lines += _questions_table(ins.get("questions") or [])
        lines += _section("Claims to verify", ins.get("claims_to_verify"))
        lines += _section("Follow-up questions", ins.get("follow_up_questions"))
        lines += _section("Questions the candidate asked", ins.get("candidate_questions"))
        lines += _section("Interviewer feedback", ins.get("interviewer_feedback"))
    elif mode == "paper":
        lines += _section("Paper", ins.get("paper"))
        lines += _section("Key points", ins.get("key_points"))
        lines += _section("Strengths noted", ins.get("strengths_noted"))
        lines += _section("Critiques", ins.get("critiques"))
        lines += _section("Disagreements", ins.get("disagreements"))
        lines += _section("Open questions", ins.get("open_questions"))
        lines += _section("Discussion vs. paper", ins.get("claims_vs_paper"))
        lines += _section("Related work mentioned", ins.get("related_work"))
        lines += _section("Ideas and extensions", ins.get("ideas_and_extensions"))
        lines += _section("Action items", ins.get("action_items"))
    else:
        lines += _section("Key points", ins.get("key_points"))
        lines += _section("Decisions", ins.get("decisions"))
        lines += _section("Action items", ins.get("action_items"))
        lines += _section("Open questions", ins.get("open_questions"))

    if include_transcript and record.get("transcript"):
        lines += ["## Transcript", "", "```text", record["transcript"], "```", ""]
    return "\n".join(lines)
