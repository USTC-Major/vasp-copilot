"""配置化 ScriptGenerator：无硬编码调度器分支；只生成不执行。"""

from pathlib import Path

import pytest

from backend.app.generators.script import ScriptGenerator
from backend.app.recipes.errors import BeAError
from backend.app.schemas.generation import SchedulerSettings


class TestProfileDriven:
    def test_three_profiles_supported(self):
        generator = ScriptGenerator()
        assert generator.supported_types == ["cbatch", "generic", "slurm"]

    def test_slurm_uses_sbatch_directives(self):
        text = ScriptGenerator().render(
            SchedulerSettings(type="slurm", partition="compute", account="grp"),
            step_id="01_relax",
        )
        assert "#SBATCH --job-name=bea_01_relax" in text
        assert "#SBATCH --partition=compute" in text
        assert "#SBATCH --account=grp" in text
        assert "srun vasp_std" in text

    def test_cbatch_uses_cbatch_directives(self):
        text = ScriptGenerator().render(
            SchedulerSettings(type="cbatch", nodes=2, tasks_per_node=16),
            step_id="02_static",
        )
        assert "#CBATCH --nodes=2" in text
        assert "#CBATCH --tasks-per-node=16" in text
        assert "#SBATCH" not in text

    def test_generic_has_no_scheduler_directives(self):
        text = ScriptGenerator().render(
            SchedulerSettings(type="generic"), step_id="01_relax"
        )
        assert "#SBATCH" not in text
        assert "#CBATCH" not in text
        assert "mpirun" in text

    def test_unknown_scheduler_fail_closed(self):
        with pytest.raises(BeAError) as excinfo:
            ScriptGenerator().render(SchedulerSettings(type="lsf"))
        assert excinfo.value.code == "SCHEDULER_PROFILE_UNKNOWN"

    def test_module_loads_rendered(self):
        text = ScriptGenerator().render(
            SchedulerSettings(type="slurm", module_loads=["vasp/6.4", "intel/2023"])
        )
        assert "module load vasp/6.4" in text
        assert "module load intel/2023" in text

    def test_launcher_comes_from_profile_not_hardcoded(self):
        """同一 scheduler 换 profile 的 launcher_prefix，输出随之改变。"""

        from backend.app.schemas.generation import SchedulerProfile

        custom = {
            "slurm": SchedulerProfile(
                scheduler_type="slurm",
                script_template="slurm.sh.j2",
                launcher_prefix="mpirun -np 32",
            )
        }
        text = ScriptGenerator(profiles=custom).render(SchedulerSettings(type="slurm"))
        assert "mpirun -np 32 vasp_std" in text


class TestNoExecution:
    def test_generator_never_invokes_subprocess(self):
        source = Path(
            "backend/app/generators/script.py"
        )
        if not source.exists():
            source = (
                Path(__file__).resolve().parents[3]
                / "backend" / "app" / "generators" / "script.py"
            )
        content = source.read_text(encoding="utf-8")
        assert "subprocess" not in content
        assert "os.system" not in content

    def test_render_returns_text_only(self):
        text = ScriptGenerator().render(SchedulerSettings(type="slurm"))
        assert isinstance(text, str)
        assert text.startswith("#!/bin/bash")
