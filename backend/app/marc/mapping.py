"""Field mapping engine.

A mapping scheme turns parsed MARC records into a flat-but-typed dataset:

* The **main table** has one row per record, keyed by the original record
  number and file byte offset.
* A main-table target receives an *array* of occurrences (repeated fields
  and repeated subfields never overwrite each other; every occurrence
  keeps its field order and byte offset).
* **Subtables** model repeated fields as child rows, linked to the main
  row by ``record_no``; every subfield occurrence is one ordered entry.

The scheme is plain JSON-serializable data so the Vue frontend can edit
it and POST it back unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .iso2709 import ParsedRecord, FieldRep, SubfieldRep

VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class MappingError(ValueError):
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def _value_entry(sf: SubfieldRep) -> dict:
    return {
        "value": sf.value,
        "subfield_order": sf.order,
        "byte_offset": sf.byte_offset,
        "raw_hex": sf.raw_hex,
        "has_raw_bytes": sf.decode.has_raw_bytes,
    }


def _field_base(field: FieldRep) -> dict:
    return {
        "tag": field.tag,
        "field_order": field.order,
        "byte_offset": field.byte_offset,
        "length": field.length,
        "indicators": list(field.indicators) if field.indicators else None,
    }


def default_scheme(records: list[ParsedRecord], name: str = "default") -> dict:
    """Build an editable default scheme from every tag present."""
    tags: dict[str, set[str]] = {}
    control_tags: set[str] = set()
    for rec in records:
        for f in rec.fields:
            if f.field_type == "control":
                control_tags.add(f.tag)
                continue
            tags.setdefault(f.tag, set())
            for sf in f.subfields:
                tags[f.tag].add(sf.code)

    main: list[dict] = []
    subtables: list[dict] = []
    for tag in sorted(control_tags):
        main.append(
            {
                "target": f"ctrl_{tag}",
                "tag": tag,
                "kind": "control",
                "join": False,
                "enabled": True,
            }
        )
    for tag in sorted(tags):
        cols = [
            {
                "subfield": code,
                "target": f"sf_{tag}_{code}",
                "join": False,
                "enabled": True,
            }
            for code in sorted(tags[tag])
        ]
        # Repeated, structurally important tags become child subtables;
        # the title (245) stays on the main row but is still array-valued.
        if tag in {"245", "100", "130"}:
            main.append(
                {
                    "target": f"tag_{tag}",
                    "tag": tag,
                    "kind": "data",
                    "join": False,
                    "enabled": True,
                    "columns": cols,
                }
            )
        else:
            subtables.append(
                {
                    "name": f"sub_{tag}",
                    "tag": tag,
                    "enabled": True,
                    "columns": cols,
                }
            )
    return {
        "name": name,
        "description": "Auto-generated mapping from tags present in the file",
        "main": main,
        "subtables": subtables,
    }


def validate_scheme(scheme: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    errors: list[str] = []
    if not isinstance(scheme, dict):
        return ["Scheme must be an object"]
    seen_targets: set[str] = set()
    for m in scheme.get("main", []):
        target = m.get("target", "")
        if not VALID_NAME.match(target or ""):
            errors.append(f"Invalid main target name {target!r}")
        elif target in seen_targets:
            errors.append(f"Duplicate main target {target!r}")
        seen_targets.add(target)
        if m.get("kind") == "data":
            for col in m.get("columns", []):
                if not VALID_NAME.match(col.get("target", "") or ""):
                    errors.append(
                        f"Invalid column name {col.get('target')!r} in {target}"
                    )
    seen_tables: set[str] = set()
    for st in scheme.get("subtables", []):
        name = st.get("name", "")
        if not VALID_NAME.match(name or ""):
            errors.append(f"Invalid subtable name {name!r}")
        elif name in seen_tables:
            errors.append(f"Duplicate subtable name {name!r}")
        seen_tables.add(name)
        for col in st.get("columns", []):
            if not VALID_NAME.match(col.get("target", "") or ""):
                errors.append(f"Invalid column name {col.get('target')!r} in {name}")
    return errors


def _join_values(entries: list[dict], separator: str) -> dict | None:
    """Collapse ordered value entries to one string when join is enabled."""
    if not entries:
        return None
    return {
        "value": separator.join(e["value"] for e in entries),
        "subfield_order": entries[0]["subfield_order"],
        "byte_offset": entries[0]["byte_offset"],
        "raw_hex": " ".join(e["raw_hex"] for e in entries),
        "has_raw_bytes": any(e["has_raw_bytes"] for e in entries),
        "joined": True,
        "occurrence_count": len(entries),
    }


def _collect_data_occurrences(field: FieldRep, columns: list[dict]) -> dict:
    """Expand one data field into {column_target: [ordered value entries]}."""
    by_target: dict[str, list] = {}
    enabled = {c["subfield"]: c for c in columns if c.get("enabled", True)}
    for sf in field.subfields:
        col = enabled.get(sf.code)
        if col is None:
            continue
        by_target.setdefault(col["target"], []).append(_value_entry(sf))
    out: dict = {}
    for col in columns:
        if not col.get("enabled", True):
            continue
        entries = by_target.get(col["target"], [])
        if col.get("join"):
            joined = _join_values(entries, col.get("separator", " "))
            out[col["target"]] = [joined] if joined else []
        else:
            out[col["target"]] = entries
    return out


def expand_record(record: ParsedRecord, scheme: dict) -> dict:
    """Expand one parsed record into the main row + subtable rows."""
    main_row: dict = {
        "record_no": record.record_no,
        "record_byte_offset": record.byte_offset,
        "declared_length": record.declared_length,
        "actual_length": record.actual_length,
        "leader": record.leader,
        "encoding": record.encoding,
        "status": record.status,
        "issue_count": len(record.issues),
    }
    sub_rows: dict[str, list[dict]] = {
        st["name"]: [] for st in scheme.get("subtables", []) if st.get("enabled", True)
    }

    fields_by_tag: dict[str, list[FieldRep]] = {}
    for f in record.fields:
        fields_by_tag.setdefault(f.tag, []).append(f)

    for mapping in scheme.get("main", []):
        if not mapping.get("enabled", True):
            continue
        target, tag = mapping["target"], mapping["tag"]
        occurrences = fields_by_tag.get(tag, [])
        if mapping["kind"] == "control":
            entries = []
            for f in occurrences:
                if f.control_decode is None:
                    continue
                entries.append(
                    {
                        "value": f.control_value,
                        "field_order": f.order,
                        "byte_offset": f.byte_offset,
                        "raw_hex": f.raw_hex,
                        "has_raw_bytes": f.control_decode.has_raw_bytes,
                    }
                )
            if mapping.get("join"):
                main_row[target] = (
                    [_join_values(entries, mapping.get("separator", " "))]
                    if entries
                    else []
                )
            else:
                main_row[target] = entries
        else:
            expanded = []
            for f in occurrences:
                occ = _field_base(f)
                occ["subfields"] = _collect_data_occurrences(
                    f, mapping.get("columns", [])
                )
                expanded.append(occ)
            main_row[target] = expanded

    for st in scheme.get("subtables", []):
        if not st.get("enabled", True):
            continue
        for f in fields_by_tag.get(st["tag"], []):
            row = {
                "record_no": record.record_no,
                "record_byte_offset": record.byte_offset,
                "tag": f.tag,
                "field_order": f.order,
                "field_byte_offset": f.byte_offset,
                "field_length": f.length,
                "indicators": list(f.indicators) if f.indicators else None,
            }
            row.update(_collect_data_occurrences(f, st.get("columns", [])))
            sub_rows[st["name"]].append(row)

    return {"main": main_row, "subtables": sub_rows}


def expand_dataset(
    records: list[ParsedRecord], scheme: dict
) -> dict:
    """Expand all parseable records; invalid records are reported, not dropped."""
    errors = validate_scheme(scheme)
    if errors:
        raise MappingError("; ".join(errors))
    main_rows = []
    sub_tables: dict[str, list[dict]] = {
        st["name"]: [] for st in scheme.get("subtables", []) if st.get("enabled", True)
    }
    skipped: list[dict] = []
    for rec in records:
        if rec.status == "invalid":
            skipped.append(
                {
                    "record_no": rec.record_no,
                    "byte_offset": rec.byte_offset,
                    "reasons": [i.code for i in rec.issues],
                }
            )
            continue
        expanded = expand_record(rec, scheme)
        main_rows.append(expanded["main"])
        for name, rows in expanded["subtables"].items():
            sub_tables[name].extend(rows)
    return {
        "scheme": scheme,
        "main_rows": main_rows,
        "subtables": sub_tables,
        "skipped_records": skipped,
    }
