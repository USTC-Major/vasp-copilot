"""README / INPUT_CHECK_REPORT / workflow_plan.json 共用同一 FileInheritancePlan。"""

import json

from backend.app.workflow.pipeline import WorkflowGenerationPipeline


def _edges_of(dependencies):
    return sorted(
        (dep.from_step_id, dep.source_file, dep.to_step_id, dep.target_file)
        for dep in dependencies
    )


class TestSharedInheritancePlan:
    def test_plan_file_and_result_share_same_plan(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        assert (
            _edges_of(result.plan_file.file_inheritance_plan.dependencies)
            == _edges_of(result.file_inheritance_plan.dependencies)
        )

    def test_workflow_plan_json_carries_same_edges(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        plan_body = json.loads(result.bundle.files["workflow_plan.json"].decode("utf-8"))
        file_edges = _edges_of(result.file_inheritance_plan.dependencies)
        json_edges = sorted(
            (
                dep["from_step_id"],
                dep["source_file"],
                dep["to_step_id"],
                dep["target_file"],
            )
            for dep in plan_body["file_inheritance_plan"]["dependencies"]
        )
        assert json_edges == file_edges
        assert json_edges == [
            ("01_relax", "CONTCAR", "02_static", "POSCAR"),
            ("02_static", "CHGCAR", "03_dos", "CHGCAR"),
        ]

    def test_readme_renders_every_edge(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        readme = result.bundle.files["README_run_order.md"].decode("utf-8")
        assert "文件继承" in readme
        for dep in result.file_inheritance_plan.dependencies:
            assert (
                f"| `{dep.from_step_id}` | {dep.source_file} | `{dep.to_step_id}` | {dep.target_file}"
                in readme
            )

    def test_input_check_report_renders_same_edges(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        report = result.bundle.files["INPUT_CHECK_REPORT.md"].decode("utf-8")
        for dep in result.file_inheritance_plan.dependencies:
            assert dep.from_step_id in report
            assert f"{dep.source_file}" in report

    def test_readme_lists_all_steps_in_order(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        readme = result.bundle.files["README_run_order.md"].decode("utf-8")
        positions = [readme.index(step.directory) for step in result.steps]
        assert positions == sorted(positions)
        for step in result.steps:
            assert f"runnable: **false**" in readme
            assert step.step_id in readme


class TestPotcarDocument:
    def test_potcar_required_generated_when_missing(self, nacl_request):
        result = WorkflowGenerationPipeline(potcar_prepared=False).generate(nacl_request)
        assert "POTCAR_REQUIRED.md" in result.bundle.files
        document = result.bundle.files["POTCAR_REQUIRED.md"].decode("utf-8")
        assert "Na" in document and "Cl" in document

    def test_potcar_required_absent_when_prepared(self, nacl_request):
        result = WorkflowGenerationPipeline(potcar_prepared=True).generate(nacl_request)
        assert "POTCAR_REQUIRED.md" not in result.bundle.files


class TestFileTreeConsistency:
    def test_file_tree_matches_bundle(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        tree_paths = set()

        def collect(node):
            for child in node.children:
                if child.type == "file":
                    tree_paths.add(child.relative_path)
                else:
                    collect(child)

        collect(result.file_tree)
        assert tree_paths == set(result.bundle.files)

    def test_input_report_has_seven_sections(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        report = result.bundle.files["INPUT_CHECK_REPORT.md"].decode("utf-8")
        headings = [line for line in report.splitlines() if line.startswith("## ")]
        assert len(headings) == 7, f"INPUT_CHECK_REPORT 应固定 7 个章节，实际 {headings}"
