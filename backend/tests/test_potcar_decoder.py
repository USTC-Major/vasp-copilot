import pytest

from backend.toolbox.potcar import _unlzw


def _pack_nine_bit_codes(codes, *, align=False):
    packed = sum(code << (9 * index) for index, code in enumerate(codes))
    length = (len(codes) * 9 + 7) // 8
    if align:
        length = ((length + 8) // 9) * 9
    return packed.to_bytes(length, "little")


def _literal_z(data, *, block=True):
    """Pack literal-only 9-bit codes into a small synthetic Unix .Z stream."""
    header = b"\x1f\x9d" + bytes([0x90 if block else 0x10])
    chunks = [data[index:index + 128] for index in range(0, len(data), 128)]
    if not block:
        assert len(data) < 250
        chunks = [data]

    stream = bytearray(header)
    for index, chunk in enumerate(chunks):
        more = index < len(chunks) - 1
        codes = list(chunk) + ([256] if more else [])
        stream.extend(_pack_nine_bit_codes(codes, align=more))
    return bytes(stream)


@pytest.mark.parametrize(
    ("payload", "block"),
    [
        (bytes(range(256)), True),
        (b"literal .Z bytes: \x00\xff\n", False),
    ],
)
def test_literal_stream_decodes_to_exact_bytes(payload, block):
    assert _unlzw.unlzw(_literal_z(payload, block=block)) == payload


def test_invalid_header_is_rejected():
    with pytest.raises(ValueError, match="Incorrect magic bytes"):
        _unlzw.unlzw(b"badheader")


def test_invalid_code_is_rejected():
    stream = b"\x1f\x9d\x10" + _pack_nine_bit_codes([65, 300])
    with pytest.raises(ValueError, match="Invalid code detected"):
        _unlzw.unlzw(stream)


def test_output_limit_is_checked_before_append():
    stream = _literal_z(b"ABC", block=False)
    assert _unlzw.unlzw(stream, max_output=3) == b"ABC"
    with pytest.raises(ValueError, match="OUTPUT_LIMIT"):
        _unlzw.unlzw(stream, max_output=2)


def test_input_limit_is_checked_before_header_parsing():
    with pytest.raises(ValueError, match="INPUT_LIMIT"):
        _unlzw.unlzw(b"not a .Z stream", max_input=3)


def test_decode_deadline_can_be_forced(monkeypatch):
    ticks = iter((10.0, 10.2))
    monkeypatch.setattr(_unlzw, "monotonic", lambda: next(ticks))
    stream = _literal_z(b"AB", block=False)

    with pytest.raises(ValueError, match="DECODE_TIMEOUT"):
        _unlzw.unlzw(stream, timeout=0.1)
