from __future__ import annotations

"""VASP-Doctor 端到端冒烟脚本。

在 backend 目录下运行（需可 import app）：
    python scripts/smoke_test.py

走完整链路：upload -> run -> get -> report -> preview -> explain -> download-fix。
不要求配置 LLM：LLM 关闭时验证降级行为，开启时验证真实/降级回答均可用。
"""

import io
import sys
import zipfile

from fastapi.testclient import TestClient

from app.main import app

CLIENT = TestClient(app)
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def _make_zip() -> bytes:
    files = {
        "INCAR": (
            "SYSTEM = smoke demo\n"
            "NELM = 40\n"
            "ISMEAR = 0\n"
            "SIGMA = 0.05\n"
            "EDIFF = 1e-5\n"
            "PREC = Normal\n"
        ),
        "POSCAR": (
            "Smoke\n"
            "1.0\n"
            "3.0 0.0 0.0\n"
            "0.0 3.0 0.0\n"
            "0.0 0.0 3.0\n"
            "Si\n"
            "1\n"
            "Direct\n"
            "0.0 0.0 0.0\n"
        ),
        "KPOINTS": (
            "Automatic mesh\n"
            "0\n"
            "Gamma\n"
            "1 1 1\n"
            "0 0 0\n"
        ),
        "OSZICAR": (
            "  1 F=-100.00000000 E0=-100.00000000  d E=-0.10000000\n"
            "  2 F=-99.00000000  E0=-99.00000000   d E=-1.00000000\n"
        ),
        "OUTCAR": (
            " vasp.6.3.0  ...\n"
            "   MAGNETIZATION\n"
            "   E-FERMI\n"
            "   FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)\n"
        ),
        "vasp.out": "[timestamp] running job on 1 node\n",
        "notes.txt": "\n".join(f"note {i}" for i in range(2000)),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def main() -> int:
    # --- upload ---
    r = CLIENT.post(
        "/api/v1/diagnosis/upload",
        files={"file": ("run.zip", _make_zip(), "application/zip")},
    )
    check("upload 200", r.status_code == 200, r.text[:140])
    diag = r.json()["data"]["diagnosis_id"]
    check("upload 返回 diag_id", diag.startswith("diag_"), diag)

    # --- run ---
    r = CLIENT.post("/api/v1/diagnosis/run", json={"diagnosis_id": diag})
    check("run 200", r.status_code == 200, r.text[:140])
    rd = r.json()["data"]
    check("run succeeded", rd["diagnosis_status"] == "succeeded")
    check("run report_ready", rd["report_ready"] is True)
    check("run mode 有效", rd["mode"] in ("rule_based", "rule_plus_llm"), rd["mode"])

    # --- get ---
    r = CLIENT.get(f"/api/v1/diagnosis/{diag}")
    g = r.json()["data"]
    check("get 含 issues", "issues" in g)
    check("get 含 next_step", "next_step" in g)
    check("get 含 report 元数据", "report" in g)

    # --- report ---
    r = CLIENT.get(f"/api/v1/diagnosis/{diag}/report")
    check("report 200 markdown",
          r.status_code == 200 and r.headers["content-type"].startswith("text/markdown"))

    # --- preview（设计 6.2：结构化 JSON、OUTCAR 限行、普通文本截断+游标翻页） ---
    r = CLIENT.get(f"/api/v1/files/{diag}/preview", params={"path": "OUTCAR"})
    ok = r.status_code == 200
    if ok:
        pd = r.json()["data"]
        ok = pd["kind"] == "outcar" and pd["policy"]["max_preview_lines"] == 500
    check("preview OUTCAR 结构化且限 500 行", ok)

    r = CLIENT.get(f"/api/v1/files/{diag}/preview",
                   params={"path": "notes.txt", "max_lines": 50})
    ok = r.status_code == 200
    p = None
    if ok:
        p = r.json()["data"]["preview"]
        ok = p["truncated"] is True and bool(p["next_cursor"])
    check("普通文本截断并返回 next_cursor", ok, f"next_cursor={p and p['next_cursor']}")
    if ok:
        r2 = CLIENT.get(f"/api/v1/files/{diag}/preview",
                        params={"path": "notes.txt", "cursor": p["next_cursor"]})
        ok2 = (r2.status_code == 200
               and r2.json()["data"]["preview"]["start_line"] == 51)
        check("用 next_cursor 翻到下一页", ok2)

    r = CLIENT.get(f"/api/v1/files/{diag}/preview", params={"path": "../secret"})
    check("preview 路径穿越 403", r.status_code == 403)

    # --- explain（LLM 默认关闭时返回降级文案，开启时返回回答） ---
    r = CLIENT.post(
        f"/api/v1/diagnosis/{diag}/explain",
        json={"question": "为什么 SCF 不收敛？"},
    )
    check("explain 200 且有 answer",
          r.status_code == 200 and bool(r.json()["data"].get("answer")), r.text[:140])

    # --- download-fix（依 fix_available：有安全自动修复则 200 zip，否则 409） ---
    r = CLIENT.get(f"/api/v1/diagnosis/{diag}/download-fix")
    check("download-fix 200/409", r.status_code in (200, 409), str(r.status_code))

    print()
    if FAILED:
        print(f"端到端冒烟失败：{len(FAILED)} 项 -> {FAILED}")
        return 1
    print("端到端冒烟全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())