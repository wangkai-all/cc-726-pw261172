"""Tests for UTF-8 / MARC-8 decoding and raw-byte preservation."""

from app.marc.charset import (
    decode_text,
    split_marker_segments,
    MARK_START,
    MARK_END,
)
from app.marc.iso2709 import parse_file


def test_utf8_multilingual_roundtrip():
    text = "多语种 catalogué Αθηνά".encode("utf-8")
    result = decode_text(text, "utf-8")
    assert result.text == "多语种 catalogué Αθηνά"
    assert result.issues == []
    assert not result.has_raw_bytes


def test_utf8_bad_bytes_are_kept_not_replaced_by_question_mark():
    data = b"ok \xe1\x80 bad \x80 end"
    result = decode_text(data, "utf-8")
    assert "?" not in result.text
    assert result.has_raw_bytes
    codes = [i.code for i in result.issues]
    assert codes == ["utf8_invalid_sequence", "utf8_invalid_sequence"]
    assert result.issues[0].raw_hex == "E1 80"
    assert result.issues[1].raw_hex == "80"
    segments = split_marker_segments(result.text)
    raw = [s for s in segments if s["type"] == "raw"]
    assert [s["hex"] for s in raw] == ["E1 80", "80"]
    clean = "".join(s["value"] for s in segments if s["type"] == "text")
    assert clean.startswith("ok ") and clean.endswith(" end")


def test_marc8_combining_diacritics_recorded_with_offsets():
    # 0xE1 = combining grave before base 'e' -> café
    result = decode_text(b"caf\xe1e", "marc8")
    assert result.text == "cafè"
    assert len(result.combining) == 1
    c = result.combining[0]
    assert c.byte_offset == 3
    assert "GRAVE" in c.name
    assert result.issues == []


def test_marc8_escape_switch_and_revert():
    # ESC (2 -> G0 set 0x32, 'b' maps to U+02D2; ESC s -> basic Latin
    result = decode_text(b"C\x1b(2b\x1bsentre", "marc8")
    assert result.text == "Cגentre"
    assert result.issues == []


def test_marc8_eacc_multibyte():
    from pymarc import marc8_mapping

    eacc = marc8_mapping.CODESETS[0x31]
    cp = next(c for c in eacc if 0x690000 <= c <= 0x695000)
    raw = b"\x1b$(1" + cp.to_bytes(3, "big") + b"\x1bs"
    result = decode_text(raw, "marc8")
    assert not result.has_raw_bytes
    assert chr(eacc[cp][0]) in result.text


def test_marc8_unmappable_byte_preserved():
    # 0xAF is not assigned in the ANSEL G1 table.
    result = decode_text(b"bad \xaf end", "marc8")
    assert result.has_raw_bytes
    assert "?" not in result.text
    issue = result.issues[0]
    assert issue.code == "marc8_unmappable_byte"
    assert issue.raw_hex == "AF"
    assert "AF" in result.text


def test_marc8_truncated_escape_preserved():
    result = decode_text(b"end\x1b", "marc8")
    assert result.has_raw_bytes
    assert result.issues[0].code == "marc8_truncated_escape"
    assert result.issues[0].raw_hex == "1B"


def test_unknown_codeset_does_not_destroy_following_text():
    # 0x41 ('A') is not a registered MARC-8 code-set designator.
    result = decode_text(b"a\x1b(Azb", "marc8")
    assert result.has_raw_bytes
    assert result.issues[0].code == "marc8_unknown_codeset"
    # Following ASCII text stays decodable because G0 is retained.
    assert result.text.endswith("zb")


def test_sample_bad_byte_records_carry_structured_issues():
    for path, enc in [
        ("/workspace/samples/sample_bad_bytes_utf8.mrc", "utf-8"),
        ("/workspace/samples/sample_bad_bytes_marc8.mrc", "marc8"),
    ]:
        recs, _ = parse_file(path)
        assert recs[0].encoding == enc
        all_issues = [
            i
            for f in recs[0].fields
            for i in f.decode_issues
        ]
        assert all_issues, path
        # No question-mark substitution anywhere.
        for f in recs[0].fields:
            for sf in f.subfields:
                assert "?" not in sf.value
