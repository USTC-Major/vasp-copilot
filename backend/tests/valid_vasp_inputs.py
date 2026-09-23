"""Synthetic, license-free minimal input files for submit-flow tests."""

INCAR = b"SYSTEM = synthetic\nENCUT = 400\n"
POSCAR = (b"Synthetic Si\n1.0\n3 0 0\n0 3 0\n0 0 3\nSi\n1\nDirect\n"
          b"0.25 0.25 0.25\n")
KPOINTS = b"Synthetic mesh\n0\nGamma\n1 1 1\n0 0 0\n"
POTCAR = (b"TITEL = PAW_PBE Si\nVRHFIN =Si:\n"
          b"Synthetic test metadata only; not a real pseudopotential.\nEnd of Dataset\n")
FILES = {"INCAR": INCAR, "POSCAR": POSCAR, "KPOINTS": KPOINTS, "POTCAR": POTCAR}
