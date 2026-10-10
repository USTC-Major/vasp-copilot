"""One bounded CSV parser shared by preview and atomic import."""
from __future__ import annotations

import csv
import io
import json
import re

from pydantic import ValidationError

from ...contracts import ToolboxError
from ..store import fail
from .calculation import FIELDS, composition, finite
from .schemas import Manual

HEADERS = ['name', 'composition', 'energy_basis', 'energy_ev', 'unit', 'relative_path', 'reference_note']
CSV_METADATA_LIMIT = 32768


def csv_metadata(raw, basis):
    """Keep bounded file-provided notes separate from the user's reference note."""
    value = json.loads(raw.get('csv_metadata') or '{}')
    if not isinstance(value, dict) or set(value) - {'source_notes', 'override_notes', 'original_energies', 'value_source'}:
        raise ValueError('CSV来源说明结构无效')
    declared_source = (raw.get('value_source') or '').strip()
    if declared_source not in {'', 'original', 'effective'} or value.get('value_source') not in {None, 'original', 'effective'}:
        raise ValueError('CSV取值方式必须为original或effective，不会按数值推断')
    result = {'source_notes': [], 'override_notes': [], 'original_energies': [], 'value_source': declared_source or None}
    for key, column, limit in (('source_notes', 'source_note', 6000), ('override_notes', 'override_note', 2000)):
        notes = value.get(key, [])
        if not isinstance(notes, list):
            raise ValueError('CSV附带说明必须为文本列表')
        for note in notes + [raw.get(column) or '']:
            if not isinstance(note, str) or len(note) > limit:
                raise ValueError(f'{column}最多{limit}字')
            if note and note not in result[key]:
                result[key].append(note)
    energies = value.get('original_energies', [])
    if not isinstance(energies, list):
        raise ValueError('CSV附带原始能量必须为列表')
    original = (raw.get('original_energy_ev') or '').strip()
    if original:
        energies = energies + [{'energy_basis': basis, 'energy_ev': float(original)}]
    for energy in energies:
        if (not isinstance(energy, dict) or set(energy) != {'energy_basis', 'energy_ev'}
                or energy['energy_basis'] not in FIELDS or not finite(energy['energy_ev'])):
            raise ValueError('CSV附带原始能量必须声明有效口径和有限数值')
        if energy not in result['original_energies']:
            result['original_energies'].append(energy)
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > CSV_METADATA_LIMIT:
        raise ValueError(f'CSV附带来源说明合计最多{CSV_METADATA_LIMIT}字，不会截断')
    return result


def restore_csv_text(value):
    if value.startswith("''") or (value.startswith("'") and value[1:].lstrip(' \r\n').startswith(('=', '+', '-', '@', '\t'))):
        return value[1:]
    return value


def readable_composition(value):
    text = value.strip()
    if text.startswith('{'):
        return composition(json.loads(text))
    result, position = {}, 0
    token = re.compile(r'([A-Z][a-z]?)\s*:\s*([0-9]+)')
    for match in token.finditer(text):
        if text[position:match.start()].strip(' \t;,') or match.group(1) in result:
            fail('组成格式应为 Pt:4 C:1 O:1，元素不能重复', 'ENERGY_COMPOSITION_REQUIRED')
        result[match.group(1)] = int(match.group(2))
        position = match.end()
    if text[position:].strip(' \t;,'):
        fail('组成格式应为 Pt:4 C:1 O:1，或旧JSON元素计数', 'ENERGY_COMPOSITION_REQUIRED')
    return composition(result)


def parse(data, revision, default_basis=None, max_samples=100):
    from .store import display_path, name_valid
    if len(data) > 1024 * 1024:
        fail('CSV最多1 MiB', 'ENERGY_TOO_LARGE', 413)
    if default_basis is not None and default_basis not in FIELDS:
        fail('默认能量口径无效', 'ENERGY_BASIS_CONFLICT')
    try:
        decoded = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        fail('CSV必须为UTF-8文本，可含BOM', 'ENERGY_ENCODING_INVALID')
    rows, global_issues = [], []
    # DictReader silently skips blank records, hiding the physical start line.
    reader = csv.reader(io.StringIO(decoded, newline=''), strict=True)
    try:
        headers = [header.strip() for header in next(reader, [])]
        required = {'name', 'composition', 'energy_ev', 'unit'}
        if default_basis is None:
            required.add('energy_basis')
        missing = required - set(headers)
        if missing or len(headers) != len(set(headers)):
            global_issues.append({'code': 'ENERGY_CSV_INVALID', 'message': 'CSV缺少必需列或列名重复：' + ', '.join(sorted(missing))})
        previous_line = reader.line_num
        for values in reader:
            line = previous_line + 1
            previous_line = reader.line_num
            if not values:
                continue
            raw = {header: values[index] if index < len(values) else None for index, header in enumerate(headers)}
            if len(values) > len(headers):
                raw[None] = values[len(headers):]
            if raw.get('csv_text_encoding') == 'apostrophe-prefix':
                for key in ('name', 'relative_path', 'reference_note', 'override_note', 'source_note'):
                    if isinstance(raw.get(key), str):
                        raw[key] = restore_csv_text(raw[key])
            if not any(value for value in raw.values() if value is not None):
                continue
            if len(rows) >= max_samples:
                global_issues.append({'code': 'ENERGY_QUOTA', 'message': f'CSV最多{max_samples}行'})
                break
            row = {'row_number': line, 'name': raw.get('name') or '', 'composition': None,
                   'energy_basis': None, 'energy_ev': None, 'unit': (raw.get('unit') or '').strip(),
                   'relative_path': raw.get('relative_path') or None, 'reference_note': raw.get('reference_note') or '',
                   'csv_metadata': None, 'issues': []}

            def issue(code, message):
                row['issues'].append({'code': code, 'message': message})

            if None in raw:
                issue('ENERGY_CSV_INVALID', '本行列数量多于CSV表头')
            try:
                name_valid(row['name'])
            except ToolboxError as exc:
                issue(exc.code, str(exc))
            try:
                row['composition'] = readable_composition(raw.get('composition') or '')
            except (ToolboxError, ValueError, TypeError) as exc:
                issue('ENERGY_COMPOSITION_REQUIRED', '组成必须为明确元素的正整数计数：' + str(exc))
            declared = (raw.get('energy_basis') or '').strip()
            if declared and declared not in FIELDS:
                issue('ENERGY_BASIS_CONFLICT', '文件声明的能量口径无效')
            elif declared and default_basis is not None and declared != default_basis:
                issue('ENERGY_BASIS_CONFLICT', '文件能量口径与所选默认口径不同，请选择按文件声明或统一文件')
            else:
                row['energy_basis'] = declared or default_basis
                if row['energy_basis'] is None:
                    issue('ENERGY_BASIS_CONFLICT', '本行缺少能量口径，请在文件声明或选择明确默认值')
            try:
                value = float((raw.get('energy_ev') or '').strip())
                if not finite(value):
                    raise ValueError('nonfinite')
                row['energy_ev'] = value
            except (ValueError, TypeError):
                issue('ENERGY_FIELD_REQUIRED', '能量不能为空且必须为有限数值，不会补零')
            if row['unit'] != 'eV':
                issue('ENERGY_CSV_INVALID', '能量单位必须为eV')
            try:
                row['relative_path'] = display_path(row['relative_path'])
            except ToolboxError as exc:
                issue(exc.code, str(exc))
            try:
                row['csv_metadata'] = csv_metadata(raw, row['energy_basis'])
            except (ValueError, TypeError, OverflowError) as exc:
                issue('ENERGY_CSV_INVALID', '附带来源说明无效：' + str(exc))
            if not row['issues']:
                try:
                    Manual.model_validate({'expected_revision': revision, 'name': row['name'], 'composition': row['composition'],
                        'energy_fields': {row['energy_basis']: row['energy_ev']}, 'energy_basis': row['energy_basis'],
                        'unit': row['unit'], 'reference_note': row['reference_note']})
                except ValidationError:
                    issue('ENERGY_CSV_INVALID', '名称、组成、能量或来源说明超出请求限制')
            rows.append(row)
    except csv.Error as exc:
        global_issues.append({'code': 'ENERGY_CSV_INVALID', 'message': 'CSV引用或列格式无效：' + str(exc)})
    if not rows:
        global_issues.append({'code': 'ENERGY_CSV_INVALID', 'message': 'CSV没有样本'})
    valid = sum(not row['issues'] for row in rows)
    return {'row_count': len(rows), 'valid_count': valid, 'can_import': bool(rows) and valid == len(rows) and not global_issues,
            'rows': rows, 'issues': global_issues}
