"""验收 6/7：LDAU 数组长度 == 元素种类数；未确认的 U 拒绝生成。"""

import pytest

from backend.app.generators.incar import IncarGenerator
from backend.app.recipes.derived import generate_ldau_arrays
from backend.app.recipes.errors import BeAError, DerivedParameterUnresolved, DftuConfirmationRequired
from backend.app.schemas.generation import (
    DftuEntry,
    DftuSettings,
    StructureContext,
)


def _structure():
    return StructureContext(
        formula="Fe2O3",
        elements=["Fe", "O"],
        counts=[2, 3],
    )


class TestLdauDerivation:
    def test_arrays_length_equals_element_count(self):
        arrays = generate_ldau_arrays(
            {
                "elements": ["Fe", "O"],
                "dftu_entries": [
                    {"element": "Fe", "l": 2, "u_ev": 4.0, "j_ev": 0.0}
                ],
            }
        )
        assert arrays["LDAUL"] == [2.0, -1.0]
        assert arrays["LDAUU"] == [4.0, 0.0]
        assert arrays["LDAUJ"] == [0.0, 0.0]

    def test_element_without_u_gets_minus_one(self):
        arrays = generate_ldau_arrays(
            {"elements": ["Na", "Cl"], "dftu_entries": []}
        )
        assert arrays["LDAUL"] == [-1.0, -1.0]
        assert arrays["LDAUU"] == [0.0, 0.0]
        assert arrays["LDAUJ"] == [0.0, 0.0]

    def test_unknown_element_rejected(self):
        with pytest.raises(DerivedParameterUnresolved):
            generate_ldau_arrays(
                {
                    "elements": ["Fe", "O"],
                    "dftu_entries": [{"element": "Cu", "l": 2, "u_ev": 4.0}],
                }
            )

    def test_poscar_order_follows_elements(self):
        arrays = generate_ldau_arrays(
            {
                "elements": ["O", "Fe"],
                "dftu_entries": [{"element": "Fe", "l": 2, "u_ev": 4.0}],
            }
        )
        assert arrays["LDAUL"] == [-1.0, 2.0]


class TestLdauGenerationGate:
    def test_unconfirmed_dftu_rejected_at_generation(self):
        parameters = {
            "LDAU": True,
            "LDAUTYPE": 2,
            "LDAUL": [2.0, -1.0],
            "LDAUU": [4.0, 0.0],
            "LDAUJ": [0.0, 0.0],
        }
        dftu = DftuSettings(
            enabled=True,
            entries=[DftuEntry(element="Fe", l=2, u_ev=4.0, confirmed_by_user=False)],
        )
        with pytest.raises(DftuConfirmationRequired):
            IncarGenerator().generate(parameters, _structure(), dftu)

    def test_confirmed_dftu_generates(self):
        parameters = {
            "LDAU": True,
            "LDAUTYPE": 2,
            "LDAUL": [2.0, -1.0],
            "LDAUU": [4.0, 0.0],
            "LDAUJ": [0.0, 0.0],
        }
        dftu = DftuSettings(
            enabled=True,
            entries=[DftuEntry(element="Fe", l=2, u_ev=4.0, confirmed_by_user=True)],
        )
        text = IncarGenerator().generate(parameters, _structure(), dftu)
        assert "LDAU = .TRUE." in text
        assert "LDAUL = 2 -1" in text

    def test_ldau_wrong_length_rejected(self):
        parameters = {
            "LDAU": True,
            "LDAUL": [2.0],
            "LDAUU": [4.0],
            "LDAUJ": [0.0],
        }
        with pytest.raises(BeAError) as excinfo:
            IncarGenerator().generate(parameters, _structure())
        assert excinfo.value.code == "LDAU_LENGTH_MISMATCH"

    def test_ldau_missing_arrays_rejected(self):
        parameters = {"LDAU": True, "LDAUL": [2.0, -1.0]}
        with pytest.raises(BeAError):
            IncarGenerator().generate(parameters, _structure())

    def test_pipeline_rejects_unconfirmed_dftu(self, fe2o3_request):
        from backend.app.workflow import WorkflowGenerationPipeline

        fe2o3_request.dftu.entries[0].confirmed_by_user = False
        with pytest.raises(DftuConfirmationRequired):
            WorkflowGenerationPipeline().generate(fe2o3_request)
