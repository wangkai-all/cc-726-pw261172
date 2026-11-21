"""Tests for the byte-level ISO 2709 parser and recovery behaviour."""

import pytest

from app.marc.iso2709 import (
    parse_records,
    parse_file,
    FIELD_TERMINATOR,
    RECORD_TERMINATOR,
    SUBFIELD_DELIMITER,
    _is_tag,
)

FT = bytes([FIELD_TERMINATOR])
RT = bytes([RECORD_TERMINATOR])
SF = bytes([SUBFIELD_DELIMITER])


def build(fields, encoding_flag="a"):
    """Minimal correct ISO 2709 record builder for tests."""
    directory = b""
    data = b""
    for tag, body in fields:
        body = body + FT
        directory += tag.encode() + f"{len(body):04d}{len(data):05d}".encode()
        data += body
    directory += FT
    base = 24 + len(directory)
    length = base + len(data) + 1
    leader = (
        f"{length:05d}nam {encoding_flag}22{base:05d}4500   ".encode()
    )
    assert len(leader) == 24
    return leader + directory + data + RT


def control(tag, content):
    return tag, content


def data(tag, i1, i2, *subs):
    body = i1.encode() + i2.encode()
    for code, value in subs:
        body += SF + code.encode() + value
    return tag, body


# ---------------------------------------------------------------------------
# Structural correctness
# ---------------------------------------------------------------------------

def test_parses_leader_directory_and_offsets():
    record = build(
        [
            control("001", b"REC-1"),
            data("245", "1", "0", ("a", b"Title."), ("c", b"Author.")),
        ]
    )
    recs, file_issues = parse_records(record)
    assert file_issues == []
    assert len(recs) == 1
    rec = recs[0]
    assert rec.status == "ok"
    assert rec.declared_length == len(record)
    assert rec.encoding == "utf-8"
    assert [f.tag for f in rec.fields] == ["001", "245"]
    title = rec.fields[1]
    assert title.indicators == ("1", "0")
    assert title.byte_offset == rec.byte_offset + rec.base_address + len(b"REC-1" + FT)
    assert title.length == len(title.raw_hex.split()) + 1  # incl. terminator


def test_splitting_uses_bytes_not_char_positions():
    # "界" is 3 UTF-8 bytes; the directory offsets must remain byte-based.
    value = "世界".encode("utf-8")
    record = build(
        [
            control("001", b"X"),
            data("245", "1", "0", ("a", value + b" tail")),
        ]
    )
    recs, _ = parse_records(record)
    rec = recs[0]
    field = rec.fields[1]
    sf = field.subfields[0]
    assert sf.value == "世界 tail"
    # The subfield byte offset is independent of the 3-bytes-per-char fact:
    # indicator 1 + indicator 2 + subfield delimiter precede the value.
    assert sf.byte_offset == field.byte_offset + 2


def test_repeated_fields_and_subfields_keep_order_without_overwrite():
    record = build(
        [
            control("001", b"R"),
            data(
                "650", " ", "0",
                ("a", b"First."), ("a", b"Second."), ("v", b"Vol."),
            ),
            data("650", " ", "0", ("a", b"Third.")),
        ]
    )
    recs, _ = parse_records(record)
    tags = recs[0].fields
    assert len(tags) == 3
    first, second = tags[1], tags[2]
    assert [s.value for s in first.subfields if s.code == "a"] == [
        "First.",
        "Second.",
    ]
    assert [s.order for s in first.subfields] == [0, 1, 2]
    assert second.subfields[0].value == "Third."
    assert second.order == 2


def test_marc8_selected_when_leader09_blank():
    record = build(
        [data("245", "1", "0", ("a", b"caf\xe1e."))], encoding_flag=" "
    )
    recs, _ = parse_records(record)
    assert recs[0].encoding == "marc8"
    assert "cafè" in recs[0].fields[0].subfields[0].value


# ---------------------------------------------------------------------------
# Recovery / isolation
# ---------------------------------------------------------------------------

def test_bad_length_record_isolated_good_records_retained():
    good1 = build([control("001", b"G1")])
    bad = bytearray(build([control("001", b"BAD")]))
    bad[0:5] = f"{len(bad) + 7:05d}".encode()  # declared length too long
    good2 = build([control("001", b"G2")])
    recs, issues = parse_records(bytes(good1) + bytes(bad) + good2)

    statuses = [(r.record_no, r.status) for r in recs]
    assert statuses == [(1, "ok"), (2, "invalid"), (3, "ok")]
    assert recs[0].fields[0].control_value == "G1"
    assert recs[2].fields[0].control_value == "G2"
    assert any(i.code == "missing_record_terminator" for i in recs[1].issues)
    assert issues == []  # resync succeeded, no fatal stop


def test_missing_record_terminator_isolated_and_resynced():
    good = build([control("001", b"OK")])
    broken = build([control("001", b"NR")])[:-1]  # strip final 0x1D
    recs, issues = parse_records(broken + good)
    assert recs[0].status == "invalid"
    assert any(i.code == "missing_record_terminator" for i in recs[0].issues)
    assert recs[1].status == "ok"
    assert issues == []


def test_corrupt_directory_flagged_but_raw_bytes_preserved():
    good = build([control("001", b"OK"), data("245", "1", "0", ("a", b"T"))])
    broken = bytearray(build([control("001", b"BD"), data("245", "1", "0", ("a", b"X"))]))
    base = int(bytes(broken[12:17]))
    broken[27] = ord("X")  # smash a length digit in the first directory entry
    recs, _ = parse_records(bytes(broken) + good)
    assert recs[0].status == "invalid"
    assert recs[0].fields == []
    assert recs[0].raw_hex  # raw bytes retained for inspection
    assert any(i.code.startswith("bad_directory") for i in recs[0].issues)
    assert recs[1].status == "ok"


def test_garbage_without_terminator_causes_explicit_fatal_stop():
    good = build([control("001", b"OK")])
    garbage = b"\xff\xfe\x00not-a-record"
    recs, issues = parse_records(good + garbage)
    assert recs[0].status == "ok"
    assert recs[1].status == "invalid"
    assert len(issues) == 1
    assert issues[0].level == "fatal"
    assert issues[0].code == "cannot_relocate"


def test_interrecord_padding_is_skipped():
    good = build([control("001", b"OK")])
    recs, issues = parse_records(good + b"\n  \n" + good)
    assert len(recs) == 2
    assert all(r.status == "ok" for r in recs)
    assert issues == []


def test_tag_must_be_three_digits():
    assert _is_tag(b"001") and _is_tag(b"245")
    assert not _is_tag(b"24L")
    assert not _is_tag(b"24z")
    assert not _is_tag(b"24")


def test_non_numeric_tag_in_directory_invalidates_record():
    good = build([control("001", b"OK")])
    broken = bytearray(build([control("001", b"TAG")]))
    broken[24:27] = b"24L"  # overwrite the first directory entry's tag
    recs, _ = parse_records(bytes(broken) + good)
    assert recs[0].status == "invalid"
    assert any(i.code == "bad_directory_entry" for i in recs[0].issues)
    assert recs[1].status == "ok"


def test_sample_corrupt_chain_end_to_end():
    recs, issues = parse_file(
        "/workspace/samples/sample_corrupt_chain.mrc"
    )
    good = [r for r in recs if r.status == "ok"]
    invalid = [r for r in recs if r.status == "invalid"]
    assert len(good) == 4
    assert len(invalid) == 4
    assert len(issues) == 1 and issues[0].level == "fatal"
    # Original record numbering is preserved even for invalid records.
    assert [r.record_no for r in recs] == list(range(1, 9))
