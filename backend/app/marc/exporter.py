"""Dataset export.

Three formats, all preserving provenance (original record number and byte
offsets into the source ISO 2709 file):

* SQLite (.sqlite3) -- the queryable dataset: one main table plus one table
  per repeated-field subtable, linked by ``record_no``.
* JSON Lines (.jsonl) -- one main-row object per line with nested subtables.
* CSV bundle (.zip) -- main.csv plus one CSV per subtable; array-valued
  cells are JSON-encoded so repeats survive the flat format.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from pathlib import Path

from .mapping import expand_dataset, VALID_NAME


def _json_cell(value) -> str:
    """CSV cell encoding: scalars stay readable, structures become JSON."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def _ordered_main_columns(dataset: dict) -> list[str]:
    cols = [
        "record_no",
        "record_byte_offset",
        "declared_length",
        "actual_length",
        "leader",
        "encoding",
        "status",
        "issue_count",
    ]
    for row in dataset["main_rows"]:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


def _ordered_sub_columns(rows: list[dict], fixed: list[str]) -> list[str]:
    cols = list(fixed)
    for row in rows:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


def export_sqlite(dataset: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(out_path)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        main_cols = _ordered_main_columns(dataset)
        fixed_main = {
            "record_no",
            "record_byte_offset",
            "declared_length",
            "actual_length",
            "leader",
            "encoding",
            "status",
            "issue_count",
        }
        main_ddl = [
            "record_no INTEGER PRIMARY KEY",
            "record_byte_offset INTEGER NOT NULL",
            "declared_length INTEGER",
            "actual_length INTEGER",
            "leader TEXT",
            "encoding TEXT",
            "status TEXT",
            "issue_count INTEGER",
        ]
        main_ddl.extend(
            f'"{c}" TEXT' for c in main_cols if c not in fixed_main
        )
        con.execute(f'CREATE TABLE records ({", ".join(main_ddl)})')
        placeholders = ",".join("?" for _ in main_cols)
        for row in dataset["main_rows"]:
            values = [
                json.dumps(row[c], ensure_ascii=False)
                if isinstance(row.get(c), (list, dict))
                else row.get(c)
                for c in main_cols
            ]
            con.execute(
                f'INSERT INTO records ({", ".join(chr(34)+c+chr(34) for c in main_cols)}) '
                f"VALUES ({placeholders})",
                values,
            )

        for name, rows in dataset["subtables"].items():
            if not VALID_NAME.match(name):
                raise ValueError(f"Unsafe subtable name {name!r}")
            fixed_names = [
                "record_no",
                "record_byte_offset",
                "tag",
                "field_order",
                "field_byte_offset",
                "field_length",
                "indicators",
            ]
            cols = _ordered_sub_columns(rows, fixed_names)
            extra = [c for c in cols if c not in fixed_names]
            ddl_parts = [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "record_no INTEGER NOT NULL REFERENCES records(record_no)",
                "record_byte_offset INTEGER",
                "tag TEXT",
                "field_order INTEGER",
                "field_byte_offset INTEGER",
                "field_length INTEGER",
                "indicators TEXT",
            ]
            ddl_parts.extend(f'"{c}" TEXT' for c in extra)
            con.execute(f"CREATE TABLE {name} ({', '.join(ddl_parts)})")
            all_cols = fixed_names + extra
            placeholders = ",".join("?" for _ in all_cols)
            for row in rows:
                values = []
                for c in all_cols:
                    v = row.get(c)
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, ensure_ascii=False)
                    values.append(v)
                con.execute(
                    f"INSERT INTO {name} "
                    f'({", ".join(chr(34)+c+chr(34) for c in all_cols)}) '
                    f"VALUES ({placeholders})",
                    values,
                )

        con.execute(
            "CREATE TABLE export_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        meta = {
            "scheme": json.dumps(dataset["scheme"], ensure_ascii=False),
            "skipped_records": json.dumps(
                dataset["skipped_records"], ensure_ascii=False
            ),
            "main_row_count": len(dataset["main_rows"]),
        }
        con.executemany(
            "INSERT INTO export_meta (key, value) VALUES (?, ?)", meta.items()
        )
        con.commit()
    finally:
        con.close()
    return out_path


def export_jsonl(dataset: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    main_cols = _ordered_main_columns(dataset)
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in dataset["main_rows"]:
            obj = {c: row.get(c) for c in main_cols}
            obj["_subtables"] = {
                name: [r for r in rows if r["record_no"] == row["record_no"]]
                for name, rows in dataset["subtables"].items()
            }
            obj["_source"] = {
                "record_no": row["record_no"],
                "record_byte_offset": row["record_byte_offset"],
            }
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        if dataset["skipped_records"]:
            fh.write(
                json.dumps(
                    {"_skipped_records": dataset["skipped_records"]},
                    ensure_ascii=False,
                )
                + "\n"
            )
    return out_path


def export_csv_zip(dataset: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    main_cols = _ordered_main_columns(dataset)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(main_cols)
    for row in dataset["main_rows"]:
        writer.writerow([_json_cell(row.get(c)) for c in main_cols])
    main_csv = buffer.getvalue()

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.csv", main_csv.encode("utf-8"))
        for name, rows in dataset["subtables"].items():
            cols = _ordered_sub_columns(
                rows,
                [
                    "record_no",
                    "record_byte_offset",
                    "tag",
                    "field_order",
                    "field_byte_offset",
                    "field_length",
                    "indicators",
                ],
            )
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for row in rows:
                w.writerow([_json_cell(row.get(c)) for c in cols])
            zf.writestr(f"{name}.csv", buf.getvalue().encode("utf-8"))
        zf.writestr(
            "export_meta.json",
            json.dumps(
                {
                    "scheme": dataset["scheme"],
                    "skipped_records": dataset["skipped_records"],
                    "note": "Array/object cells are JSON-encoded",
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
    return out_path


def export(dataset: dict, fmt: str, out_path: str | Path) -> Path:
    fmt = fmt.lower()
    if fmt == "sqlite":
        return export_sqlite(dataset, out_path)
    if fmt == "jsonl":
        return export_jsonl(dataset, out_path)
    if fmt == "csv":
        return export_csv_zip(dataset, out_path)
    raise ValueError(f"Unknown export format {fmt!r}")
