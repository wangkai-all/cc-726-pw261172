"""Generate deterministic ISO 2709 / MARC21 sample files.

Run with the project venv::

    python -I backend/scripts/generate_samples.py

Outputs go to ``samples/`` (kept out of the application package so the
web service never serves generator code as data). The builder assembles
records byte-by-byte so that MARC-8 escapes, combining diacritics and
deliberate structural corruption are all under exact control -- pymarc
5.x's own writer cannot express every one of these cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

FT = b"\x1e"  # field terminator
RT = b"\x1d"  # record terminator
SF = b"\x1f"  # subfield delimiter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "samples"


def _control_field(tag: str, content: bytes) -> tuple[bytes, bytes]:
    # Directory field length includes the field terminator (0x1E).
    body = content + FT
    return tag.encode("ascii"), body


def _data_field(tag: str, ind1: str, ind2: str, subfields: list[tuple[str, bytes]]) -> tuple[bytes, bytes]:
    body = ind1.encode("ascii") + ind2.encode("ascii")
    for code, value in subfields:
        body += SF + code.encode("ascii") + value
    body += FT
    return tag.encode("ascii"), body


def build_record(
    fields: list[tuple[bytes, bytes]],
    encoding_flag: str = "a",
    leader_overrides: dict | None = None,
    length_delta: int = 0,
    omit_record_terminator: bool = False,
    corrupt_directory: bool = False,
) -> bytes:
    """Assemble one ISO 2709 record from (tag-bytes, field-body) pairs.

    Lengths and base address are computed from actual bytes, so a clean
    build is always internally consistent; corruption flags perturb the
    output in well-defined ways for the parser's recovery tests.
    """
    directory = b""
    data_area = b""
    for idx, (tag, body) in enumerate(fields):
        start = len(data_area)
        directory += tag + f"{len(body):04d}{start:05d}".encode("ascii")
        data_area += body
    if corrupt_directory:
        # Replace one length digit with a non-digit, producing an
        # unparseable directory entry while keeping byte layout otherwise.
        directory = directory[:3 + 3 + 1] + b"X" + directory[3 + 3 + 2 :]
    directory += FT
    base_address = 24 + len(directory)
    record_length = base_address + len(data_area) + 1 + length_delta
    # MARC21 Leader layout: 00-04 length, 05 status, 06 type, 07 bib level,
    # 08 type of control, 09 encoding, 10 indicator count, 11 subfield code
    # count, 12-16 base address, 17-19 entry map ("4500"), 20-23 unused.
    leader = (
        f"{record_length:05d}"        # 00-04
        "n"                           # 05 record status
        "a"                           # 06 type of record
        "m"                           # 07 bibliographic level
        " "                           # 08 type of control
        f"{encoding_flag}"            # 09 character coding scheme
        "2"                           # 10 indicator length
        "2"                           # 11 subfield code length
        f"{base_address:05d}"         # 12-16
        "4500"                        # 17-20 entry map
        "   "                         # 21-23 unused
    ).encode("ascii")
    assert len(leader) == 24
    if leader_overrides:
        leader = bytearray(leader)
        for pos, value in leader_overrides.items():
            leader[pos] = ord(value) if isinstance(value, str) else value
        leader = bytes(leader)
    record = leader + directory + data_area
    if not omit_record_terminator:
        record += RT
    return record


# ---------------------------------------------------------------------------
# Reusable field content
# ---------------------------------------------------------------------------

def multilingual_record() -> bytes:
    """UTF-8 record: Arabic, Chinese, Russian, Greek plus repeats."""
    fields = [
        _control_field("001", b"ML-001"),
        _control_field("008", b"230115s2023    nyu     b    001 0 eng d"),
        _data_field("020", " ", " ", [("a", "978-0-306-40615-7".encode("utf-8"))]),
        _data_field(
            "245", "1", "0",
            [
                ("a", "多语种联合目录: ".encode("utf-8")),
                ("b", "النشر متعدد اللغات / ".encode("utf-8")),
                ("c", "под ред. Αθηνάς Παππά.".encode("utf-8")),
            ],
        ),
        _data_field(
            "246", "1", "1",
            [("a", "Multilingual union catalogue".encode("utf-8"))],
        ),
        _data_field(
            "650", " ", "0",
            [
                ("a", "Cataloging".encode("utf-8")),
                ("a", "Multilingualism.".encode("utf-8")),  # repeated $a
                ("v", "Periodicals.".encode("utf-8")),
            ],
        ),
        _data_field(
            "650", " ", "0",
            [("a", "Metadata.".encode("utf-8"))],  # second 650 occurrence
        ),
    ]
    return build_record(fields)


def repeated_subfields_record() -> bytes:
    """Repeated tags and repeated subfields, order-sensitive."""
    fields = [
        _control_field("001", b"REP-002"),
        _data_field(
            "245", "1", "0",
            [
                ("a", b"Main title. "),
                ("b", b"Part one. "),
                ("b", b"Part two, same key must not overwrite."),
                ("c", b"First editor ;"),
                ("c", b"second editor."),
            ],
        ),
        _data_field(
            "700", "1", " ",
            [("a", b"Doe, Jane,"), ("e", b"editor."), ("e", b"writer of foreword.")],
        ),
        _data_field(
            "700", "1", " ",
            [("a", b"Roe, Richard,"), ("e", b"illustrator.")],
        ),
    ]
    return build_record(fields)


def marc8_record() -> bytes:
    """MARC-8 (Leader/09 = blank): ANSEL diacritics + escape-switched sets."""
    # ANSEL combining marks precede their base letter:
    #   0xE1 = combining grave, 0xE2 = acute, 0xE7 = caron
    # 0xA6 = small oe ligature (U+0153), a G1 graphic.
    title = b"caf\xe1e et litte\xa6ure, \xe7sou\xc0cek."
    # ESC (2 switches G0 to code set 0x32 (mathematical/super/subscript):
    # 'b' there is U+02D2 (modifier letter turned comma); ESC s reverts
    # G0 to basic Latin.
    publisher = b"Editions du C" + b"\x1b(2" + b"b" + b"\x1bs" + b"entre"
    fields = [
        _control_field("001", b"M8-003"),
        _data_field("245", "1", "0", [("a", title), ("b", publisher + b".")]),
        _data_field(
            "100", "1", " ",
            # ANSEL 0xE2 = acute: "André"
            [("a", b"Andr\xe2e Gide,"), ("d", b"1869-1951.")],
        ),
        _data_field("260", " ", " ", [("b", publisher + b","), ("c", b"1925.")]),
    ]
    # Leader/09 blank => MARC-8.
    return build_record(fields, encoding_flag=" ")


def marc8_cjk_record() -> bytes:
    """MARC-8 EACC multi-byte: ESC $,1 then three-byte CJK ideographs."""
    # EACC code points come from the pymarc EACC table (code set 0x31);
    # pick three real entries so the sample stays in sync with the
    # installed mapping table. G0 stays in EACC mode for the whole run,
    # then ESC s reverts to basic Latin.
    from pymarc import marc8_mapping

    eacc = marc8_mapping.CODESETS[0x31]
    samples = [cp for cp in eacc if 0x690000 <= cp <= 0x695000][:3]
    ideographs = b"".join(cp.to_bytes(3, "big") for cp in samples)
    raw = b"\x1b$(1" + ideographs + b"\x1bs"
    fields = [
        _control_field("001", b"CK-004"),
        _data_field(
            "245", "1", "0",
            [("a", b"EACC sample "), ("b", raw + b".")],
        ),
    ]
    return build_record(fields, encoding_flag=" ")


def bad_bytes_record() -> bytes:
    """Unexplainable byte combinations in both encoding regimes."""
    # UTF-8 lead byte 0xE1 followed by only one continuation -> invalid;
    # plus an isolated continuation 0x80.
    broken_utf8 = b"Broken title \xe1\x80 \x80 end"
    fields = [
        _control_field("001", b"BB-005"),
        _data_field("245", "1", "0", [("a", broken_utf8)]),
    ]
    return build_record(fields)


def marc8_bad_bytes_record() -> bytes:
    """MARC-8 record carrying an unmappable byte and a truncated escape."""
    # 0xAF is not assigned in the ANSEL G1 table; trailing ESC has no
    # designator. Both must survive as raw-byte markers, not '?'.
    fields = [
        _control_field("001", b"MB-006"),
        _data_field("245", "1", "0", [("a", b"Bad marc8 \xaf end\x1b")]),
    ]
    return build_record(fields, encoding_flag=" ")


def build_corrupt_chain() -> bytes:
    """Corruption cases, each followed by a healthy locatable record.

    Layout (``G`` = good, ``B`` = deliberately broken):

    1. G multilingual
    2. B declared length +5 (record length mismatch)
    3. G repeated subfields
    4. B missing record terminator
    5. G MARC-8 combining
    6. B corrupt directory entry
    7. G multilingual again
    8. trailing garbage with no terminator -> parser must stop explicitly
    """
    good1 = multilingual_record()
    good2 = repeated_subfields_record()
    good3 = marc8_record()
    good4 = multilingual_record()

    bad_length = build_record(
        [
            _control_field("001", b"BL-101"),
            _data_field("245", "1", "0", [("a", b"Wrong declared length.")]),
        ],
        length_delta=+5,
    )

    missing_rt = build_record(
        [
            _control_field("001", b"NR-102"),
            _data_field("245", "1", "0", [("a", b"Missing record terminator.")]),
        ],
        omit_record_terminator=True,
    )

    bad_dir = build_record(
        [
            _control_field("001", b"BD-103"),
            _data_field("245", "1", "0", [("a", b"Corrupt directory entry.")]),
        ],
        corrupt_directory=True,
    )

    # Garbage block with no record terminator anywhere: parser must stop
    # explicitly rather than invent records.
    garbage = b"\xff\xfe\x00not-a-marc-record-at-all"

    return b"".join(
        [good1, bad_length, good2, missing_rt, good3, bad_dir, good4, garbage]
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {
        "sample_multilingual.mrc": multilingual_record(),
        "sample_repeated_subfields.mrc": repeated_subfields_record(),
        "sample_marc8_combining.mrc": marc8_record(),
        "sample_marc8_eacc.mrc": marc8_cjk_record(),
        "sample_bad_bytes_utf8.mrc": bad_bytes_record(),
        "sample_bad_bytes_marc8.mrc": marc8_bad_bytes_record(),
        "sample_corrupt_chain.mrc": build_corrupt_chain(),
    }
    for name, payload in files.items():
        path = OUT / name
        path.write_bytes(payload)
        print(f"wrote {path} ({len(payload)} bytes)")


if __name__ == "__main__":
    sys.exit(main())
