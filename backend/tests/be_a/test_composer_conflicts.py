"""验收 3/4：固定层序合并；同层冲突报错，绝不静默覆盖。"""

import pytest

from backend.app.recipes.composer import ComposeRequest, RecipeComposer
from backend.app.recipes.errors import RecipeConflictError, RecipeScopeMismatch
from backend.app.recipes.selector import RecipeSelector
from backend.app.schemas.recipe import (
    ElectronicType,
    PrecisionLevel,
    SelectionContext,
    TaskType,
)


def _context(task=TaskType.RELAX, magnetic=False, dftu=False):
    return SelectionContext(
        task=task,
        electronic_type=ElectronicType.SEMICONDUCTOR,
        precision=PrecisionLevel.STANDARD,
        magnetic=magnetic,
        dftu=dftu,
        elements=["Fe", "O"],
    )


def _compose(registry, pack, context, **kwargs):
    entries = RecipeSelector().select(context)
    return RecipeComposer(registry, pack).compose(
        ComposeRequest(
            step_id="01_relax",
            context=context,
            entries=entries,
            confirmed_keys={"EDIFFG"},
            derived_inputs={
                "elements": list(context.elements),
                "counts": [2, 3],
                "formula": "Fe2O3",
                "task": context.task.value,
                "precision": context.precision.value,
            },
            **kwargs,
        )
    )


class TestLayerOrder:
    def test_selected_order_is_fixed(self, registry):
        entries = RecipeSelector().select(_context(magnetic=True))
        layers = [e.layer_name for e in entries]
        assert layers == ["base", "task", "electronic_type", "modifier", "precision"]

    def test_higher_layer_overrides_with_provenance(self, registry, pack):
        """static 的 LCHARG=True（task 层）覆盖 base 层 LCHARG=False。"""

        context = _context(task=TaskType.STATIC)
        composition = _compose(registry, pack, context)
        assert composition.resolved_parameters["LCHARG"] is True
        provenance = {
            entry["parameter"]: entry for entry in composition.provenance
        }
        lcharg = provenance["LCHARG"]
        assert lcharg["source_id"].startswith("task.static.standard")
        assert lcharg["overrode"] is not None
        assert lcharg["overrode"]["value"] is False

    def test_electronic_layer_not_applied_to_dos(self, registry):
        entries = RecipeSelector().select(_context(task=TaskType.DOS))
        assert all(e.layer_name != "electronic_type" for e in entries)

    def test_scope_mismatch_rejected(self, registry, pack):
        """relax recipe 显式只覆盖 relax：用 DOS context 强选会 scope 失败。"""

        from backend.app.recipes.selector import SelectionEntry
        from backend.app.schemas.recipe import RecipeRef

        context = _context(task=TaskType.DOS)
        entries = [
            SelectionEntry(
                RecipeRef(recipe_id="task.relax.standard", version="1.0.0"),
                "task",
                "forced",
                {},
            )
        ]
        composer = RecipeComposer(registry, pack)
        with pytest.raises(RecipeScopeMismatch):
            composer.compose(
                ComposeRequest(step_id="x", context=context, entries=entries)
            )


class TestSameLayerConflict:
    def test_same_layer_conflict_raises(self, registry, pack, tmp_path):
        """同层两个 recipe 对同一参数给出不同值 → RECIPE_CONFLICT。"""

        from backend.app.recipes.loader import RecipePackLoader
        from backend.app.recipes.selector import SelectionEntry
        from backend.app.schemas.recipe import RecipeRef

        loader = RecipePackLoader()
        conflict_a = tmp_path / "a.yaml"
        conflict_a.write_text(
            "schema_version: '1.0'\nrecipe_id: task.dup_a\nversion: 1.0.0\n"
            "kind: task\nrecipe_status: draft\nscope:\n  tasks: [relax]\n"
            "parameters:\n  NSW: 100\ntests:\n  test_status: passed\n",
            encoding="utf-8",
        )
        conflict_b = tmp_path / "b.yaml"
        conflict_b.write_text(
            "schema_version: '1.0'\nrecipe_id: task.dup_b\nversion: 1.0.0\n"
            "kind: task\nrecipe_status: draft\nscope:\n  tasks: [relax]\n"
            "parameters:\n  NSW: 200\ntests:\n  test_status: passed\n",
            encoding="utf-8",
        )
        registry.register_recipe(loader.load_recipe(conflict_a))
        registry.register_recipe(loader.load_recipe(conflict_b))
        entries = [
            SelectionEntry(RecipeRef(recipe_id="task.dup_a", version="1.0.0"), "task", "", {}),
            SelectionEntry(RecipeRef(recipe_id="task.dup_b", version="1.0.0"), "task", "", {}),
        ]
        composer = RecipeComposer(registry, pack)
        with pytest.raises(RecipeConflictError) as excinfo:
            composer.compose(
                ComposeRequest(step_id="01_relax", context=_context(), entries=entries)
            )
        assert excinfo.value.code == "RECIPE_CONFLICT"
        assert excinfo.value.details["conflicts"][0]["parameter"] == "NSW"

    def test_same_layer_same_value_is_not_conflict(self, registry, pack, tmp_path):
        from backend.app.recipes.loader import RecipePackLoader
        from backend.app.recipes.selector import SelectionEntry
        from backend.app.schemas.recipe import RecipeRef

        loader = RecipePackLoader()
        for name in ("same_a", "same_b"):
            path = tmp_path / f"{name}.yaml"
            path.write_text(
                f"schema_version: '1.0'\nrecipe_id: task.{name}\nversion: 1.0.0\n"
                "kind: task\nrecipe_status: draft\nscope:\n  tasks: [relax]\n"
                "parameters:\n  NSW: 100\ntests:\n  test_status: passed\n",
                encoding="utf-8",
            )
            registry.register_recipe(loader.load_recipe(path))
        entries = [
            SelectionEntry(RecipeRef(recipe_id="task.same_a", version="1.0.0"), "task", "", {}),
            SelectionEntry(RecipeRef(recipe_id="task.same_b", version="1.0.0"), "task", "", {}),
        ]
        composer = RecipeComposer(registry, pack)
        composition = composer.compose(
            ComposeRequest(step_id="01_relax", context=_context(), entries=entries)
        )
        assert composition.resolved_parameters["NSW"] == 100
