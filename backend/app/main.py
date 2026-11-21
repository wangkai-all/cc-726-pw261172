"""FastAPI application: upload ISO 2709, inspect records, edit mapping, export."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from app.storage import store
from app.marc.mapping import (
    expand_record,
    expand_dataset,
    validate_scheme,
    MappingError,
)
from app.marc.exporter import export
from app.marc.charset import split_marker_segments

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MiB guard

app = FastAPI(
    title="MARC21 Ingest Station",
    version="1.0.0",
    description=(
        "Byte-level ISO 2709 ingestion: inspect fields/indicators/subfields "
        "and raw bytes, edit mapping schemes, export queryable datasets."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_dataset(dataset_id: str):
    try:
        return store.get(dataset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Dataset not found")


def _require_record(item, record_no: int):
    for rec in item.records:
        if rec.record_no == record_no:
            return rec
    raise HTTPException(
        status_code=404, detail=f"Record {record_no} not found in dataset"
    )


def _require_scheme(item, scheme_name: str | None) -> dict:
    name = scheme_name or item.active_scheme
    if name not in item.schemes:
        raise HTTPException(status_code=404, detail=f"Scheme {name!r} not found")
    return item.schemes[name]


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/datasets")
async def upload_dataset(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit",
        )
    item = store.create(file.filename or "upload.mrc", raw)
    return {
        "dataset_id": item.dataset_id,
        "filename": item.filename,
        "size_bytes": len(item.raw),
        "record_count": len(item.records),
        "status_counts": _status_counts(item),
        "file_issues": [i.to_dict() for i in item.file_issues],
        "active_scheme": item.active_scheme,
    }


@app.get("/api/datasets")
def list_datasets() -> dict:
    return {
        "datasets": [
            {
                "dataset_id": d.dataset_id,
                "filename": d.filename,
                "size_bytes": len(d.raw),
                "record_count": len(d.records),
                "status_counts": _status_counts(d),
                "uploaded_at": d.uploaded_at,
                "active_scheme": d.active_scheme,
            }
            for d in store.list_datasets()
        ]
    }


@app.get("/api/datasets/{dataset_id}")
def dataset_summary(dataset_id: str) -> dict:
    item = _require_dataset(dataset_id)
    return {
        "dataset_id": item.dataset_id,
        "filename": item.filename,
        "size_bytes": len(item.raw),
        "record_count": len(item.records),
        "status_counts": _status_counts(item),
        "file_issues": [i.to_dict() for i in item.file_issues],
        "schemes": list(item.schemes.keys()),
        "active_scheme": item.active_scheme,
        "records": [r.to_summary() for r in item.records],
    }


@app.get("/api/datasets/{dataset_id}/records/{record_no}")
def record_detail(dataset_id: str, record_no: int) -> dict:
    item = _require_dataset(dataset_id)
    rec = _require_record(item, record_no)
    detail = rec.to_detail()
    # Pre-split decoded values into text/raw segments for the UI so the
    # browser never has to know the PUA marker convention.
    for field in detail["fields"]:
        if field["field_type"] == "control" and field.get("control_decode"):
            field["control_segments"] = split_marker_segments(
                field["control_decode"]["text"]
            )
        for sf in field.get("subfields", []):
            sf["segments"] = split_marker_segments(sf["decode"]["text"])
    return detail


@app.get("/api/datasets/{dataset_id}/records/{record_no}/raw")
def record_raw(dataset_id: str, record_no: int) -> Response:
    """Return the exact source bytes of one located record."""
    item = _require_dataset(dataset_id)
    rec = _require_record(item, record_no)
    start = rec.byte_offset
    end = start + rec.actual_length if rec.actual_length > 0 else start
    if end > len(item.raw):
        end = len(item.raw)
    return Response(
        content=item.raw[start:end],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="record-{record_no}.mrc"'
            )
        },
    )


@app.get("/api/datasets/{dataset_id}/schemes")
def list_schemes(dataset_id: str) -> dict:
    item = _require_dataset(dataset_id)
    return {
        "active_scheme": item.active_scheme,
        "schemes": [
            {"name": name, "scheme": scheme}
            for name, scheme in item.schemes.items()
        ],
    }


@app.put("/api/datasets/{dataset_id}/schemes/{name}")
def save_scheme(dataset_id: str, name: str, payload: dict = Body(...)) -> dict:
    item = _require_dataset(dataset_id)
    scheme = payload.get("scheme", payload)
    errors = validate_scheme(scheme)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    scheme = dict(scheme)
    scheme.setdefault("name", name)
    activate = bool(payload.get("activate", True))
    store.save_scheme(dataset_id, name, scheme, activate=activate)
    return {"saved": name, "active_scheme": item.active_scheme}


@app.get("/api/datasets/{dataset_id}/preview/{record_no}")
def preview_record(
    dataset_id: str, record_no: int, scheme: str | None = None
) -> dict:
    """Show how one record expands under a saved (or named) scheme."""
    item = _require_dataset(dataset_id)
    rec = _require_record(item, record_no)
    if rec.status == "invalid":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Record is structurally invalid and cannot be mapped",
                "record_no": record_no,
                "issues": [i.to_dict() for i in rec.issues],
            },
        )
    chosen = _require_scheme(item, scheme)
    return {
        "dataset_id": dataset_id,
        "record_no": record_no,
        "scheme_name": scheme or item.active_scheme,
        "expanded": expand_record(rec, chosen),
    }


@app.post("/api/datasets/{dataset_id}/preview/{record_no}")
def preview_record_with_scheme(
    dataset_id: str, record_no: int, payload: dict = Body(...)
) -> dict:
    """Live preview of an unsaved, in-progress scheme draft.

    The draft is validated but never persisted, so editing cannot pollute
    the list of saved mapping schemes.
    """
    item = _require_dataset(dataset_id)
    rec = _require_record(item, record_no)
    if rec.status == "invalid":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Record is structurally invalid and cannot be mapped",
                "record_no": record_no,
                "issues": [i.to_dict() for i in rec.issues],
            },
        )
    draft = payload.get("scheme", payload)
    errors = validate_scheme(draft)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return {
        "dataset_id": dataset_id,
        "record_no": record_no,
        "scheme_name": "(unsaved draft)",
        "expanded": expand_record(rec, draft),
    }


@app.post("/api/datasets/{dataset_id}/export")
def export_dataset(dataset_id: str, payload: dict = Body(default={})) -> dict:
    """Build an export synchronously and return a download ticket path."""
    item = _require_dataset(dataset_id)
    fmt = (payload.get("format") or "sqlite").lower()
    if fmt not in {"sqlite", "jsonl", "csv"}:
        raise HTTPException(status_code=400, detail="Unsupported format")
    scheme = _require_scheme(item, payload.get("scheme"))
    try:
        dataset = expand_dataset(item.records, scheme)
    except MappingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    suffix = {"sqlite": ".sqlite3", "jsonl": ".jsonl", "csv": ".zip"}[fmt]
    out_dir = Path("/tmp/marc_ingest_exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{item.dataset_id}_{item.active_scheme}{suffix}"
    export(dataset, fmt, out_path)
    return {
        "download_path": f"/api/datasets/{dataset_id}/download/{out_path.name}",
        "format": fmt,
        "main_row_count": len(dataset["main_rows"]),
        "subtable_counts": {
            name: len(rows) for name, rows in dataset["subtables"].items()
        },
        "skipped_records": dataset["skipped_records"],
    }


@app.get("/api/datasets/{dataset_id}/download/{filename}")
def download_export(dataset_id: str, filename: str) -> StreamingResponse:
    item = _require_dataset(dataset_id)
    # Path containment: never serve arbitrary /tmp paths.
    safe_name = Path(filename).name
    out_path = Path("/tmp/marc_ingest_exports") / safe_name
    if not out_path.exists() or not safe_name.startswith(item.dataset_id):
        raise HTTPException(status_code=404, detail="Export not found")
    media = {
        ".sqlite3": "application/vnd.sqlite3",
        ".jsonl": "application/x-ndjson",
        ".zip": "application/zip",
    }.get(out_path.suffix, "application/octet-stream")
    data = out_path.read_bytes()
    return StreamingResponse(
        io.BytesIO(data),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(len(data)),
        },
    )


@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict:
    store.delete(dataset_id)
    return {"deleted": dataset_id}


def _status_counts(item) -> dict:
    counts = {"ok": 0, "recovered": 0, "invalid": 0}
    for rec in item.records:
        counts[rec.status] = counts.get(rec.status, 0) + 1
    return counts


# Serve the built Vue SPA when a frontend/dist exists (production layout).
# In development Vite (5173) proxies /api to this app instead.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/assets",
        StaticFiles(directory=str(_DIST / "assets")),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    def index() -> Response:
        return Response(
            content=(_DIST / "index.html").read_bytes(),
            media_type="text/html",
        )
