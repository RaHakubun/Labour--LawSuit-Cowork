from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


ALLOWED_MEDIA_TYPES = {
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


@dataclass(frozen=True)
class StoredEvidence:
    evidence_id: UUID
    display_name: str
    storage_key: str
    media_type: str
    sha256: str
    size: int


class LocalEvidenceStorage:
    """Private evidence storage whose keys never contain user-controlled paths."""

    def __init__(self, root: Path, *, max_bytes: int = 20 * 1024 * 1024) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes

    async def save(self, *, display_name: str, media_type: str, content: bytes) -> StoredEvidence:
        normalized_type = media_type.split(";", 1)[0].strip().lower()
        if normalized_type not in ALLOWED_MEDIA_TYPES:
            raise ValueError(f"unsupported evidence media type: {normalized_type}")
        expected_suffix = ALLOWED_MEDIA_TYPES[normalized_type]
        supplied_suffix = Path(display_name).suffix.lower()
        if supplied_suffix and supplied_suffix != expected_suffix:
            raise ValueError(
                f"evidence extension {supplied_suffix} does not match media type "
                f"{normalized_type}"
            )
        if not content:
            raise ValueError("evidence file is empty")
        if len(content) > self.max_bytes:
            raise ValueError(f"evidence exceeds {self.max_bytes} byte limit")
        evidence_id = uuid4()
        suffix = expected_suffix
        storage_key = f"{evidence_id.hex}{suffix}"
        safe_name = re.sub(r"[\x00-\x1f/\\]+", "_", display_name).strip()[:240]
        safe_name = safe_name.replace("..", "_")
        if not safe_name:
            safe_name = f"evidence{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path_for(storage_key)
        await asyncio.to_thread(target.write_bytes, content)
        return StoredEvidence(
            evidence_id=evidence_id,
            display_name=safe_name,
            storage_key=storage_key,
            media_type=normalized_type,
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
        )

    def path_for(self, storage_key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}\.(txt|csv|json|pdf|docx)", storage_key):
            raise ValueError("invalid evidence storage key")
        target = (self.root / storage_key).resolve()
        if target.parent != self.root:
            raise ValueError("evidence storage key escapes root")
        return target

    async def delete(self, storage_key: str) -> None:
        target = self.path_for(storage_key)
        if target.exists():
            await asyncio.to_thread(target.unlink)
