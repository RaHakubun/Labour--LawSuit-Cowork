from __future__ import annotations

import asyncio
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from Agents.domain.case_state import (
    EvidenceItem,
    EvidenceStatus,
    FactItem,
    FactSource,
    FactStatus,
    utc_now,
)


@dataclass(frozen=True)
class EvidenceParseResult:
    evidence: EvidenceItem
    candidate_facts: tuple[FactItem, ...]


class EvidenceParser:
    def __init__(self, path_resolver) -> None:
        self._path_resolver = path_resolver

    async def parse(self, evidence: EvidenceItem) -> EvidenceParseResult:
        path: Path = self._path_resolver(evidence.storage_key)
        raw = await asyncio.to_thread(path.read_bytes)
        text, parser_name = await asyncio.to_thread(
            self._extract_text,
            raw,
            evidence.media_type,
        )
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not normalized:
            raise ValueError("evidence parser produced empty text")
        parsed = evidence.model_copy(
            update={
                "status": EvidenceStatus.PARSED,
                "extracted_text_ref": f"evidence://{evidence.evidence_id}/text",
                "extracted_text": normalized[:200_000],
                "parser_name": parser_name,
                "parsed_at": utc_now(),
            }
        )
        facts = tuple(self._extract_candidate_facts(normalized, str(evidence.evidence_id)))
        return EvidenceParseResult(evidence=parsed, candidate_facts=facts)

    def _extract_text(self, raw: bytes, media_type: str) -> tuple[str, str]:
        if media_type in {"text/plain", "text/csv"}:
            return raw.decode("utf-8-sig"), "utf8-text"
        if media_type == "application/json":
            payload = json.loads(raw.decode("utf-8-sig"))
            return json.dumps(payload, ensure_ascii=False, indent=2), "json"
        if media_type == "application/pdf":
            from pypdf import PdfReader

            pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages]
            return "\n".join(pages), "pypdf"
        if media_type.endswith("wordprocessingml.document"):
            from docx import Document

            document = Document(io.BytesIO(raw))
            return "\n".join(paragraph.text for paragraph in document.paragraphs), "python-docx"
        raise ValueError(f"unsupported evidence media type: {media_type}")

    def _extract_candidate_facts(self, text: str, evidence_id: str):
        patterns = (
            ("employment.monthly_wage", r"(?:月工资|工资(?:标准)?)[：:\s]*(?:人民币)?\s*([0-9,]+(?:\.\d+)?)\s*元?", "number"),
            ("employment.start_date", r"(?:入职日期|入职时间|参加工作时间)[：:\s]*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?)", "text"),
            ("termination.date", r"(?:解除日期|辞退日期|解除时间)[：:\s]*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?)", "text"),
            ("parties.employer", r"(?:用人单位|甲方|公司名称)[：:\s]*([^\n，,。]{2,80})", "text"),
            ("parties.employee", r"(?:劳动者|乙方|员工姓名)[：:\s]*([^\n，,。]{2,40})", "text"),
        )
        for fact_id, pattern, value_type in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            raw_value = match.group(1).strip()
            value = float(raw_value.replace(",", "")) if value_type == "number" else raw_value
            yield FactItem(
                fact_id=fact_id,
                value=value,
                status=FactStatus.PENDING_VERIFICATION,
                source=FactSource(kind="evidence", ref_id=evidence_id),
                confidence=0.9,
                derivation=f"从证据原文中按字段标签抽取：{match.group(0)[:160]}",
            )
