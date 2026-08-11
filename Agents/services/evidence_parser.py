from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from Agents.domain.case_state import (
    EvidenceExtraction,
    EvidenceItem,
    EvidenceStatus,
    FactItem,
    FactSource,
    FactStatus,
    utc_now,
)
from Agents.services.ocr import VisionOcrAdapter
from Agents.services.tool_hub import ToolExecutionError


@dataclass(frozen=True)
class EvidenceParseResult:
    evidence: EvidenceItem
    extraction: EvidenceExtraction
    candidate_facts: tuple[FactItem, ...]


class EvidenceParser:
    def __init__(
        self,
        *,
        path_resolver: Callable[[str], Path],
        text_writer: Callable[[UUID, str], Awaitable[str]],
        vision_ocr: VisionOcrAdapter | None = None,
    ) -> None:
        self._path_resolver = path_resolver
        self._text_writer = text_writer
        self._vision_ocr = vision_ocr

    async def parse(self, evidence: EvidenceItem) -> EvidenceParseResult:
        path = self._path_resolver(evidence.storage_key)
        raw = await asyncio.to_thread(path.read_bytes)
        text, parser_name, page_count = await self._extract_text(
            raw,
            evidence.media_type,
        )
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not normalized:
            raise ValueError("evidence parser produced empty text")
        text_ref = await self._text_writer(evidence.evidence_id, normalized)
        extraction = EvidenceExtraction(
            evidence_id=evidence.evidence_id,
            text_ref=text_ref,
            content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            parser_name=parser_name,
            character_count=len(normalized),
            page_count=page_count,
        )
        parsed = evidence.model_copy(
            update={
                "status": EvidenceStatus.PARSED,
                "extracted_text_ref": text_ref,
                "parser_name": parser_name,
                "parsed_at": utc_now(),
            }
        )
        facts = tuple(self._extract_candidate_facts(normalized, str(evidence.evidence_id)))
        return EvidenceParseResult(
            evidence=parsed,
            extraction=extraction,
            candidate_facts=facts,
        )

    async def _extract_text(
        self,
        raw: bytes,
        media_type: str,
    ) -> tuple[str, str, int | None]:
        if media_type in {"image/png", "image/jpeg"}:
            return await self._run_ocr([raw], media_type)
        text, parser_name, page_count = await asyncio.to_thread(
            self._extract_embedded_text,
            raw,
            media_type,
        )
        if text.strip() or media_type != "application/pdf":
            return text, parser_name, page_count
        images = await asyncio.to_thread(self._render_pdf_pages, raw)
        return await self._run_ocr(images, "image/png")

    async def _run_ocr(
        self,
        images: list[bytes],
        media_type: str,
    ) -> tuple[str, str, int]:
        if self._vision_ocr is None:
            raise ToolExecutionError(
                "visual OCR is required for image or scanned PDF evidence",
                retryable=False,
            )
        result = await self._vision_ocr.recognize(images=images, media_type=media_type)
        if not result.text.strip():
            raise ToolExecutionError(
                "visual OCR returned empty text",
                retryable=False,
            )
        return result.text, result.provider, result.page_count

    def _extract_embedded_text(
        self,
        raw: bytes,
        media_type: str,
    ) -> tuple[str, str, int | None]:
        if media_type in {"text/plain", "text/csv"}:
            return raw.decode("utf-8-sig"), "utf8-text", None
        if media_type == "application/json":
            payload = json.loads(raw.decode("utf-8-sig"))
            return json.dumps(payload, ensure_ascii=False, indent=2), "json", None
        if media_type == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages), "pypdf", len(reader.pages)
        if media_type.endswith("wordprocessingml.document"):
            from docx import Document

            document = Document(io.BytesIO(raw))
            return (
                "\n".join(paragraph.text for paragraph in document.paragraphs),
                "python-docx",
                None,
            )
        raise ValueError(f"unsupported evidence media type: {media_type}")

    def _render_pdf_pages(self, raw: bytes) -> list[bytes]:
        try:
            import fitz  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("PyMuPDF is required for scanned PDF OCR") from exc
        document = fitz.open(stream=raw, filetype="pdf")
        return [page.get_pixmap(dpi=180, alpha=False).tobytes("png") for page in document]

    def _extract_candidate_facts(self, text: str, evidence_id: str):
        patterns = (
            (
                "employment.monthly_wage",
                r"(?:月工资|工资(?:标准)?)[：:\s]*(?:人民币)?\s*([0-9,]+(?:\.\d+)?)\s*元?",
                "number",
            ),
            (
                "employment.start_date",
                r"(?:入职日期|入职时间|参加工作时间)[：:\s]*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?)",
                "text",
            ),
            (
                "termination.date",
                r"(?:解除日期|辞退日期|解除时间)[：:\s]*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?)",
                "text",
            ),
            (
                "parties.employer",
                r"(?:用人单位|甲方|公司名称)[：:\s]*([^\n，,。]{2,80})",
                "text",
            ),
            (
                "parties.employee",
                r"(?:劳动者|乙方|员工姓名)[：:\s]*([^\n，,。]{2,40})",
                "text",
            ),
        )
        for fact_id, pattern, value_type in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            raw_value = match.group(1).strip()
            value = (
                float(raw_value.replace(",", ""))
                if value_type == "number"
                else raw_value
            )
            yield FactItem(
                fact_id=fact_id,
                value=value,
                status=FactStatus.PENDING_VERIFICATION,
                source=FactSource(kind="evidence", ref_id=evidence_id),
                confidence=0.9,
                derivation=f"从证据原文中按字段标签抽取：{match.group(0)[:160]}",
            )
