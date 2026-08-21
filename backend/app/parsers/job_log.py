from __future__ import annotations

import re
from typing import Optional

from ..schemas.parsed import JobLogData


_KEYWORD_PATTERNS = [
    ("oom", r"(?i)(out of memory|memory cgroup|killed process|oom-kill|memoryerror|cannot allocate memory)"),
    ("time_limit", r"(?i)(due to time limit|time limit exceeded|DUE TO TIME LIMIT|CANCELLED AT.*TIME)"),
    ("module", r"(?i)(module[ _]not[ _]found|no module named|import error|importerror)"),
    ("path", r"(?i)(no such file|not found|file not found|command not found|permission denied|cannot open)"),
    ("scheduler", r"(?i)(srun:|sbatch:|qsub:|bsub:|job id|jobstate)"),
    ("signal", r"(?i)(segmentation fault|core dumped|terminated|term-signal|bus error)"),
]

DEFAULT_TAIL = 40


def parse_job_log(text: str, path: str = "", tail: int = DEFAULT_TAIL) -> JobLogData:
    data = JobLogData(path=path)
    lines = text.splitlines()
    data.tail_lines = lines[-tail:] if tail > 0 else lines
    for i, line in enumerate(lines):
        for cat, pat in _KEYWORD_PATTERNS:
            if re.search(pat, line):
                data.keywords.append({
                    "category": cat,
                    "line": i + 1,
                    "text": line.strip(),
                })
                break
    return data