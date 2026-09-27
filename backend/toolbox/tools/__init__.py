"""M9 工具子包：vaspkit 探测/技能 + SLURM 模板 + 提交草稿（只生成不执行）。"""
from .draft import (
    SUBMIT_BIN,
    SubmissionDraft,
    SubmissionDraftBuilder,
    make_draft_only_submitter,
    submit_command,
)
from .slurm import (
    DEFAULT_DIRECTIVES,
    DIRECTIVE_ALLOWLIST,
    default_directives,
    render_sbatch,
    sanitize_text,
    validate_directives,
)
from .vaspkit import (
    VASPKIT_TASKS,
    VaspkitSkill,
    probe_and_store,
    probe_vaspkit,
    store_path,
)

__all__ = [
    "SUBMIT_BIN", "SubmissionDraft", "SubmissionDraftBuilder",
    "make_draft_only_submitter", "submit_command",
    "DEFAULT_DIRECTIVES", "DIRECTIVE_ALLOWLIST", "default_directives",
    "render_sbatch", "sanitize_text", "validate_directives",
    "VASPKIT_TASKS", "VaspkitSkill", "probe_and_store", "probe_vaspkit",
    "store_path",
]
