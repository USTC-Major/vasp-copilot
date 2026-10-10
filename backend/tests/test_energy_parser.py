"""PP-22A block attribution, not a validation of physical reference states."""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from backend.toolbox.postprocessing.energy.parser import parse_energy_outcar

FIXTURES = Path(__file__).parent / "fixtures" / "energy"
SUMMARY = "   FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)\n"
FOOTER = " General timing and accounting informations for this job:\n"
IONIC_STOP = " reached required accuracy - stopping structural energy minimisation\n"
EDIFF_STOP = " ---------------- aborting loop because EDIFF is reached ----------------\n"


def header(*, relaxation=False, extra="", species=""):
    return (
        " vasp.6.2.1 build synthetic-test\n"
        + (species or " TITEL = PAW_PBE Si 05Jan2001\n VRHFIN =Si: s2p2\n ions per type = 2\n NIONS = 2\n")
        + " ISPIN = 1\n ICHARG = 2\n LNONCOLLINEAR = F\n LSORBIT = F\n"
        + (" IBRION = 2\n NSW = 4\n EDIFFG = -0.02\n" if relaxation else " IBRION = -1\n NSW = 0\n")
        + " EDIFF = 1d-6\n GGA = PE\n" + extra
    )


def block(step=1, iteration=2, *, toten="-10.2", without="-10.1", e0="-10.15", ediff=True):
    return (
        f" ---- Iteration {step}( {iteration}) ----\n"
        + (EDIFF_STOP if ediff else "") + SUMMARY + " ---------------------\n"
        + f" free energy TOTEN = {toten} eV\n"
        + f" energy without entropy= {without} energy(sigma->0) = {e0}\n"
    )


def codes(result):
    return {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("name,sha,composition,step,energies,lines,status", [
    ("OUTCAR.Al", "02068c13eab5d7ca8a3cdadca046884eaf453a1e8a09f0c97f6abf52605663a3", {"Al": 1}, 1,
     (-3.743359, -3.743359, -3.743359), (6086, 6088), (True, None)),
    ("OUTCAR.serial.gz", "a1b4c7f63768b25682ddba374bb307616c6f8af50128dcca610eb7553b0d8ab2", {"Si": 8}, 1,
     (-43.39175481, -43.39175481, -43.39175481), (1601, 1603), (True, True)),
    ("OUTCAR.etest1.gz", "80d10c3fd8b76fde517f17cb5497945cf28544999e36f6373e6bad0dd9083131", {"Cu": 1}, 8,
     (-11.18981538, -11.13480014, -11.217323), (4266, 4268), (True, True)),
])
def test_public_outcar_fields_and_independent_pymatgen(name, sha, composition, step, energies, lines, status):
    # Expected values/line positions come from the fixed upstream files. The
    # mature library is an independent value check, not our completeness oracle.
    from pymatgen.io.vasp.outputs import Outcar
    path = FIXTURES / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
    text = gzip.open(path, "rt").read() if path.suffix == ".gz" else path.read_text()
    parsed = parse_energy_outcar(text)
    independent = Outcar(path)
    assert tuple(parsed["energy_fields"].values()) == pytest.approx(energies)
    assert energies == pytest.approx((independent.final_energy, independent.final_energy_wo_entrp, independent.final_fr_energy))
    assert parsed["composition"] == composition
    assert parsed["provenance"]["selected_ionic_step"] == step
    assert parsed["provenance"]["field_lines"] == {
        "free_energy_toten_ev": lines[0], "without_entropy_ev": lines[1], "sigma_to_zero_ev": lines[1]}
    assert parsed["status"]["completion"] == "completed"
    assert (parsed["status"]["electronic_converged"], parsed["status"]["ionic_converged"]) == status
    assert not parsed["errors"]
    assert parsed["metadata"]["support"]["automatic_comparison"] is (name != "OUTCAR.etest1.gz")
    json.dumps(parsed, allow_nan=False)


def test_electronic_energy_tables_never_become_ionic_records():
    text = header() + " ---- Iteration 1( 1) ----\n"
    text += " Free energy of the ion-electron system (eV)\n free energy TOTEN = -100 eV\n"
    text += " energy without entropy = -99 energy(sigma->0) = -99.5\n"
    parsed = parse_energy_outcar(text + FOOTER)
    assert "ENERGY_MISSING" in codes(parsed)
    assert all(value is None for value in parsed["energy_fields"].values())
    assert parsed["status"]["completion"] == "completed"
    assert parsed["evidence"]["run_ended"]


def test_last_complete_block_and_trailing_new_step_are_separate():
    parsed = parse_energy_outcar(header(relaxation=True) + block(1) + IONIC_STOP + " ---- Iteration 2( 1) ----\n")
    assert parsed["energy_fields"]["sigma_to_zero_ev"] == -10.15
    assert parsed["provenance"]["selected_ionic_step"] == 1
    assert parsed["status"]["completion"] == "incomplete"
    assert {"TRAILING_INCOMPLETE", "RUN_INCOMPLETE"} <= codes(parsed)
    assert parsed["status"]["electronic_converged"] is True
    assert parsed["status"]["ionic_converged"] is True


def test_truncated_last_summary_cannot_mix_fields_from_steps():
    text = header(relaxation=True) + block(1, e0="-11")
    text += " ---- Iteration 2( 1) ----\n" + SUMMARY + " free energy TOTEN = -20 eV\n"
    parsed = parse_energy_outcar(text)
    assert parsed["energy_fields"] == {"sigma_to_zero_ev": -11, "without_entropy_ev": -10.1, "free_energy_toten_ev": -10.2}
    assert parsed["provenance"]["selected_block_index"] == 1
    assert "TRAILING_INCOMPLETE" in codes(parsed)
    assert parse_energy_outcar(header() + SUMMARY + " free energy TOTEN = -20 eV\n")["errors"]


def test_force_table_boundary_cannot_complete_an_old_summary():
    text = header() + SUMMARY + " free energy TOTEN = -20 eV\n TOTAL-FORCE (eV/Angst)\n"
    text += " energy without entropy = -19 energy(sigma->0) = -19.5\n"
    assert "ENERGY_MISSING" in codes(parse_energy_outcar(text))


@pytest.mark.parametrize("tail", [header() + block(), " ---- Iteration 1( 1) ----\n" + block(), SUMMARY + " free energy TOTEN = -2 eV\n"])
def test_multiple_runs_or_post_footer_calculation_are_hard_errors(tail):
    parsed = parse_energy_outcar(header() + block() + FOOTER + tail)
    assert {"RUN_MULTIPLE_SEGMENTS", "RUN_SEGMENT_AMBIGUOUS"} & codes(parsed)
    assert all(value is None for value in parsed["energy_fields"].values())


@pytest.mark.parametrize("raw", ["NaN", "nan", "Inf", "-Infinity", "1e9999"])
def test_nonfinite_is_hard_error_even_when_an_earlier_block_exists(raw):
    parsed = parse_energy_outcar(header(relaxation=True) + block(1) + block(2, e0=raw))
    assert "ENERGY_NONFINITE" in codes(parsed)
    assert parsed["metadata"]["support"]["automatic_comparison"] is False
    assert next(issue for issue in parsed["issues"] if issue["code"] == "ENERGY_NONFINITE")["recoverable_by_manual"] is False
    json.dumps(parsed, allow_nan=False)


def test_fortran_exponents_and_zero_are_retained():
    parsed = parse_energy_outcar(header() + block(toten="0.0", without="-.101d+02", e0="-1.015D+01") + FOOTER)
    assert parsed["energy_fields"] == {"sigma_to_zero_ev": -10.15, "without_entropy_ev": -10.1, "free_energy_toten_ev": 0.0}


def test_species_repeated_in_order_suffixes_and_counts_are_preserved():
    species = (" TITEL = PAW_PBE Fe_pv 06Sep2000\n TITEL = PAW_PBE O 08Apr2002\n" * 2
               + " VRHFIN =Fe: d7\n VRHFIN =O: s2p4\n" * 2
               + " ions per type = 2 3\n ions per type = 2 3\n NIONS = 5\n")
    parsed = parse_energy_outcar(header(species=species, extra=" LDAU = T\n LDAUTYPE = 2\n LDAUL = 2 -1\n LDAUU = 4.5 0\n LDAUJ = 0 0\n") + block() + FOOTER)
    assert parsed["composition"] == {"Fe": 2, "O": 3}
    assert len(parsed["metadata"]["species"]) == 2
    assert parsed["metadata"]["potcar_datasets"]["Fe"] == "PAW_PBE Fe_pv 06Sep2000"
    assert parsed["metadata"]["u_by_element"]["Fe"] == {"l": 2, "u": 4.5, "j": 0}
    assert parsed["metadata"]["method"] == "PBE+U"
    assert not parsed["errors"]


@pytest.mark.parametrize("species,code", [
    (" TITEL = PAW_PBE Si 01Jan2000\n", "COMPOSITION_MISSING"),
    (" ions per type = 2\n", "COMPOSITION_MISSING"),
    (" TITEL = PAW_PBE Si 01Jan2000\n VRHFIN =O: s2p4\n ions per type = 2\n", "COMPOSITION_CONFLICT"),
    (" TITEL = PAW_PBE Si 01Jan2000\n ions per type = 2.5\n", "COMPOSITION_INVALID"),
    (" TITEL = PAW_PBE Si 01Jan2000\n ions per type = nan\n", "COMPOSITION_INVALID"),
    (" TITEL = PAW_PBE Si 01Jan2000\n ions per type = 2\n NIONS = 3\n", "COMPOSITION_NIONS_MISMATCH"),
    (" TITEL = PAW_PBE Si 01Jan2000\n ions per type = 2\n ions per type = 3\n", "COMPOSITION_CONFLICT"),
])
def test_composition_errors_do_not_guess_from_name_or_electron_count(species, code):
    parsed = parse_energy_outcar(header(species=species, extra=" SYSTEM = Si2 molecular test\n NELECT = 8\n") + block())
    assert code in codes(parsed)
    assert parsed["composition"] == {}
    assert next(issue for issue in parsed["issues"] if issue["code"] == code)["recoverable_by_manual"] is True
    assert parsed["energy_fields"]["sigma_to_zero_ev"] == -10.15


@pytest.mark.parametrize("extra", [" ICHARG = 11\n", " IBRION = 0\n NSW = 10\n", " LSORBIT = T\n", " LNONCOLLINEAR = T\n", " LEPSILON = T\n", " ALGO = G0W0\n", " ML_MODE = run\n", " IMAGES = 4\n", " ALGO = Eigenval\n", " IALGO = 2\n", " ALGO = TDHF\n", " METAGGA = MBJ\n", " LDAU = T\n LDAUTYPE = 3\n"])
def test_special_run_types_are_explicit_hard_errors(extra):
    parsed = parse_energy_outcar(header(extra=extra) + block() + FOOTER)
    assert "RUN_UNSUPPORTED" in codes(parsed)
    assert parsed["metadata"]["support"]["automatic_comparison"] is False
    assert parsed["energy_fields"]["sigma_to_zero_ev"] == -10.15


def test_exact_diagonalization_is_a_scf_algorithm_not_gw():
    parsed = parse_energy_outcar(header(extra=" ALGO = Exact\n IALGO = 90\n") + block() + FOOTER)
    assert "RUN_UNSUPPORTED" not in codes(parsed)


def test_convergence_does_not_transfer_between_steps_or_electronic_iterations():
    text = header(relaxation=True) + block(1) + IONIC_STOP + block(2, ediff=False) + FOOTER
    parsed = parse_energy_outcar(text)
    assert parsed["status"] == {"completion": "completed", "electronic_converged": None,
                                "ionic_converged": None, "ionic_applicability": "applicable"}
    assert parsed["evidence"]["electronic"] == [] and parsed["evidence"]["ionic"] == []
    # Same ionic step, a later electronic iteration invalidates earlier stop evidence.
    text = header() + " ---- Iteration 1( 1) ----\n" + EDIFF_STOP + block(1, 2, ediff=False) + FOOTER
    assert parse_energy_outcar(text)["status"]["electronic_converged"] is None


def test_nelm_is_not_a_convergence_or_failure_oracle():
    parsed = parse_energy_outcar(header(extra=" NELM = 2\n") + block(iteration=2, ediff=False) + FOOTER)
    assert parsed["status"]["electronic_converged"] is None
    parsed = parse_energy_outcar(header(extra=" EDIFF = 0\n") + block() + FOOTER)
    assert parsed["status"]["electronic_converged"] is None
    text = header() + " ---- Iteration 1( 2) ----\n aborting loop because NELM is reached\n" + SUMMARY
    text += " free energy TOTEN = -10 eV\n energy without entropy = -10 energy(sigma->0) = -10\n"
    assert parse_energy_outcar(text + FOOTER)["status"]["electronic_converged"] is False


def test_system_prose_does_not_override_scientific_metadata():
    parsed = parse_energy_outcar(header(extra=" SYSTEM = specimen ICHARG = 11 LSORBIT = T\n") + block() + FOOTER)
    assert "RUN_UNSUPPORTED" not in codes(parsed)
    assert parsed["metadata"]["parameters"]["ICHARG"] == 2
    assert parsed["metadata"]["parameters"]["LSORBIT"] is False


def test_static_and_unknown_ionic_evidence_are_not_claimed_as_relaxation():
    parsed = parse_energy_outcar(header() + block() + IONIC_STOP + FOOTER)
    assert parsed["status"]["ionic_applicability"] == "not_applicable"
    assert parsed["status"]["ionic_converged"] is None
    unknown = parse_energy_outcar(header().replace(" IBRION = -1\n NSW = 0\n", "") + block() + FOOTER)
    assert unknown["status"]["ionic_applicability"] == "unknown"
    assert "RUN_TYPE_UNKNOWN" in codes(unknown)


def test_unassigned_short_stop_text_remains_unknown_and_diagnostic_semantics_unchanged():
    from backend.app.parsers.outcar import parse_outcar
    text = header() + SUMMARY + " free energy TOTEN = -10.2 eV\n"
    text += " energy without entropy= -10.1 energy(sigma->0) = -10.15\n reached required accuracy\n" + FOOTER
    parsed = parse_energy_outcar(text)
    assert parsed["provenance"]["selected_ionic_step"] is None
    assert parsed["status"]["electronic_converged"] is None
    assert parse_outcar(text).final_energy == -10.2


def test_duplicate_final_summary_in_the_same_step_is_ambiguous():
    text = header() + block() + SUMMARY + " free energy TOTEN = -11 eV\n"
    text += " energy without entropy= -11 energy(sigma->0) = -11\n" + FOOTER
    assert "ENERGY_BLOCK_AMBIGUOUS" in codes(parse_energy_outcar(text))
