"""Character set handling for MARC21 field values.

Two encodings are supported, selected by Leader byte 9:

* ``a`` -> UTF-8 (Leader/09 == 'a')
* otherwise -> MARC-8 (the MARC 21 default; ANSEL plus escape-switchable
  G0/G1 code sets and the EACC multi-byte code set).

The hard requirement implemented here is that *nothing is silently
substituted*.  Bytes that cannot be interpreted are kept verbatim inside a
Private Use Area marker (``\\ue000..\\ue001`` carrying their hex dump) and
also reported in a structured issue list, so callers can never mistake a
recovered value for a clean one.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pymarc import marc8_mapping

EncodingName = Literal["utf-8", "marc8"]

# Private Use Area sentinels used to embed raw, unexplainable bytes inside
# an otherwise normal Python str.  PUA is never assigned by Unicode, so a
# marker can never collide with real bibliographic text.
MARK_START = ""
MARK_END = ""

# MARC-8 control bytes that retain meaning inside field data.
_MARC8_ESCAPE = 0x1B
_MARC8_G0_DESIGNATORS = {0x28, 0x2C, 0x24}  # ( , $
_MARC8_G1_DESIGNATORS = {0x29, 0x2D, 0x24}  # ) - $
_EACC_CODESET = 0x31
_BASIC_LATIN = 0x42
_ANSEL = 0x45


@dataclass
class DecodeIssue:
    """A single byte-level decoding problem."""

    code: str
    message: str
    byte_offset: int
    raw_hex: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "byte_offset": self.byte_offset,
            "raw_hex": self.raw_hex,
        }


@dataclass
class CombiningInfo:
    """A combining diacritic encountered while decoding."""

    char: str
    name: str
    byte_offset: int
    encoding: EncodingName

    def to_dict(self) -> dict:
        return {
            "char": self.char,
            "name": self.name,
            "byte_offset": self.byte_offset,
            "encoding": self.encoding,
        }


@dataclass
class DecodeResult:
    """Decoded text plus full provenance about what was not clean."""

    text: str
    encoding: EncodingName
    issues: list[DecodeIssue] = field(default_factory=list)
    combining: list[CombiningInfo] = field(default_factory=list)
    has_raw_bytes: bool = False

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "encoding": self.encoding,
            "issues": [i.to_dict() for i in self.issues],
            "combining": [c.to_dict() for c in self.combining],
            "has_raw_bytes": self.has_raw_bytes,
        }


def make_raw_marker(raw: bytes) -> str:
    """Wrap unexplainable bytes in a PUA marker carrying their hex dump."""
    return f"{MARK_START}{raw.hex(' ').upper()}{MARK_END}"


def split_marker_segments(text: str) -> list[dict]:
    """Split decoded text into clean text and raw-byte marker segments.

    The frontend uses this to render bad bytes as badges instead of
    invisible replacement characters.
    """
    segments: list[dict] = []
    buf: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == MARK_START:
            if buf:
                segments.append({"type": "text", "value": "".join(buf)})
                buf = []
            end = text.find(MARK_END, i + 1)
            if end == -1:  # defensive: treat rest as marker
                segments.append(
                    {"type": "raw", "hex": text[i + 1 :].strip()}
                )
                return segments
            segments.append({"type": "raw", "hex": text[i + 1 : end].strip()})
            i = end + 1
        else:
            buf.append(text[i])
            i += 1
    if buf:
        segments.append({"type": "text", "value": "".join(buf)})
    return segments


def _record_utf8_combining(text: str, base_offset: int) -> list[CombiningInfo]:
    return [
        CombiningInfo(
            char=ch,
            name=unicodedata.name(ch, "UNKNOWN COMBINING CHARACTER"),
            byte_offset=base_offset,
            encoding="utf-8",
        )
        for ch in text
        if unicodedata.combining(ch)
    ]


def _decode_utf8(data: bytes) -> DecodeResult:
    out: list[str] = []
    issues: list[DecodeIssue] = []
    combining: list[CombiningInfo] = []
    i = 0
    n = len(data)
    while i < n:
        lead = data[i]
        if lead < 0x80:
            out.append(chr(lead))
            i += 1
            continue
        if 0xC2 <= lead <= 0xDF:
            seq_len = 2
        elif 0xE0 <= lead <= 0xEF:
            seq_len = 3
        elif 0xF0 <= lead <= 0xF4:
            seq_len = 4
        else:
            seq_len = 1  # orphan continuation (0x80-0xBF) or 0xC0/0xC1
        candidate = data[i : i + seq_len]
        try:
            ch = candidate.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            # Consume the whole broken run: keep going until a byte at which a
            # new valid UTF-8 sequence can legally start.
            j = i + 1
            while j < n and 0x80 <= data[j] <= 0xBF:
                j += 1
            raw = data[i:j]
            issues.append(
                DecodeIssue(
                    code="utf8_invalid_sequence",
                    message=(
                        f"Invalid UTF-8 byte sequence {raw.hex(' ').upper()} "
                        "cannot be decoded; raw bytes retained"
                    ),
                    byte_offset=i,
                    raw_hex=raw.hex(" ").upper(),
                )
            )
            out.append(make_raw_marker(raw))
            i = j
            continue
        out.append(ch)
        combining.extend(_record_utf8_combining(ch, i))
        i += seq_len
    return DecodeResult(
        text="".join(out),
        encoding="utf-8",
        issues=issues,
        combining=combining,
        has_raw_bytes=bool(issues),
    )


def _lookup_single_byte(code_point: int, g0: int, g1: int, multibyte: bool = False):
    """Resolve one MARC-8 character (single byte or EACC code point).

    Single-byte graphics with the high bit set come from G1; everything
    else (low bytes and multi-byte EACC points) comes from G0.
    """
    try:
        if code_point > 0x80 and not multibyte:
            table = marc8_mapping.CODESETS[g1]
        else:
            table = marc8_mapping.CODESETS[g0]
        uni, cflag = table[code_point]
        return chr(uni), bool(cflag)
    except KeyError:
        odd = marc8_mapping.ODD_MAP.get(code_point)
        if odd is not None:
            return chr(odd), False
    return None


def _decode_marc8(data: bytes) -> DecodeResult:
    out: list[str] = []
    issues: list[DecodeIssue] = []
    combining: list[CombiningInfo] = []
    g0, g1 = _BASIC_LATIN, _ANSEL
    pending_combinings: list[str] = []
    i = 0
    n = len(data)

    def emit_base(ch: str) -> None:
        # ANSEL diacritics precede their base character; Unicode puts
        # combining marks after the base, so flush pending marks here.
        out.append(ch)
        if pending_combinings:
            out.extend(pending_combinings)
            pending_combinings.clear()

    while i < n:
        b = data[i]

        if b == _MARC8_ESCAPE:
            # Escape sequence: 1b intermediate final.
            if i + 1 >= n:
                raw = data[i : i + 1]
                issues.append(
                    DecodeIssue(
                        "marc8_truncated_escape",
                        "Escape character 0x1B at end of field with no designator",
                        i,
                        raw.hex(" ").upper(),
                    )
                )
                out.append(make_raw_marker(raw))
                i += 1
                continue
            second = data[i + 1]
            if second in _MARC8_G0_DESIGNATORS or second in _MARC8_G1_DESIGNATORS:
                # Four-byte EACC form: 1b 24 28/29/2c/2d final
                # (e.g. ESC $ ( 1 -> G0 = code set 0x31).
                if second == 0x24 and i + 3 < n and data[i + 2] in (
                    _MARC8_G0_DESIGNATORS | _MARC8_G1_DESIGNATORS
                ):
                    intermediate = data[i + 2]
                    final = data[i + 3]
                    consumed = 4
                else:
                    # Three-byte form: 1b <intermediate> <final>
                    if i + 2 >= n:
                        raw = data[i : n - i]
                        issues.append(
                            DecodeIssue(
                                "marc8_truncated_escape",
                                "Truncated MARC-8 escape sequence",
                                i,
                                raw.hex(" ").upper(),
                            )
                        )
                        out.append(make_raw_marker(raw))
                        i = n
                        continue
                    intermediate = second
                    final = data[i + 2]
                    consumed = 3
                if intermediate in _MARC8_G0_DESIGNATORS:
                    target = "G0"
                    current = g0
                else:
                    target = "G1"
                    current = g1
                if final in marc8_mapping.CODESETS:
                    if target == "G0":
                        g0 = final
                    else:
                        g1 = final
                else:
                    raw = data[i : i + consumed]
                    issues.append(
                        DecodeIssue(
                            "marc8_unknown_codeset",
                            f"Unknown {target} code set designator "
                            f"0x{final:02X}; keeping previous code set "
                            f"0x{current:02X}",
                            i,
                            raw.hex(" ").upper(),
                        )
                    )
                    out.append(make_raw_marker(raw))
                i += consumed
                continue
            # Two-byte form: 1b <final>
            final = second
            if final in marc8_mapping.CODESETS:
                g0 = final
            elif final == 0x73:  # ESC s -> revert to basic Latin
                g0 = _BASIC_LATIN
            else:
                raw = data[i : i + 2]
                issues.append(
                    DecodeIssue(
                        "marc8_unknown_escape",
                        f"Unrecognized MARC-8 escape sequence {raw.hex(' ').upper()}",
                        i,
                        raw.hex(" ").upper(),
                    )
                )
                out.append(make_raw_marker(raw))
            i += 2
            continue

        if g0 == _EACC_CODESET:
            if i + 2 >= n:
                raw = data[i:n]
                issues.append(
                    DecodeIssue(
                        "marc8_truncated_multibyte",
                        "Truncated EACC multi-byte character (3 bytes expected)",
                        i,
                        raw.hex(" ").upper(),
                    )
                )
                out.append(make_raw_marker(raw))
                i = n
                continue
            raw3 = data[i : i + 3]
            code_point = raw3[0] * 65536 + raw3[1] * 256 + raw3[2]
            resolved = _lookup_single_byte(
                code_point, g0, g1, multibyte=True
            )
            if resolved is None:
                issues.append(
                    DecodeIssue(
                        "marc8_unmappable_multibyte",
                        f"EACC code point {raw3.hex(' ').upper()} has no Unicode mapping",
                        i,
                        raw3.hex(" ").upper(),
                    )
                )
                out.append(make_raw_marker(raw3))
            else:
                emit_base(resolved[0])
            i += 3
            continue

        code_point = b
        offset = i
        i += 1
        if code_point < 0x20 or 0x80 <= code_point <= 0x9F:
            # C0 / C1 controls have no MARC-8 graphic meaning; pass through
            # (field terminators are stripped before reaching this layer).
            emit_base(chr(code_point))
            continue
        resolved = _lookup_single_byte(code_point, g0, g1)
        if resolved is None:
            raw = bytes([code_point])
            issues.append(
                DecodeIssue(
                    "marc8_unmappable_byte",
                    f"Byte 0x{code_point:02X} is not defined in active MARC-8 "
                    f"code sets (G0=0x{g0:02X}, G1=0x{g1:02X})",
                    offset,
                    raw.hex(" ").upper(),
                )
            )
            out.append(make_raw_marker(raw))
            continue
        ch, cflag = resolved
        if cflag:
            pending_combinings.append(ch)
            combining.append(
                CombiningInfo(
                    char=ch,
                    name=unicodedata.name(ch, "UNKNOWN COMBINING CHARACTER"),
                    byte_offset=offset,
                    encoding="marc8",
                )
            )
        else:
            emit_base(ch)

    if pending_combinings:  # combining marks with no following base
        out.extend(pending_combinings)
    return DecodeResult(
        text=unicodedata.normalize("NFC", "".join(out)),
        encoding="marc8",
        issues=issues,
        combining=combining,
        has_raw_bytes=bool(issues),
    )


def decode_text(data: bytes, encoding: EncodingName) -> DecodeResult:
    """Decode field-level bytes according to the declared character set.

    ``encoding`` is derived from Leader byte 9 by the caller.
    """
    if encoding == "utf-8":
        return _decode_utf8(data)
    return _decode_marc8(data)


def leader_encoding(leader: bytes) -> EncodingName:
    """Determine the field encoding from Leader position 9."""
    if len(leader) >= 10 and leader[9:10] == b"a":
        return "utf-8"
    return "marc8"
