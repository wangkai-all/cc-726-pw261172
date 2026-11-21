"""Byte-level ISO 2709 / MARC21 record parser.

Splitting is driven exclusively by the byte counts and offsets in the
Leader and directory entries -- never by decoded character positions:

* Leader positions 00-04 give the *record length* (ASCII digits).
* Leader positions 12-16 give the *base address of data*.
* Each 12-byte directory entry is ``tag(3) + field_length(4) +
  starting_position(5)``; the position is relative to the base address.
* Fields end with 0x1E (field terminator), the directory ends with 0x1E
  and every record ends with 0x1D (record terminator).

Recovery rules (mirroring the requirements):

* A broken record -- bad directory, missing terminator, declared length
  mismatch -- is isolated as an issue with the raw bytes preserved, and
  the parser scans forward for the next record terminator so later
  locatable records can still be inspected.
* When re-location cannot be made reliable (garbage with no record
  terminator before EOF, or no digit-led Leader candidate) parsing stops
  explicitly with a fatal issue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .charset import decode_text, leader_encoding, EncodingName, DecodeResult

RECORD_TERMINATOR = 0x1D
FIELD_TERMINATOR = 0x1E
SUBFIELD_DELIMITER = 0x1F
DIRECTORY_ENTRY_SIZE = 12
LEADER_SIZE = 24
MIN_RECORD_LEN = LEADER_SIZE + DIRECTORY_ENTRY_SIZE + 2 + 1  # leader+1 entry+FT+RT


@dataclass
class ParseIssue:
    """A structural or decoding problem, located by byte offset."""

    level: str  # "error" | "warning" | "fatal"
    code: str
    message: str
    byte_offset: int
    raw_hex: str | None = None
    record_no: int | None = None

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "byte_offset": self.byte_offset,
            "raw_hex": self.raw_hex,
            "record_no": self.record_no,
        }


@dataclass
class SubfieldRep:
    """One subfield occurrence. Repeats are separate objects, order kept."""

    code: str
    value: str
    decode: DecodeResult
    order: int
    byte_offset: int  # absolute offset of the subfield delimiter 0x1F
    raw_hex: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "value": self.value,
            "order": self.order,
            "byte_offset": self.byte_offset,
            "raw_hex": self.raw_hex,
            "decode": self.decode.to_dict(),
        }


@dataclass
class FieldRep:
    """One field occurrence, located inside the source file."""

    tag: str
    order: int
    field_type: str  # "control" | "data"
    byte_offset: int  # absolute offset of first field content byte
    length: int  # length from directory entry (excludes field terminator)
    raw_hex: str
    indicators: tuple[str, str] | None = None
    control_value: str | None = None
    control_decode: DecodeResult | None = None
    subfields: list[SubfieldRep] = field(default_factory=list)
    decode_issues: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "tag": self.tag,
            "order": self.order,
            "field_type": self.field_type,
            "byte_offset": self.byte_offset,
            "length": self.length,
            "raw_hex": self.raw_hex,
            "indicators": list(self.indicators) if self.indicators else None,
            "decode_issues": [i.to_dict() for i in self.decode_issues],
        }
        if self.field_type == "control":
            d["control_value"] = self.control_value
            d["control_decode"] = (
                self.control_decode.to_dict() if self.control_decode else None
            )
        else:
            d["subfields"] = [s.to_dict() for s in self.subfields]
        return d


@dataclass
class ParsedRecord:
    record_no: int
    leader: str
    leader_bytes: bytes
    declared_length: int
    actual_length: int
    byte_offset: int  # absolute offset of record start in the source file
    base_address: int
    encoding: EncodingName
    fields: list[FieldRep]
    issues: list[ParseIssue]
    raw_hex: str
    status: str  # "ok" | "recovered" | "invalid"

    @property
    def has_errors(self) -> bool:
        return any(i.level in ("error", "fatal") for i in self.issues)

    def to_summary(self) -> dict:
        return {
            "record_no": self.record_no,
            "leader": self.leader,
            "declared_length": self.declared_length,
            "actual_length": self.actual_length,
            "byte_offset": self.byte_offset,
            "base_address": self.base_address,
            "encoding": self.encoding,
            "status": self.status,
            "issue_count": len(self.issues),
            "field_count": len(self.fields),
            "tags": [f.tag for f in self.fields],
        }

    def to_detail(self) -> dict:
        d = self.to_summary()
        d["issues"] = [i.to_dict() for i in self.issues]
        d["fields"] = [f.to_dict() for f in self.fields]
        d["raw_hex"] = self.raw_hex
        return d


def _ascii_digits(b: bytes, start: int, length: int) -> int | None:
    chunk = b[start : start + length]
    if len(chunk) != length or any(c < 0x30 or c > 0x39 for c in chunk):
        return None
    return int(chunk.decode("ascii"))


def _is_tag(chunk: bytes) -> bool:
    # MARC21 tags are exactly three ASCII digits (e.g. 001, 245, 650).
    return len(chunk) == 3 and all(0x30 <= c <= 0x39 for c in chunk)


def _scan_record_terminator(data: bytes, start: int) -> int:
    """Return offset of the next 0x1D at or after ``start``."""
    idx = data.find(b"\x1d", start)
    return idx  # -1 if absent


def _parse_field(
    tag: str,
    field_bytes: bytes,
    abs_offset: int,
    order: int,
    encoding: EncodingName,
) -> FieldRep:
    """Parse one field's bytes as located by the directory.

    ``field_bytes`` has the directory-declared length and therefore still
    includes the trailing field terminator (0x1E); the terminator is
    stripped only for value decoding, while the declared length is kept
    for provenance.
    """
    is_control = tag.startswith("00")
    content = field_bytes[:-1] if field_bytes.endswith(b"\x1e") else field_bytes
    rep = FieldRep(
        tag=tag,
        order=order,
        field_type="control" if is_control else "data",
        byte_offset=abs_offset,
        length=len(field_bytes),
        raw_hex=content.hex(" ").upper(),
    )
    if is_control:
        decoded = decode_text(content, encoding)
        rep.control_value = decoded.text
        rep.control_decode = decoded
        rep.decode_issues.extend(decoded.issues)
        return rep

    indicators = (
        chr(content[0]) if len(content) > 0 and content[0] != 0x20 else " ",
        chr(content[1]) if len(content) > 1 and content[1] != 0x20 else " ",
    )
    # Keep blank indicators explicitly blank rather than rendering 0x20.
    indicators = (
        " " if len(content) > 0 and content[0] == 0x20 else indicators[0],
        " " if len(content) > 1 and content[1] == 0x20 else indicators[1],
    )
    rep.indicators = indicators

    body = content[2:]
    order_idx = 0
    pos = 0
    # A data field not starting with 0x1F is malformed; keep the bytes as
    # one raw subfield with code "" rather than dropping them.
    segments: list[tuple[int, bytes]] = []
    while pos < len(body):
        if body[pos] == SUBFIELD_DELIMITER:
            next_delim = body.find(bytes([SUBFIELD_DELIMITER]), pos + 1)
            end = next_delim if next_delim != -1 else len(body)
            segments.append((pos, body[pos + 1 : end]))
            pos = end
        else:
            next_delim = body.find(bytes([SUBFIELD_DELIMITER]), pos)
            end = next_delim if next_delim != -1 else len(body)
            segments.append((pos, body[pos:end]))
            pos = end

    for rel_offset, seg in segments:
        if not seg:
            continue
        code_byte = seg[0]
        code = chr(code_byte) if 0x21 <= code_byte <= 0x7A else f"0x{code_byte:02X}"
        value_bytes = seg[1:]
        decoded = decode_text(value_bytes, encoding)
        sf = SubfieldRep(
            code=code,
            value=decoded.text,
            decode=decoded,
            order=order_idx,
            byte_offset=abs_offset + 2 + rel_offset,
            raw_hex=seg.hex(" ").upper(),
        )
        rep.subfields.append(sf)
        rep.decode_issues.extend(decoded.issues)
        order_idx += 1
    return rep


def _looks_like_record_at(data: bytes, pos: int) -> int | None:
    """Return the declared length if ``pos`` could start a MARC record.

    A candidate must have 5 ASCII digit length bytes, fit inside the
    file, end with a record terminator at the declared length minus one,
    and carry a plausible base address whose byte is the directory
    terminator predecessor.
    """
    length = _ascii_digits(data, pos, 5)
    if length is None or length < MIN_RECORD_LEN:
        return None
    end = pos + length
    if end > len(data) or data[end - 1] != RECORD_TERMINATOR:
        return None
    base = _ascii_digits(data, pos + 12, 5)
    if base is None or not (LEADER_SIZE < base < length):
        return None
    if data[pos + base - 1] != FIELD_TERMINATOR:
        return None
    return length


def _find_next_record(data: bytes, after: int) -> int | None:
    """Scan for the next reliable record start strictly after ``after``.

    Every 5-digit run is treated as a candidate and *verified* against its
    own terminator/base-address claims rather than trusted blindly.
    """
    pos = after
    n = len(data)
    while pos + LEADER_SIZE < n:
        if _looks_like_record_at(data, pos) is not None:
            return pos
        pos += 1
    return None


def parse_records(data: bytes) -> tuple[list[ParsedRecord], list[ParseIssue]]:
    """Parse a complete ISO 2709 byte string.

    Returns located records (including invalid ones, flagged via
    ``status``/``issues``) and file-level issues. A fatal file-level issue
    means parsing stopped early because further records could not be
    located reliably.
    """
    records: list[ParsedRecord] = []
    file_issues: list[ParseIssue] = []
    pos = 0
    record_no = 0
    n = len(data)

    while pos < n:
        # Skip inter-record padding (spaces / nulls / newlines are common
        # between concatenated records).
        while pos < n and data[pos] in (0x20, 0x00, 0x0A, 0x0D):
            pos += 1
        if pos >= n:
            break
        record_no += 1
        rec_start = pos

        declared_length = _ascii_digits(data, pos, 5)
        if declared_length is None or declared_length < MIN_RECORD_LEN:
            # Garbage / non-digit leader. Bound the raw capture at the next
            # reliable record so the bad bytes are preserved without
            # swallowing healthy neighbours.
            nxt = _find_next_record(data, pos + 1)
            raw = data[pos:nxt] if nxt is not None else data[pos:]
            records.append(
                _invalid_record(
                    record_no,
                    pos,
                    raw,
                    [
                        ParseIssue(
                            level="error",
                            code="bad_leader_length",
                            message=(
                                "Leader bytes 00-04 do not hold a valid record "
                                "length; record cannot be parsed from its directory"
                            ),
                            byte_offset=pos,
                            raw_hex=raw[:40].hex(" ").upper(),
                            record_no=record_no,
                        )
                    ],
                )
            )
            if nxt is None:
                file_issues.append(
                    ParseIssue(
                        level="fatal",
                        code="cannot_relocate",
                        message=(
                            "Corrupt leader with no verifiable MARC record later "
                            "in the file; parsing stopped"
                        ),
                        byte_offset=pos,
                    )
                )
                break
            pos = nxt
            continue

        declared_end = pos + declared_length
        if declared_end > n:
            nxt = _find_next_record(data, pos + 1)
            raw = data[pos:nxt] if nxt is not None else data[pos:]
            records.append(
                _invalid_record(
                    record_no,
                    pos,
                    raw,
                    [
                        ParseIssue(
                            level="error",
                            code="length_exceeds_file",
                            message=(
                                f"Declared record length {declared_length} runs "
                                f"past end of file ({n - pos} bytes available)"
                            ),
                            byte_offset=pos,
                            raw_hex=raw[:40].hex(" ").upper(),
                            record_no=record_no,
                        )
                    ],
                )
            )
            if nxt is None:
                file_issues.append(
                    ParseIssue(
                        level="fatal",
                        code="cannot_relocate",
                        message=(
                            "Corrupt length with no verifiable following record; "
                            "parsing stopped"
                        ),
                        byte_offset=pos,
                    )
                )
                break
            pos = nxt
            continue

        chunk = data[pos:declared_end]
        rec_issues: list[ParseIssue] = []

        if chunk[-1] != RECORD_TERMINATOR:
            rec_issues.append(
                ParseIssue(
                    level="error",
                    code="missing_record_terminator",
                    message=(
                        f"Byte at declared end (offset {declared_end - 1}) is "
                        f"0x{chunk[-1]:02X}, not record terminator 0x1D"
                    ),
                    byte_offset=declared_end - 1,
                    raw_hex=bytes([chunk[-1]]).hex(" ").upper(),
                    record_no=record_no,
                )
            )

        leader = chunk[:LEADER_SIZE]
        base_address = _ascii_digits(leader, 12, 5)
        if base_address is None or base_address >= len(chunk):
            rec_issues.append(
                ParseIssue(
                    level="error",
                    code="bad_base_address",
                    message=(
                        "Leader bytes 12-16 do not hold a valid base address"
                    ),
                    byte_offset=pos + 12,
                    raw_hex=leader[12:17].hex(" ").upper(),
                    record_no=record_no,
                )
            )

        # Directory: starts at byte 24, must end with a field terminator
        # sitting exactly one byte before the base address.
        directory_end = (
            chunk.find(bytes([FIELD_TERMINATOR]), LEADER_SIZE)
            if base_address is not None
            else -1
        )
        if (
            base_address is None
            or directory_end == -1
            or directory_end + 1 != base_address
        ):
            rec_issues.append(
                ParseIssue(
                    level="error",
                    code="bad_directory",
                    message=(
                        "Directory is not terminated by 0x1E immediately "
                        "before the declared base address"
                    ),
                    byte_offset=pos + LEADER_SIZE,
                    record_no=record_no,
                )
            )
        directory_bytes = (
            chunk[LEADER_SIZE:directory_end] if directory_end != -1 else b""
        )
        if len(directory_bytes) % DIRECTORY_ENTRY_SIZE != 0:
            rec_issues.append(
                ParseIssue(
                    level="error",
                    code="bad_directory_entry_size",
                    message=(
                        f"Directory length {len(directory_bytes)} is not a "
                        "multiple of the 12-byte entry size"
                    ),
                    byte_offset=pos + LEADER_SIZE,
                    raw_hex=directory_bytes[:24].hex(" ").upper(),
                    record_no=record_no,
                )
            )

        entries: list[tuple[str, int, int]] = []
        if directory_bytes and base_address is not None:
            for idx in range(0, len(directory_bytes), DIRECTORY_ENTRY_SIZE):
                entry = directory_bytes[idx : idx + DIRECTORY_ENTRY_SIZE]
                tag = entry[0:3].decode("ascii", errors="replace")
                field_len = _ascii_digits(entry, 3, 4)
                start_pos = _ascii_digits(entry, 7, 5)
                if not _is_tag(entry[0:3]) or field_len is None or start_pos is None:
                    rec_issues.append(
                        ParseIssue(
                            level="error",
                            code="bad_directory_entry",
                            message=(
                                f"Directory entry {idx // 12} "
                                f"({entry.hex(' ').upper()}) has an invalid tag, "
                                "length or position"
                            ),
                            byte_offset=pos + LEADER_SIZE + idx,
                            raw_hex=entry.hex(" ").upper(),
                            record_no=record_no,
                        )
                    )
                    continue
                entries.append((tag, field_len, start_pos))

        # Cross-check: actual terminator position vs declared length.
        rt_relative = chunk.rfind(b"\x1d")
        if (
            rt_relative != -1
            and rt_relative != declared_length - 1
            and not any(i.code == "missing_record_terminator" for i in rec_issues)
        ):
            rec_issues.append(
                ParseIssue(
                    level="warning",
                    code="length_mismatch",
                    message=(
                        f"Record terminator found at byte {rt_relative} but "
                        f"declared length implies {declared_length - 1}"
                    ),
                    byte_offset=pos + rt_relative,
                    record_no=record_no,
                )
            )

        encoding = leader_encoding(leader)
        leader_text = _render_leader(leader)

        if any(i.level == "error" for i in rec_issues) or not entries:
            # Structural failure: keep raw bytes, do not attempt a field
            # split, then resynchronise at the next *verified* record.
            records.append(
                _invalid_record(
                    record_no,
                    pos,
                    chunk,
                    rec_issues,
                    leader_text=leader_text,
                    declared_length=declared_length,
                    base_address=base_address,
                    encoding=encoding,
                )
            )
            nxt = _find_next_record(data, rec_start + 1)
            if nxt is None:
                file_issues.append(
                    ParseIssue(
                        level="fatal",
                        code="cannot_relocate",
                        message=(
                            "Structurally invalid record with no verifiable "
                            "following record; parsing stopped"
                        ),
                        byte_offset=rec_start,
                    )
                )
                break
            pos = nxt
            continue

        # Parse fields strictly from directory byte offsets/lengths.
        fields: list[FieldRep] = []
        data_area = chunk[base_address:]
        running_end = 0
        for order, (tag, field_len, start_pos) in enumerate(entries):
            abs_field = pos + base_address + start_pos
            field_bytes = data_area[start_pos : start_pos + field_len]
            if len(field_bytes) != field_len:
                rec_issues.append(
                    ParseIssue(
                        level="error",
                        code="field_outside_record",
                        message=(
                            f"Directory entry for {tag} (length {field_len} at "
                            f"+{start_pos}) extends past the record data area"
                        ),
                        byte_offset=abs_field,
                        record_no=record_no,
                    )
                )
                continue
            # A directory-listed field must end with the field terminator.
            if not field_bytes.endswith(b"\x1e"):
                rec_issues.append(
                    ParseIssue(
                        level="warning",
                        code="missing_field_terminator",
                        message=(
                            f"Field {tag} (occurrence {order}) of declared "
                            f"length {field_len} does not end with 0x1E"
                        ),
                        byte_offset=abs_field + field_len - 1,
                        record_no=record_no,
                    )
                )
            fields.append(
                _parse_field(tag, field_bytes, abs_field, order, encoding)
            )
            running_end = max(running_end, start_pos + field_len)

        # Verify the data area ends exactly at the record terminator.
        trailing = data_area[running_end:]
        if trailing not in (b"\x1d", b"\x1e\x1d", b""):
            rec_issues.append(
                ParseIssue(
                    level="warning",
                    code="trailing_data_bytes",
                    message=(
                        f"{len(trailing)} byte(s) after the last directory-listed "
                        "field do not belong to any entry"
                    ),
                    byte_offset=pos + base_address + running_end,
                    raw_hex=trailing[:20].hex(" ").upper(),
                    record_no=record_no,
                )
            )

        status = "ok" if not rec_issues else "recovered"
        records.append(
            ParsedRecord(
                record_no=record_no,
                leader=leader_text,
                leader_bytes=leader,
                declared_length=declared_length,
                actual_length=len(chunk),
                byte_offset=rec_start,
                base_address=base_address,
                encoding=encoding,
                fields=fields,
                issues=rec_issues,
                raw_hex=chunk.hex(" ").upper(),
                status=status,
            )
        )
        pos = declared_end

    return records, file_issues


def _render_leader(leader: bytes) -> str:
    """Render a Leader, showing non-printable/non-ASCII control bytes as dots."""
    chars = []
    for b in leader:
        if 0x20 <= b <= 0x7E:
            chars.append(chr(b))
        else:
            chars.append(".")
    return "".join(chars)


def _invalid_record(
    record_no: int,
    offset: int,
    raw: bytes,
    issues: list[ParseIssue],
    leader_text: str = "",
    declared_length: int | None = None,
    base_address: int | None = None,
    encoding: EncodingName = "marc8",
) -> ParsedRecord:
    return ParsedRecord(
        record_no=record_no,
        leader=leader_text or _render_leader(raw[:LEADER_SIZE]),
        leader_bytes=raw[:LEADER_SIZE],
        declared_length=declared_length if declared_length is not None else -1,
        actual_length=len(raw),
        byte_offset=offset,
        base_address=base_address if base_address is not None else -1,
        encoding=encoding,
        fields=[],
        issues=issues,
        raw_hex=raw.hex(" ").upper(),
        status="invalid",
    )


def parse_file(path: str | Path) -> tuple[list[ParsedRecord], list[ParseIssue]]:
    """Read an ISO 2709 file from disk and parse it byte-for-byte."""
    raw = Path(path).read_bytes()
    return parse_records(raw)
