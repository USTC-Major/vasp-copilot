"""Disposable parser process: bounded lifetime, no network or credentials."""
from pathlib import Path
import json
import sys
import os
import threading

from .parser import parse
from ..contracts import ToolboxError


def main():
    # Also expire if the parent service crashes before it can enforce its timeout.
    deadline = threading.Timer(95, lambda: os._exit(124))
    deadline.daemon = True
    deadline.start()
    directory, kind, target = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    try:
        result = {'result': parse(directory, kind)}
    except ToolboxError as exc:
        result = {'error': exc.payload()}
    except Exception:
        # Parser exceptions may contain private input text/paths. Keep them local.
        result = {'error': {'code': 'PP_PARSE_FAILED', 'message': '文件不完整、格式未支持或文件组合不匹配，请检查同次计算的原始输出', 'retryable': False}}
    target.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
