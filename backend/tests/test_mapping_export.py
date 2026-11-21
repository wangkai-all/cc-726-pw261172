"""Tests for the mapping engine and dataset export."""

import json
import sqlite3

import pytest

from app.marc.iso2709 import parse_file
from app.marc.mapping import (
    default_scheme,
    expand_record,
    expand_dataset,
    validate_scheme,
)
from app.marc.exporter import export


@pytest.fixture()
def parsed():
    recs, issues = parse_file(
        "/workspace/samples/sample_repeated_subfields.mrc"
    )
    assert issues == []
    return recs


def test_default_scheme_covers_every_tag(parsed):
    scheme = default_scheme(parsed)
    errors = validate_scheme(scheme)
    assert errors == []
    mapped_tags = {m["tag"] for m in scheme["main"]} | {
        st["tag"] for st in scheme["subtables"]
    }
    present = {f.tag for rec in parsed for f in rec.fields}
    assert present <= mapped_tags


def test_repeated_subfields_expand_as_ordered_arrays(parsed):
    scheme = default_scheme(parsed)
    row = expand_record(parsed[0], scheme)["main"]
    title = row["tag_245"]
    assert len(title) == 1
    title_mapping = next(
        m for m in scheme["main"] if m["target"] == "tag_245"
    )
    b_name = next(
        c["target"] for c in title_mapping["columns"] if c["subfield"] == "b"
    )
    b_values = title[0]["subfields"][b_name]
    assert [v["value"] for v in b_values] == [
        "Part one. ",
        "Part two, same key must not overwrite.",
    ]
    # subfield_order is the position within the whole field ($a=0).
    assert [v["subfield_order"] for v in b_values] == [1, 2]
    # Provenance: byte offsets into the source file.
    assert all(isinstance(v["byte_offset"], int) for v in b_values)


def test_repeated_fields_become_subtable_rows_with_relation(parsed):
    scheme = default_scheme(parsed)
    dataset = expand_dataset(parsed, scheme)
    rows = dataset["subtables"]["sub_700"]
    assert len(rows) == 2  # two 700 occurrences
    assert all(r["record_no"] == parsed[0].record_no for r in rows)
    # field_order is the record-wide field index (001=0, 245=1, 700s=2,3).
    assert [r["field_order"] for r in rows] == [2, 3]
    e_name = next(
        c["target"]
        for st in scheme["subtables"]
        if st["name"] == "sub_700"
        for c in st["columns"]
        if c["subfield"] == "e"
    )
    # Repeated $e within the first 700 survives as two entries.
    e_entries = rows[0][e_name]
    assert len(e_entries) == 2
    assert {v["value"] for v in e_entries} == {
        "editor.",
        "writer of foreword.",
    }


def test_invalid_records_listed_as_skipped_not_silently_dropped():
    recs, _ = parse_file("/workspace/samples/sample_corrupt_chain.mrc")
    scheme = default_scheme([r for r in recs if r.status != "invalid"])
    dataset = expand_dataset(recs, scheme)
    assert len(dataset["main_rows"]) == 4
    assert len(dataset["skipped_records"]) == 4
    assert all("reasons" in s and s["record_no"] for s in dataset["skipped_records"])


def test_scheme_validation_rejects_bad_names():
    bad = {"main": [{"target": "1bad", "tag": "245", "kind": "data",
                     "columns": []}], "subtables": []}
    assert validate_scheme(bad)
    dup = {"main": [
        {"target": "ok", "tag": "245", "kind": "control"},
        {"target": "ok", "tag": "246", "kind": "control"},
    ], "subtables": []}
    assert validate_scheme(dup)


def test_sqlite_export_is_queryable_and_keeps_provenance(parsed, tmp_path):
    scheme = default_scheme(parsed)
    dataset = expand_dataset(parsed, scheme)
    out = export(dataset, "sqlite", tmp_path / "out.sqlite3")
    con = sqlite3.connect(out)
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "records" in tables and "sub_700" in tables
        main = con.execute(
            "SELECT record_no, record_byte_offset FROM records"
        ).fetchall()
        assert main == [(1, 0)]
        joined = con.execute(
            "SELECT m.record_no, s.field_order FROM records m "
            "JOIN sub_700 s ON s.record_no = m.record_no ORDER BY s.field_order"
        ).fetchall()
        assert joined == [(1, 2), (1, 3)]
        meta = dict(con.execute("SELECT key, value FROM export_meta").fetchall())
        assert "scheme" in meta and "skipped_records" in meta
    finally:
        con.close()


def test_jsonl_export_embeds_subtables_and_source(tmp_path, parsed):
    scheme = default_scheme(parsed)
    dataset = expand_dataset(parsed, scheme)
    out = export(dataset, "jsonl", tmp_path / "out.jsonl")
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    obj = json.loads(lines[0])
    assert obj["_source"]["record_no"] == 1
    assert isinstance(obj["_source"]["record_byte_offset"], int)
    assert len(obj["_subtables"]["sub_700"]) == 2


def test_csv_zip_export_uses_json_for_array_cells(tmp_path, parsed):
    import zipfile

    scheme = default_scheme(parsed)
    dataset = expand_dataset(parsed, scheme)
    out = export(dataset, "csv", tmp_path / "out.zip")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "main.csv" in names and "sub_700.csv" in names
        main = zf.read("main.csv").decode("utf-8")
        assert "record_no" in main
        meta = json.loads(zf.read("export_meta.json").decode("utf-8"))
        assert meta["note"].startswith("Array/object")
