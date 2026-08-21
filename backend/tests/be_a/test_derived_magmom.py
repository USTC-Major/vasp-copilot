"""验收 5：MAGMOM 展开长度 == 原子数、按 POSCAR 元素顺序。"""

import pytest

from backend.app.recipes.derived import (
    DEFAULT_OTHER_MOMENT,
    DEFAULT_TM_MOMENT,
    generate_magmom_from_structure,
)
from backend.app.recipes.errors import DerivedParameterUnresolved


class TestMagmomDerivation:
    def test_length_equals_atom_count(self):
        magmom = generate_magmom_from_structure(
            {"elements": ["Fe", "O"], "counts": [2, 3]}
        )
        assert len(magmom) == 5

    def test_poscar_order_preserved(self):
        magmom = generate_magmom_from_structure(
            {"elements": ["Fe", "O"], "counts": [2, 3]}
        )
        assert magmom[:2] == [DEFAULT_TM_MOMENT] * 2
        assert magmom[2:] == [DEFAULT_OTHER_MOMENT] * 3

    def test_multiple_transition_metals(self):
        magmom = generate_magmom_from_structure(
            {"elements": ["Fe", "Ni", "O"], "counts": [1, 2, 4]}
        )
        assert magmom == [DEFAULT_TM_MOMENT] * 3 + [DEFAULT_OTHER_MOMENT] * 4

    def test_user_confirmed_moments_take_priority(self):
        magmom = generate_magmom_from_structure(
            {
                "elements": ["Fe", "O"],
                "counts": [2, 3],
                "element_initial_moments": {"Fe": 4.2},
            }
        )
        assert magmom[:2] == [4.2, 4.2]

    def test_elements_counts_mismatch_raises(self):
        with pytest.raises(DerivedParameterUnresolved):
            generate_magmom_from_structure({"elements": ["Fe"], "counts": [2, 3]})

    def test_nonmagnetic_element_gets_small_moment(self):
        magmom = generate_magmom_from_structure(
            {"elements": ["Si"], "counts": [8]}
        )
        assert magmom == [DEFAULT_OTHER_MOMENT] * 8


class TestMagmomInComposition:
    def test_composition_magmom_matches_structure(self, registry, pack):
        from backend.app.recipes.composer import ComposeRequest, RecipeComposer
        from backend.app.recipes.selector import RecipeSelector
        from backend.app.schemas.recipe import (
            ElectronicType,
            PrecisionLevel,
            SelectionContext,
            TaskType,
        )

        context = SelectionContext(
            task=TaskType.RELAX,
            electronic_type=ElectronicType.SEMICONDUCTOR,
            precision=PrecisionLevel.STANDARD,
            magnetic=True,
            elements=["Fe", "O"],
        )
        entries = RecipeSelector().select(context)
        composition = RecipeComposer(registry, pack).compose(
            ComposeRequest(
                step_id="01_relax",
                context=context,
                entries=entries,
                confirmed_keys={"EDIFFG"},
                derived_inputs={
                    "elements": ["Fe", "O"],
                    "counts": [2, 3],
                    "formula": "Fe2O3",
                    "task": "relax",
                    "precision": "standard",
                },
            )
        )
        assert composition.resolved_parameters["MAGMOM"] == [5.0, 5.0, 0.6, 0.6, 0.6]
