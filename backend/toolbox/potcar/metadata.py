"""Decode bounded bytes and expose only a metadata allowlist, never body text."""
from __future__ import annotations

import gzip
import hashlib
import io
import math
import re
import time

from backend.input_validation import InputValidationError, validate_potcar
from ..contracts import ToolboxError
from .filesystem import fail

_TAGS = re.compile(r'(?i)\b(TITEL|VRHFIN|LEXCH|ZVAL|ENMAX)\s*=\s*(.*?)(?=\s*;|\s+\b[A-Z][A-Z0-9_]*\s*=|$)', re.MULTILINE)
_TITLE = re.compile(r'^([A-Z][A-Z0-9_]*)\s+([A-Z][a-z]?(?:(?:_[A-Za-z0-9]+)+|(?:\d+(?:\.\d+)?|\.\d+))?)(?:\s+(\d{1,2}[A-Za-z]{3}\d{2,4}|\d{4}-\d{2}-\d{2}))?(?:\s|$)')
_SPECIES = re.compile(r'^([A-Z][a-z]?)\s*:')
_NUMBER = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?$')
_FAMILIES = {'PAW', 'PAW_PBE', 'PAW_GGA', 'PAW_RPBE', 'US', 'NC'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw, name, limits, *, timeout=None):
    if len(raw) > limits.max_input:
        fail('INPUT_LIMIT', '候选超过压缩输入大小上限')
    compression = 'gzip' if raw.startswith(b'\x1f\x8b') else 'Z' if raw.startswith(b'\x1f\x9d') else 'raw'
    suffix = name.lower()
    if (suffix.endswith('.gz') and compression != 'gzip') or (suffix.endswith('.z') and compression != 'Z') or (compression != 'raw' and not suffix.endswith('.gz' if compression == 'gzip' else '.z')):
        fail('FORMAT_CONFLICT', '文件扩展名与压缩魔数不一致')
    started = time.monotonic()
    budget = min(limits.decode_timeout, timeout) if timeout is not None else limits.decode_timeout
    if budget <= 0:
        fail('DECODE_TIMEOUT', '单文件解码超过时间上限', 400, True)
    try:
        if compression == 'gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                decoded = stream.read(limits.max_output + 1)
        elif compression == 'Z':
            from ._unlzw import unlzw
            decoded = bytes(unlzw(raw, max_input=limits.max_input, max_output=limits.max_output, timeout=budget))
        else:
            decoded = raw
    except ToolboxError:
        raise
    except TimeoutError:
        fail('DECODE_TIMEOUT', '单文件解码超过时间上限', 400, True)
    except ValueError as exc:
        if str(exc) in {'INPUT_LIMIT', 'OUTPUT_LIMIT', 'DECODE_TIMEOUT'}:
            messages = {'INPUT_LIMIT': '候选超过压缩输入大小上限', 'OUTPUT_LIMIT': '解码数据超过单数据集大小上限',
                        'DECODE_TIMEOUT': '单文件解码超过时间上限'}
            fail(str(exc), messages[str(exc)], retryable=str(exc) == 'DECODE_TIMEOUT')
        fail('DECODE_INVALID', '压缩数据损坏或截断')
    except (OSError, EOFError, OverflowError):
        fail('DECODE_INVALID', '压缩数据损坏、截断或超过解码上限')
    if time.monotonic() - started > budget:
        fail('DECODE_TIMEOUT', '单文件解码超过时间上限', 400, True)
    if len(decoded) > limits.max_output:
        fail('OUTPUT_LIMIT', '解码数据超过单数据集大小上限')
    return decoded, compression


def issue(code, message):
    return {'code': code, 'message': message}


def metadata(raw, row):
    # Decoder limits bound text creation. Shared validation remains authoritative.
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        row['issues'].append(issue('INPUT_ENCODING_INVALID', '数据集不是可验证的 UTF-8 文本'))
        return
    values = {}
    for match in _TAGS.finditer(text):
        values.setdefault(match[1].upper(), []).append(match[2].strip())
    conflict = False
    for tag, matches in values.items():
        if len(matches) != 1:
            conflict = True
            row['issues'].append(issue('POTCAR_METADATA_CONFLICT', '数据集包含重复或冲突的头部字段'))
        limit = {'TITEL': 256, 'VRHFIN': 128, 'LEXCH': 8, 'ZVAL': 100, 'ENMAX': 100}[tag]
        if any(len(value) > limit for value in matches):
            conflict = True
            row['issues'].append(issue('POTCAR_METADATA_LIMIT', '头部元数据超过允许长度；不保存原始字段'))
            values[tag] = ['']
    title = _TITLE.match((values.get('TITEL') or [''])[0])
    if title:
        family, variant, date = title.groups()
        if len(variant) > 128:
            row['issues'].append(issue('POTCAR_METADATA_LIMIT', '变体名称超过允许长度；不保存原始字段'))
            return
        row.update(family=family if family in _FAMILIES else None,
                   variant=variant, dataset_date=date,
                   title=' '.join(part for part in (family if family in _FAMILIES else None, variant, date) if part))
        species = _SPECIES.match((values.get('VRHFIN') or [''])[0])
        if species:
            row['element'] = species[1]
            if re.match(r'^[A-Z][a-z]?', variant)[0] != species[1]:
                conflict = True
                row['issues'].append(issue('POTCAR_SPECIES_CONFLICT', 'TITEL 与 VRHFIN 物种不一致'))
        if re.search(r'\.\d+', variant):
            row['status'] = 'unsupported'
            row['issues'].append(issue('POTCAR_VARIANT_UNSUPPORTED', '特殊小数变体尚未支持，不能用于首版选择'))
    lexch = (values.get('LEXCH') or [''])[0]
    row['lexch'] = lexch if re.fullmatch(r'[A-Za-z]{1,8}', lexch) else None
    for tag, field in [('ZVAL', 'zval'), ('ENMAX', 'enmax_ev')]:
        value = (values.get(tag) or [''])[0]
        # VASP headers append standard human-readable labels to some values,
        # notably "ZVAL = 4.000 mass and valenz". Permit those exact labels,
        # not an arbitrary numeric prefix followed by unknown text.
        tokens = value.split(maxsplit=1)
        numeric = tokens[0] if tokens else ''
        suffix = ' '.join(tokens[1].casefold().split()) if len(tokens) > 1 else ''
        allowed_suffixes = {'', 'mass and valenz', 'mass and valence'} if tag == 'ZVAL' else {'', 'ev'}
        if _NUMBER.fullmatch(numeric) and len(value) < 100 and suffix in allowed_suffixes:
            number = float(numeric.replace('D', 'E').replace('d', 'e'))
            if math.isfinite(number):
                row[field] = number
        if tag in values and row[field] is None:
            conflict = True
            row['issues'].append(issue('POTCAR_NUMERIC_METADATA_INVALID', '数值元数据无法可靠识别'))
    if row['family'] == 'PAW_PBE' and row['lexch'] is not None and row['lexch'].upper() != 'PE':
        conflict = True
        row['issues'].append(issue('POTCAR_FAMILY_CONFLICT', 'PAW-PBE 标题与 LEXCH 标识不一致'))
    try:
        species = validate_potcar(raw, None)
        if len(species) != 1:
            row['issues'].append(issue('POTCAR_MULTIPLE_DATASETS', '单元素候选含多个数据集，不能自动采用'))
        elif title and not conflict and row['status'] != 'unsupported':
            row['element'] = species[0]
            row['status'] = 'ready' if row['family'] == 'PAW_PBE' else 'unsupported'
            if row['status'] == 'unsupported':
                row['issues'].append(issue('POTCAR_FAMILY_UNSUPPORTED', '此体系不在首版 PAW-PBE 选择范围'))
    except InputValidationError as exc:
        row['issues'].append(issue(exc.code, exc.message))
    if conflict:
        row['status'] = 'invalid'


def dataset(library_id, relative_path):
    return {'dataset_id': digest((library_id + '\0' + relative_path).encode('utf-8'))[:32],
            'library_id': library_id, 'relative_path': relative_path,
            **dict.fromkeys(['compression', 'element', 'variant', 'family', 'lexch', 'zval', 'enmax_ev', 'dataset_date', 'title', 'decoded_sha256', 'source_sha256', 'duplicate_of']),
            'status': 'invalid', 'issues': []}


def mark_duplicates(rows):
    hashes = {}
    variants = {}
    for row in rows:
        if row['decoded_sha256']:
            aliases = hashes.setdefault(row['decoded_sha256'], [])
            if aliases:
                row['duplicate_of'] = aliases[0]['dataset_id']
                row['issues'].append(issue('POTCAR_DUPLICATE_CONTENT', '与另一候选解码后内容相同；保留独立路径供查看'))
            aliases.append(row)
        if row['status'] == 'ready':
            variants.setdefault((row['family'], row['variant']), []).append(row)
    for group in variants.values():
        if len({row['decoded_sha256'] for row in group}) > 1:
            for row in group:
                row['status'] = 'ambiguous'
                row['issues'].append(issue('POTCAR_VARIANT_AMBIGUOUS', '相同变体存在不同内容；必须明确区分数据集'))
