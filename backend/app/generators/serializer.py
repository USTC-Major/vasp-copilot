"""确定性 IncarSerializer + 轻量 reparse（设计文档 7.5/10.7 节）。

要求：
- 参数名排序稳定输出（同一输入永远得到同一文本）；
- bool → ``.TRUE.`` / ``.FALSE.``；
- 浮点规范格式（整值浮点去掉小数点，非整值 ``.12g``）；
- 数值数组使用 ``n*value`` 连续段压缩（MAGMOM/LDAU 数组）；
- 生成后强制 reparse round-trip 自检，任何不一致 → ``INCAR_ROUNDTRIP_MISMATCH``。

序列化器不读取外部文件、不含时间戳，保证产物可复现。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple, Union

from backend.app.recipes.errors import IncarRoundtripMismatch

Scalar = Union[bool, int, float, str]
ParameterValue = Union[Scalar, List[Scalar]]

_REPEAT_TOKEN = re.compile(r"^(\d+)\*(.+)$")
_INT_TOKEN = re.compile(r"^[+-]?\d+$")

# LDAU 数组按惯例逐元素展开（不用 n*value 压缩），MAGMOM 保持压缩。
_NO_COMPRESS_TAGS = {"LDAUL", "LDAUU", "LDAUJ"}


def format_number(value: Union[int, float]) -> str:
    """规范数值格式：整值浮点写为整数；非整值用 .12g（可无损 round-trip）。"""

    if isinstance(value, bool):
        raise ValueError("bool is not a number")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e16:
            return str(int(value))
        return format(value, ".12g")
    raise ValueError(f"unsupported number type: {type(value)!r}")


def _format_scalar(value: Scalar) -> str:
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, (int, float)):
        return format_number(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or any(ch in text for ch in " \t\n="):
            raise ValueError(f"INCAR string value must be a single token: {value!r}")
        return text
    raise ValueError(f"unsupported INCAR value type: {type(value)!r}")


def _compress_runs(items: List[Scalar]) -> List[str]:
    """连续相同数值用 n*value 压缩；字符串与非数值逐个输出。"""

    tokens: List[str] = []
    i = 0
    while i < len(items):
        item = items[i]
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            tokens.append(_format_scalar(item))
            i += 1
            continue
        run = 1
        while (
            i + run < len(items)
            and isinstance(items[i + run], (int, float))
            and not isinstance(items[i + run], bool)
            and float(items[i + run]) == float(item)
        ):
            run += 1
        rendered = format_number(item)
        tokens.append(f"{run}*{rendered}" if run > 1 else rendered)
        i += run
    return tokens


def parse_number_token(token: str) -> Union[int, float]:
    if _INT_TOKEN.match(token):
        return int(token)
    try:
        return float(token)
    except ValueError as exc:
        raise ValueError(f"not a number: {token!r}") from exc


def parse_value_token(token: str) -> Scalar:
    upper = token.upper()
    if upper == ".TRUE.":
        return True
    if upper == ".FALSE.":
        return False
    try:
        return parse_number_token(token)
    except ValueError:
        return token


def parse_value(text: str) -> ParameterValue:
    """解析单个 INCAR 值文本（支持 n*value 展开）。"""

    tokens = text.split()
    items: List[Scalar] = []
    for token in tokens:
        match = _REPEAT_TOKEN.match(token)
        if match:
            count = int(match.group(1))
            items.extend([parse_value_token(match.group(2))] * count)
        else:
            items.append(parse_value_token(token))
    if len(items) == 1:
        return items[0]
    return items


class IncarParser:
    """极简 INCAR 解析器，仅用于 round-trip 自检（不解析真实计算输出）。"""

    def parse(self, text: str) -> Dict[str, ParameterValue]:
        result: Dict[str, ParameterValue] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if "=" not in line:
                raise ValueError(f"invalid INCAR line (no '='): {raw_line!r}")
            key, _, value = line.partition("=")
            parameter = key.strip().upper()
            if not parameter:
                raise ValueError(f"invalid INCAR line (empty tag): {raw_line!r}")
            if parameter in result:
                raise ValueError(f"duplicate INCAR tag: {parameter}")
            result[parameter] = parse_value(value)
        return result


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_values_equal(x, y) for x, y in zip(a, b))
    return a == b


class IncarSerializer:
    """确定性 INCAR 序列化器；生成后强制 reparse 比对。"""

    def __init__(self, parser: IncarParser | None = None) -> None:
        self._parser = parser or IncarParser()

    def serialize(self, parameters: Dict[str, ParameterValue], *, verify: bool = True) -> str:
        lines: List[str] = []
        for parameter in sorted(parameters):
            value = parameters[parameter]
            lines.append(
                f"{parameter.upper()} = {self._render_value(value, parameter.upper())}"
            )
        text = "\n".join(lines) + "\n"
        if verify:
            self._verify_roundtrip(text, parameters)
        return text

    @staticmethod
    def _render_value(value: ParameterValue, parameter: str = "") -> str:
        if isinstance(value, list):
            if not value:
                raise ValueError("empty list value is not allowed in INCAR")
            if parameter in _NO_COMPRESS_TAGS:
                return " ".join(_format_scalar(item) for item in value)
            return " ".join(_compress_runs(list(value)))
        return _format_scalar(value)

    def _verify_roundtrip(self, text: str, expected: Dict[str, ParameterValue]) -> None:
        try:
            reparsed = self._parser.parse(text)
        except ValueError as exc:
            raise IncarRoundtripMismatch(
                f"INCAR reparse failed: {exc}", details={"reason": str(exc)}
            ) from exc
        diffs: List[Tuple[str, Any, Any]] = []
        for parameter in sorted(set(expected) | set(reparsed)):
            original = expected.get(parameter)
            parsed = reparsed.get(parameter)
            if original is None or parsed is None or not _values_equal(original, parsed):
                diffs.append((parameter, original, parsed))
        if diffs:
            raise IncarRoundtripMismatch(
                "INCAR round-trip mismatch after serialization",
                details={
                    "diffs": [
                        {"parameter": p, "original": repr(o), "reparsed": repr(r)}
                        for p, o, r in diffs
                    ]
                },
            )
