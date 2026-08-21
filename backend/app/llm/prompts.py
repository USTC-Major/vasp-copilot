from __future__ import annotations

from ..schemas.result import DiagnosisResult

INSUFFICIENT_PREFIX = '无法判断，需要补充文件：'


def insufficient_line(missing: list) -> str:
    files = '、'.join(missing) if missing else '未知'
    return INSUFFICIENT_PREFIX + files


def _dump(result: DiagnosisResult) -> str:
    lines = []
    lines.append('诊断状态: ' + result.diagnosis_status.value)
    lines.append('总体判断: ' + (result.summary or '无'))
    lines.append('是否允许进入下一步: ' + str(result.next_step.allowed)
                 + ('；原因: ' + result.next_step.reason if result.next_step.reason else ''))
    lines.append('问题列表:')
    if not result.issues:
        lines.append('  无')
    for i in result.issues:
        lines.append('  [' + i.severity.value.upper() + '] ' + i.title
                     + ' | ' + (i.summary or ''))
        if i.possible_causes:
            lines.append('    可能原因: ' + '；'.join(i.possible_causes))
        for r in (i.recommendations or [])[:3]:
            rationale = getattr(r, 'rationale', '') or ''
            lines.append('    处理建议: ' + (rationale or ''))
    if result.missing_evidence:
        lines.append('缺失证据: ' + '、'.join(result.missing_evidence))
    if result.recommended_fixes:
        lines.append('可用自动修复: '
                     + ','.join(getattr(f, 'fix_id', '?') for f in result.recommended_fixes))
    return chr(10).join(lines)


_SYSTEM = (
    '你是 VASP-Doctor 的诊断解释助手。你只能基于我提供的结构化诊断结果作答，'
    '不得读取、推测或编造任何原始文件内容。要求：'
    '1) 用通俗中文解释问题、可能原因与处理步骤；'
    '2) 不添加结构化结果中未出现的新诊断；'
    '3) 涉及 DFT+U、磁矩、资源/计算量等数值性修改时，只提示需人工核验，不给具体数值；'
    '4) 若结果标明存在缺失证据，必须输出固定句式：' + INSUFFICIENT_PREFIX + '[缺失文件]；'
    '5) 若结果不允许进入下一步（allowed=false），必须明确指出不能继续并给出原因。'
)


def build_explain_messages(result: DiagnosisResult) -> list:
    user = (
        '请基于以下诊断结果给出易懂的中文说明，包含：一、总体判断；二、逐条问题说明；'
        '三、建议的处理步骤；四、能否进入下一步。' + chr(10) * 2 + _dump(result)
    )
    return [{'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': user}]


def build_chat_messages(result: DiagnosisResult, question: str) -> list:
    user = (
        '以下是诊断结果，请结合它回答用户追问。只能依据该结果，不得编造。' + chr(10) * 2
        + _dump(result) + chr(10) * 2 + '用户追问：' + question
    )
    return [{'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': user}]


_ASSISTANT_SYSTEM = (
    '你是 VASP-Copilot / VASP-Doctor 内置的 AI 助手。你可以帮助用户完成材料计算（尤其是 VASP）'
    '相关的问答：解析报错、解释物理概念（DFT、k 点、ENCUT、U 值、磁矩、收敛等）、给出排查与参数建议。'
    '要求：1) 用通俗中文回答，结构清晰；2) 涉及具体数值（如 ENCUT、KPOINTS、U 值）时说明需根据体系人工核验，'
    '不给出确定性数值；3) 与 VASP 无关的问题也可正常解答，但优先考虑材料计算场景；'
    '4) 不得编造你不了解的文件内容。'
)


def build_general_chat_messages(question: str, history: 'list | None' = None) -> list:
    messages = [{'role': 'system', 'content': _ASSISTANT_SYSTEM}]
    for item in (history or []):
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        content = item.get('content')
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': question})
    return messages