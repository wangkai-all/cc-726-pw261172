"""In-process storage for uploaded files, parsed records and schemes."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock

from app.marc.iso2709 import ParsedRecord, ParseIssue, parse_records
from app.marc.mapping import default_scheme


@dataclass
class UploadedDataset:
    dataset_id: str
    filename: str
    raw: bytes
    records: list[ParsedRecord]
    file_issues: list[ParseIssue]
    uploaded_at: float
    schemes: dict[str, dict] = field(default_factory=dict)
    active_scheme: str | None = None


class DatasetStore:
    """Thread-safe in-memory store; enough for a single-node workstation."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[str, UploadedDataset] = {}

    def create(self, filename: str, raw: bytes) -> UploadedDataset:
        records, file_issues = parse_records(raw)
        dataset_id = uuid.uuid4().hex[:12]
        item = UploadedDataset(
            dataset_id=dataset_id,
            filename=filename,
            raw=raw,
            records=records,
            file_issues=file_issues,
            uploaded_at=time.time(),
        )
        scheme = default_scheme(records, name="default")
        item.schemes["default"] = scheme
        item.active_scheme = "default"
        with self._lock:
            self._items[dataset_id] = item
        return item

    def get(self, dataset_id: str) -> UploadedDataset:
        with self._lock:
            item = self._items.get(dataset_id)
        if item is None:
            raise KeyError(dataset_id)
        return item

    def list_datasets(self) -> list[UploadedDataset]:
        with self._lock:
            return list(self._items.values())

    def save_scheme(
        self, dataset_id: str, name: str, scheme: dict, activate: bool = False
    ) -> UploadedDataset:
        item = self.get(dataset_id)
        with self._lock:
            item.schemes[name] = scheme
            if activate or item.active_scheme is None:
                item.active_scheme = name
        return item

    def delete(self, dataset_id: str) -> None:
        with self._lock:
            self._items.pop(dataset_id, None)


store = DatasetStore()
