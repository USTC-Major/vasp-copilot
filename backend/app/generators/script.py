"""配置化 ScriptGenerator（设计文档 7.10 节）。

- scheduler 行为完全由 ``SchedulerProfile`` + Jinja2 模板驱动；
- 代码内不硬编码 sbatch/salloc 分支逻辑；
- 只渲染脚本文本，绝不执行任何命令（submit 仅作为 hint 展示）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from backend.app.recipes.errors import BeAError
from backend.app.schemas.generation import SchedulerProfile, SchedulerSettings

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "scheduler"

DEFAULT_PROFILES: Dict[str, SchedulerProfile] = {
    "slurm": SchedulerProfile(
        scheduler_type="slurm",
        script_template="slurm.sh.j2",
        launcher_prefix="srun",
        submit_command_hint="sbatch submit.sh",
    ),
    "cbatch": SchedulerProfile(
        scheduler_type="cbatch",
        script_template="cbatch.sh.j2",
        launcher_prefix="mpirun",
        submit_command_hint="cbatch submit.sh",
    ),
    "generic": SchedulerProfile(
        scheduler_type="generic",
        script_template="generic.sh.j2",
        launcher_prefix="mpirun",
        submit_command_hint="sh submit.sh  # 手动提交",
    ),
}


class ScriptGenerator:
    """渲染 submit.sh；未知 scheduler 类型 fail closed。"""

    def __init__(
        self,
        profiles: Optional[Dict[str, SchedulerProfile]] = None,
        template_dir: Optional[Path] = None,
    ) -> None:
        self._profiles = dict(profiles or DEFAULT_PROFILES)
        directory = Path(template_dir) if template_dir else TEMPLATE_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(directory)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )

    @property
    def supported_types(self):
        return sorted(self._profiles)

    def profile_for(self, scheduler_type: str) -> SchedulerProfile:
        profile = self._profiles.get(scheduler_type)
        if profile is None:
            raise BeAError(
                f"unknown scheduler type: {scheduler_type}",
                code="SCHEDULER_PROFILE_UNKNOWN",
                details={
                    "scheduler_type": scheduler_type,
                    "supported": self.supported_types,
                },
            )
        return profile

    def render(self, settings: SchedulerSettings, step_id: str = "step") -> str:
        profile = self.profile_for(settings.type)
        template = self._env.get_template(profile.script_template)
        total_tasks = max(1, int(settings.nodes) * int(settings.tasks_per_node))
        rendered = template.render(
            settings=settings,
            profile=profile,
            step_id=step_id,
            job_name=settings.job_name or f"bea_{step_id}",
            total_tasks=total_tasks,
            parallel_defaults=settings.parallel_defaults or {},
        )
        return rendered.rstrip("\n") + "\n"
