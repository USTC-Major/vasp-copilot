from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ...core.errors import (ConflictError, NotFoundError,
                                SecurityError, UnsupportedMediaTypeError)
from ...schemas.api import ApiEnvelope
from ...services.diagnosis_service import DiagnosisService, _load_parsed, detect_files
from ...llm import get_explainer
from .deps import extractor, get_request_id, settings, store

router = APIRouter()
diagnosis_service = DiagnosisService()


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    diagnosis_id: str
    selected_root: Optional[str] = None
    job_log: Optional[str] = None
    resources: Optional[dict[str, Any]] = None
    language: str = "zh-CN"
    llm_explanation: bool = False


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str


def _new_diag_id() -> str:
    return "diag_" + uuid.uuid4().hex[:8]


@router.post("/diagnosis/upload", response_model=ApiEnvelope)
async def upload(file: UploadFile = File(...),
                 x_request_id: str = Depends(get_request_id),
                 session_id: Optional[str] = None) -> ApiEnvelope:
    data = await file.read()
    diag_id = _new_diag_id()
    dest = Path(settings.data_dir) / "runs" / diag_id
    extractor.extract(data, dest)
    detected = detect_files(dest)
    store.create(diag_id, detected, dest, session_id=session_id)
    return ApiEnvelope(request_id=x_request_id, data={
        "diagnosis_id": diag_id,
        "diagnosis_status": "uploaded",
        "session_id": session_id,
        "detected": detected.model_dump(exclude_none=True),
        "detected_run": detected.model_dump(exclude_none=True),
    })


@router.post("/diagnosis/run", response_model=ApiEnvelope)
async def run(req: RunRequest,
              x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    record = store.get(req.diagnosis_id)
    parsed = _load_parsed(record.base_dir, req.job_log)
    result, body, fix_files = diagnosis_service.run_diagnosis(
        parsed, record.base_dir, llm_explanation=req.llm_explanation,
        settings=settings)
    result.diagnosis_id = req.diagnosis_id
    record.result = result
    record.report_text = body
    record.report_metadata = result.report
    if result.recommended_fixes and result.recommended_fixes[0].safe_to_generate:
        record.fix_files = {result.recommended_fixes[0].fix_id: fix_files}
    record.diagnosis_status = "succeeded"
    store.put(record)

    counts: dict[str, int] = {}
    for i in result.issues:
        counts[i.severity.value] = counts.get(i.severity.value, 0) + 1
    first = result.recommended_fixes[0] if result.recommended_fixes else None
    fix_available = bool(first is not None and first.safe_to_generate)
    return ApiEnvelope(request_id=x_request_id, data={
        "diagnosis_id": req.diagnosis_id,
        "diagnosis_status": "succeeded",
        "result_url": f"/api/v1/diagnosis/{req.diagnosis_id}",
        "issue_count": counts,
        "summary": _summary_object(result),
        "plots": _plots_compat(result.plots),
        "detected_run": result.detected_run.model_dump(exclude_none=True)
        if result.detected_run is not None else None,
        "report": _report_compat(
            record, dict(result.report.model_dump(exclude_none=True))
            if result.report is not None else None),
        "report_ready": bool(record.report_text),
        "fix_available": fix_available,
        "mode": result.provenance.mode.value,
    })


@router.post("/diagnosis/{diagnosis_id}/explain", response_model=ApiEnvelope)
async def explain(diagnosis_id: str, req: ExplainRequest,
                  x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    record = store.get(diagnosis_id)
    if record.result is None:
        return ApiEnvelope(request_id=x_request_id, data={
            "diagnosis_id": diagnosis_id,
            "diagnosis_status": record.diagnosis_status,
            "answer": "请先运行诊断后再追问。",
        })
    explainer = get_explainer(settings)
    if explainer is None:
        return ApiEnvelope(request_id=x_request_id, data={
            "diagnosis_id": diagnosis_id,
            "diagnosis_status": record.diagnosis_status,
            "answer": "大模型解释未启用，暂无法回答追问。",
        })
    try:
        answer = explainer.chat(record.result, req.question)
    except Exception:
        return ApiEnvelope(request_id=x_request_id, data={
            "diagnosis_id": diagnosis_id,
            "diagnosis_status": record.diagnosis_status,
            "answer": "大模型服务暂不可用，无法回答追问。",
            "degraded": True,
        })
    return ApiEnvelope(request_id=x_request_id, data={
        "diagnosis_id": diagnosis_id,
        "diagnosis_status": record.diagnosis_status,
        "answer": answer,
    })


def _summary_object(result) -> dict:
    """前端期望结构化 summary（headline/highest_severity/issue_count）。"""
    order = ["critical", "high", "medium", "low", "info"]
    counts = {level: 0 for level in order}
    highest = "info"
    for issue in result.issues:
        level = issue.severity.value
        counts[level] = counts.get(level, 0) + 1
        if order.index(level) < order.index(highest):
            highest = level
    return {
        "headline": result.summary or "诊断完成",
        "highest_severity": highest,
        "issue_count": counts,
    }


def _plots_compat(plots: dict) -> dict:
    """前端 SCF 序列读 energy 字段；磁矩读 element/initial_moment/final_moment。
    向后兼容：同时保留后端原有字段，避免破坏既有测试。"""
    out = dict(plots or {})
    scf = out.get("scf")
    if isinstance(scf, dict):
        series = []
        for item in scf.get("series", []):
            row = dict(item)
            if "energy" not in row and "energy_ev" in row:
                row["energy"] = row["energy_ev"]
            series.append(row)
        out["scf"] = {**scf, "series": series}
    mag = out.get("magnetization")
    if isinstance(mag, dict):
        series = []
        for idx, item in enumerate(mag.get("series", []), start=1):
            row = dict(item)
            row.setdefault("atom_index", row.get("ion") or idx)
            if "final_moment" not in row and "tot" in row:
                row["final_moment"] = row["tot"]
            if "element" not in row:
                row["element"] = ""
            if "initial_moment" not in row:
                row["initial_moment"] = None
            series.append(row)
        out["magnetization"] = {**mag, "series": series}
    return out


def _report_compat(record, report: dict) -> dict:
    ready = bool(record.report_text)
    return {
        "report_id": (report or {}).get("report_id") or f"report_{record.diagnosis_id}",
        "format": (report or {}).get("format") or "markdown",
        "ready": ready,
        "download_url": (f"/api/v1/diagnosis/{record.diagnosis_id}/report" if ready else None),
    }


@router.get("/diagnosis/{diagnosis_id}", response_model=ApiEnvelope)
async def get_result(diagnosis_id: str,
                     x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    record = store.get(diagnosis_id)
    if record.result is None:
        return ApiEnvelope(request_id=x_request_id, data={
            "diagnosis_id": diagnosis_id,
            "diagnosis_status": record.diagnosis_status,
        })
    data = record.result.model_dump(exclude_none=True)
    data["summary"] = _summary_object(record.result)
    data["plots"] = _plots_compat(record.result.plots)
    data["detected_run"] = record.result.detected_run.model_dump(exclude_none=True) \
        if record.result.detected_run is not None else data.get("detected_run")
    data["report"] = _report_compat(
        record, dict(record.result.report.model_dump(exclude_none=True))
        if record.result.report is not None else None)
    return ApiEnvelope(request_id=x_request_id, data=data)


@router.get("/diagnosis/{diagnosis_id}/report")
async def get_report(diagnosis_id: str,
                     x_request_id: str = Depends(get_request_id)) -> Response:
    record = store.get(diagnosis_id)
    if record.report_text is None:
        raise ConflictError("REPORT_NOT_READY", "report is not ready yet")
    return Response(
        content=record.report_text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="diagnosis_report.md"'},
    )


@router.get("/diagnosis/{diagnosis_id}/download-fix")
async def download_fix(diagnosis_id: str,
                       x_request_id: str = Depends(get_request_id)) -> Response:
    record = store.get(diagnosis_id)
    if not record.fix_files:
        raise ConflictError("FIX_NOT_AVAILABLE", "no safe auto fix is available")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fix_id, files in record.fix_files.items():
            for name, content in files.items():
                zf.writestr(f"{fix_id}/{name}", content)
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="fix_package.zip"'},
    )


_BINARY_KINDS = {"WAVECAR", "CHGCAR"}
_BINARY_EXTS = (".zip", ".gz", ".tar", ".tgz", ".rar",
                ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico")


def _preview_kind(name: str) -> str:
    known = {
        "INCAR": "incar", "POSCAR": "poscar", "KPOINTS": "kpoints",
        "POTCAR": "potcar", "OUTCAR": "outcar", "OSZICAR": "oszicar",
        "CONTCAR": "concar", "VASPRUN.XML": "vasprun", "DOSCAR": "doscar",
        "README": "readme", "REPORT": "report",
    }
    if name.upper().endswith(".CIF"):
        return "cif"
    return known.get(name.upper(), "file")


def _preview_cursor_encode(offset: int) -> str:
    import base64
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii")


def _preview_cursor_decode(cursor: str) -> int:
    import base64
    from ...core.errors import ValidationError
    try:
        return int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - opaque cursor must fail gracefully
        raise ValidationError("INVALID_CURSOR", "invalid opaque preview cursor") from exc


@router.get("/diagnosis/{diagnosis_id}/preview")
async def preview(
    diagnosis_id: str,
    path: str,
    mode: str = "head",
    start_line: int = 1,
    max_lines: int | None = None,
    cursor: str | None = None,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    record = store.get(diagnosis_id)
    root = record.base_dir.resolve()
    candidate = (root / path.lstrip("/")).resolve()
    if not str(candidate).startswith(str(root)):
        raise SecurityError("PATH_TRAVERSAL", "path escapes run root")
    if not candidate.is_file():
        raise NotFoundError("FILE_NOT_FOUND", "file not found")

    name = candidate.name.upper()
    if name == "POTCAR":
        raise SecurityError("FILE_PREVIEW_POLICY_DENIED",
                            "POTCAR is not previewable", http_status=403)
    if (_preview_kind(name) in ("wavecar", "chgcar")) or name.endswith(_BINARY_EXTS):
        raise UnsupportedMediaTypeError(
            "FILE_PREVIEW_UNSUPPORTED_BINARY", "binary file not previewable")

    data = candidate.read_bytes()
    if b"\x00" in data[:8192]:
        raise UnsupportedMediaTypeError(
            "FILE_PREVIEW_UNSUPPORTED_BINARY", "binary file not previewable")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UnsupportedMediaTypeError(
            "FILE_PREVIEW_UNSUPPORTED_BINARY", "undecodable file not previewable")

    lines = text.splitlines()
    total = len(lines)
    is_outcar = name == "OUTCAR"

    if is_outcar:
        limit = max(1, settings.outcar_preview_lines)
        if max_lines is not None:
            limit = max(1, min(max_lines, limit))
        if mode == "tail":
            seg = lines[max(0, total - limit):]
            start = max(1, total - len(seg) + 1)
        elif mode == "range":
            start = max(1, start_line)
            seg = lines[start - 1:start - 1 + limit]
        else:  # head
            start = max(1, start_line)
            seg = lines[start - 1:start - 1 + limit]
        end = max(start - 1, start + len(seg) - 1)
        truncated = False
        next_cursor = None
    else:
        effective_max_lines = max(1, settings.max_preview_lines)
        byte_cap = max(1, settings.max_preview_bytes)
        if max_lines is not None:
            effective_max_lines = max(1, min(max_lines, effective_max_lines))
        if mode == "tail":
            seg = lines[max(0, total - effective_max_lines):]
            start = max(1, total - len(seg) + 1)
            end = total
            truncated = False
            next_cursor = None
        else:
            if cursor is not None:
                idx = _preview_cursor_decode(cursor)
            else:
                idx = max(0, start_line - 1)
            idx = max(0, min(idx, total))
            seg = []
            used_bytes = 0
            for i in range(idx, total):
                line = lines[i]
                if seg and used_bytes + len(line.encode("utf-8")) + 1 > byte_cap:
                    break
                if len(seg) >= effective_max_lines:
                    break
                seg.append(line)
                used_bytes += len(line.encode("utf-8")) + 1
            start = idx + 1
            end = idx + len(seg)
            next_idx = idx + len(seg)
            truncated = next_idx < total
            next_cursor = _preview_cursor_encode(next_idx) if truncated else None

    content = "\n".join(seg)
    return ApiEnvelope(request_id=x_request_id, data={
        "file_id": f"{diagnosis_id}:{path.lstrip('/')}",
        "name": candidate.name,
        "kind": _preview_kind(candidate.name),
        "mime_type": "text/plain",
        "encoding": "utf-8",
        "sha256": __import__("hashlib").sha256(data).hexdigest(),
        "preview": {
            "content": content,
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "returned_bytes": len(content.encode("utf-8")),
            "truncated": truncated,
            "next_cursor": next_cursor,
        },
        "policy": {
            "max_preview_bytes": settings.max_preview_bytes,
            "max_preview_lines": (settings.outcar_preview_lines
                                  if is_outcar else settings.max_preview_lines),
            "binary_rejected": True,
            "sensitive_content_redacted": False,
        },
    })