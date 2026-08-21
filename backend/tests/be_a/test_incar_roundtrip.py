"""验收 9：IncarSerializer → reparse round-trip；bool/浮点/数组格式确定性。"""

import pytest

from backend.app.generators.serializer import IncarParser, IncarSerializer
from backend.app.recipes.errors import IncarRoundtripMismatch


class TestFormats:
    def test_bool_format(self):
        text = IncarSerializer().serialize({"LCHARG": True, "LWAVE": False})
        assert "LCHARG = .TRUE." in text
        assert "LWAVE = .FALSE." in text

    def test_float_canonical_format(self):
        text = IncarSerializer().serialize(
            {"EDIFF": 1e-05, "ENCUT": 520.0, "SIGMA": 0.2, "EDIFFG": -0.02}
        )
        assert "EDIFF = 1e-05" in text
        assert "ENCUT = 520" in text
        assert "SIGMA = 0.2" in text
        assert "EDIFFG = -0.02" in text

    def test_magmom_run_compression(self):
        text = IncarSerializer().serialize({"MAGMOM": [5.0, 5.0, 0.6, 0.6, 0.6]})
        assert "MAGMOM = 2*5 3*0.6" in text

    def test_ldau_arrays_space_separated(self):
        text = IncarSerializer().serialize(
            {"LDAUL": [2.0, -1.0], "LDAUU": [4.0, 0.0], "LDAUJ": [0.0, 0.0]}
        )
        assert "LDAUL = 2 -1" in text
        assert "LDAUU = 4 0" in text
        assert "LDAUJ = 0 0" in text

    def test_parameters_sorted(self):
        text = IncarSerializer().serialize({"NSW": 100, "ALGO": "Normal", "IBRION": 2})
        lines = [line.split(" = ")[0] for line in text.strip().splitlines()]
        assert lines == sorted(lines) == ["ALGO", "IBRION", "NSW"]

    def test_deterministic_output(self):
        parameters = {"NSW": 100, "ALGO": "Normal", "EDIFF": 1e-05}
        first = IncarSerializer().serialize(parameters)
        second = IncarSerializer().serialize(dict(reversed(list(parameters.items()))))
        assert first == second


class TestRoundtrip:
    @pytest.mark.parametrize(
        "parameters",
        [
            {"ISPIN": 2, "LCHARG": True, "LREAL": "Auto"},
            {"MAGMOM": [5.0, 5.0, 0.6, 0.6, 0.6]},
            {"EDIFF": 1e-06, "NELM": 200, "ADDGRID": True},
            {"LDAUL": [2.0, -1.0], "LDAUU": [4.0, 0.0], "LDAUJ": [0.0, 0.0], "LDAU": True},
            {"SYSTEM": "Fe2O3_relax", "ENCUT": 520.0, "ISMEAR": -5, "SIGMA": 0.05},
        ],
    )
    def test_reparse_matches(self, parameters):
        serializer = IncarSerializer()
        text = serializer.serialize(parameters)
        reparsed = IncarParser().parse(text)
        assert set(reparsed) == set(parameters)
        for key, value in parameters.items():
            expected = value
            actual = reparsed[key]
            if isinstance(expected, list):
                assert [float(x) for x in actual] == [float(x) for x in expected]
            elif isinstance(expected, bool):
                assert actual is expected
            elif isinstance(expected, (int, float)):
                assert float(actual) == float(expected)
            else:
                assert actual == expected

    def test_mismatch_raises(self):
        class BrokenParser(IncarParser):
            def parse(self, text):
                result = super().parse(text)
                result["NSW"] = 999
                return result

        serializer = IncarSerializer(parser=BrokenParser())
        with pytest.raises(IncarRoundtripMismatch) as excinfo:
            serializer.serialize({"NSW": 100})
        assert excinfo.value.code == "INCAR_ROUNDTRIP_MISMATCH"

    def test_parser_expands_repeat_tokens(self):
        parsed = IncarParser().parse("MAGMOM = 2*5 3*0.6\n")
        assert parsed["MAGMOM"] == [5, 5, 0.6, 0.6, 0.6]

    def test_parser_rejects_duplicate_tag(self):
        with pytest.raises(ValueError):
            IncarParser().parse("NSW = 100\nNSW = 200\n")
