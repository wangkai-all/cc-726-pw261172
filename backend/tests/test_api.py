"""End-to-end API tests driven through the FastAPI ASGI stack."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import store

client = TestClient(app)

SAMPLE_DIR = "/workspace/samples"


@pytest.fixture(autouse=True)
def _empty_store():
    # The module-level store is process-global; isolate each test.
    store._items.clear()
    yield
    store._items.clear()


def _upload(name: str) -> str:
    with open(f"{SAMPLE_DIR}/{name}", "rb") as fh:
        resp = client.post(
            "/api/datasets",
            files={"file": (name, fh.read(), "application/octet-stream")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["dataset_id"]


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_upload_and_browse_repeated_sample():
    ds = _upload("sample_repeated_subfields.mrc")
    summary = client.get(f"/api/datasets/{ds}").json()
    assert summary["record_count"] == 1
    detail = client.get(f"/api/datasets/{ds}/records/1").json()
    tags = [f["tag"] for f in detail["fields"]]
    assert tags == ["001", "245", "700", "700"]
    field_245 = next(f for f in detail["fields"] if f["tag"] == "245")
    b_subs = [s for s in field_245["subfields"] if s["code"] == "b"]
    assert len(b_subs) == 2
    assert all("segments" in s for s in field_245["subfields"])


def test_raw_bytes_endpoint_roundtrips():
    ds = _upload("sample_multilingual.mrc")
    resp = client.get(f"/api/datasets/{ds}/records/1/raw")
    assert resp.status_code == 200
    assert resp.content.endswith(b"\x1d")
    assert resp.content[:5].decode("ascii").isdigit()


def test_preview_reflects_default_scheme_with_subtable_relation():
    ds = _upload("sample_repeated_subfields.mrc")
    preview = client.get(f"/api/datasets/{ds}/preview/1").json()
    main = preview["expanded"]["main"]
    assert main["record_no"] == 1
    assert "sub_700" in preview["expanded"]["subtables"]
    assert len(preview["expanded"]["subtables"]["sub_700"]) == 2


def test_scheme_edit_and_live_preview_then_persists():
    ds = _upload("sample_repeated_subfields.mrc")
    schemes = client.get(f"/api/datasets/{ds}/schemes").json()
    scheme = schemes["schemes"][0]["scheme"]
    # Disable every subtable to test editing round-trip.
    for st in scheme["subtables"]:
        st["enabled"] = False
    resp = client.put(
        f"/api/datasets/{ds}/schemes/trimmed", json={"scheme": scheme}
    )
    assert resp.status_code == 200
    preview = client.get(f"/api/datasets/{ds}/preview/1").json()
    assert preview["expanded"]["subtables"] == {}
    assert "tag_245" in preview["expanded"]["main"]


def test_invalid_scheme_rejected():
    ds = _upload("sample_repeated_subfields.mrc")
    resp = client.put(
        f"/api/datasets/{ds}/schemes/bad",
        json={"scheme": {"main": [{"target": "1x"}], "subtables": []}},
    )
    assert resp.status_code == 422


def test_live_draft_preview_does_not_persist_scheme():
    ds = _upload("sample_repeated_subfields.mrc")
    before = {s["name"] for s in client.get(f"/api/datasets/{ds}/schemes").json()["schemes"]}
    schemes = client.get(f"/api/datasets/{ds}/schemes").json()
    draft = schemes["schemes"][0]["scheme"]
    for st in draft["subtables"]:
        st["enabled"] = False
    resp = client.post(f"/api/datasets/{ds}/preview/1", json={"scheme": draft})
    assert resp.status_code == 200
    assert resp.json()["expanded"]["subtables"] == {}
    after = {s["name"] for s in client.get(f"/api/datasets/{ds}/schemes").json()["schemes"]}
    assert before == after  # no leaked temporary scheme


def test_draft_preview_validates_scheme():
    ds = _upload("sample_repeated_subfields.mrc")
    resp = client.post(
        f"/api/datasets/{ds}/preview/1",
        json={"scheme": {"main": [{"target": "1bad"}], "subtables": []}},
    )
    assert resp.status_code == 422


def test_export_sqlite_is_downloadable_and_queryable():
    import sqlite3

    ds = _upload("sample_repeated_subfields.mrc")
    ticket = client.post(
        f"/api/datasets/{ds}/export", json={"format": "sqlite"}
    ).json()
    resp = client.get(ticket["download_path"])
    assert resp.status_code == 200
    # Load the downloaded bytes into an in-memory database.
    con = sqlite3.connect(":memory:")
    con.deserialize(resp.content)
    try:
        rows = con.execute(
            "SELECT record_no, record_byte_offset FROM records"
        ).fetchall()
        assert rows == [(1, 0)]
        assert con.execute(
            "SELECT COUNT(*) FROM sub_700"
        ).fetchone()[0] == 2
    finally:
        con.close()


def test_export_csv_zip_and_jsonl():
    ds = _upload("sample_multilingual.mrc")
    for fmt, member in [("csv", None), ("jsonl", None)]:
        ticket = client.post(
            f"/api/datasets/{ds}/export", json={"format": fmt}
        ).json()
        resp = client.get(ticket["download_path"])
        assert resp.status_code == 200
        if fmt == "csv":
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            assert "main.csv" in zf.namelist()
        else:
            obj = json.loads(resp.content.decode("utf-8").splitlines()[0])
            assert obj["_source"]["record_no"] == 1


def test_corrupt_chain_reports_fatal_issue_and_skips_invalid_on_export():
    ds = _upload("sample_corrupt_chain.mrc")
    summary = client.get(f"/api/datasets/{ds}").json()
    assert summary["status_counts"] == {"ok": 4, "recovered": 0, "invalid": 4}
    assert summary["file_issues"][0]["level"] == "fatal"
    ticket = client.post(
        f"/api/datasets/{ds}/export", json={"format": "jsonl"}
    ).json()
    assert ticket["main_row_count"] == 4
    assert len(ticket["skipped_records"]) == 4
    # Invalid record preview is refused with 409.
    bad_no = next(
        r["record_no"] for r in summary["records"] if r["status"] == "invalid"
    )
    resp = client.get(f"/api/datasets/{ds}/preview/{bad_no}")
    assert resp.status_code == 409


def test_unknown_dataset_404():
    assert client.get("/api/datasets/nope").status_code == 404


def test_empty_upload_rejected():
    resp = client.post(
        "/api/datasets",
        files={"file": ("empty.mrc", b"", "application/octet-stream")},
    )
    assert resp.status_code == 400
