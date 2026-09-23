"""Bounded content checks shared by both Toolbox submit prechecks."""
from __future__ import annotations

import hashlib
from pathlib import Path

from backend.input_validation import (
    InputValidationError, POTCAR_LIMIT, TEXT_LIMIT, validate_incar,
    validate_kpoints, validate_poscar, validate_potcar,
)
from .tools.draft import input_fingerprint_local, input_fingerprint_remote


LIMITS = {"INCAR": TEXT_LIMIT, "POSCAR": TEXT_LIMIT,
          "KPOINTS": TEXT_LIMIT, "POTCAR": POTCAR_LIMIT}


def bounded_fingerprint(name: str, path: str | Path, *, hpc=None) -> dict:
    """Reject oversized input before streaming its full hash."""
    try:
        if hpc is None:
            target = Path(path)
            size = target.stat().st_size
        else:
            stat = hpc.stat(str(path))
            size = stat.get("size") if isinstance(stat, dict) and stat.get("is_dir") is not True else None
    except Exception as exc:
        raise InputValidationError("INPUT_READ_FAILED", f"{name} 无法检查大小") from exc
    if type(size) is not int or size <= 0 or size > LIMITS[name]:
        raise InputValidationError("INPUT_SIZE_INVALID", f"{name} 为空或超过本轮大小上限")
    return input_fingerprint_local(Path(path)) if hpc is None else input_fingerprint_remote(hpc, str(path))


def read_bound_input(name: str, fingerprint: dict, *, hpc=None) -> bytes:
    """Read exact, capped bytes and compare against the already-bound digest."""
    limit = LIMITS[name]
    size = fingerprint.get("size")
    if type(size) is not int or size <= 0 or size > limit:
        raise InputValidationError("INPUT_SIZE_INVALID", f"{name} 为空或超过本轮大小上限")
    path = fingerprint["normalized_path"]
    try:
        if hpc is None:
            with Path(path).open("rb") as handle:
                data = handle.read(limit + 1)
        else:
            data = bytes(hpc.read_file(path, max_bytes=limit + 1))
    except Exception as exc:
        raise InputValidationError("INPUT_READ_FAILED", f"{name} 无法完整读取") from exc
    if len(data) != size or len(data) > limit:
        raise InputValidationError("INPUT_READ_INCOMPLETE", f"{name} 读取长度与已绑定文件不一致")
    if hashlib.sha256(data).hexdigest() != fingerprint.get("sha256"):
        raise InputValidationError("INPUT_CHANGED", f"{name} 内容与已绑定 SHA-256 不一致")
    return data


def validate_input_set(contents: dict[str, bytes]) -> None:
    """Check mechanical file grammar and POSCAR/POTCAR order, without science claims."""
    for name in LIMITS:
        if name not in contents:
            raise InputValidationError("INPUT_MISSING", f"缺少 {name}，无法完成基础输入检查")
    poscar = validate_poscar(contents["POSCAR"])
    validate_incar(contents["INCAR"])
    validate_kpoints(contents["KPOINTS"])
    species = validate_potcar(contents["POTCAR"], None if poscar.vasp4 else poscar.elements)
    if poscar.vasp4 and len(species) != len(poscar.counts):
        raise InputValidationError("POTCAR_SPECIES_MISMATCH", "POTCAR 数据集数量与 VASP4 POSCAR 原子组数不一致")
