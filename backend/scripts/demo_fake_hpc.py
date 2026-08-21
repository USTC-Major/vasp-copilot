"""Fake HPC Bridge 路演脚本（MVP 6.13-6.24，P1 fake）。

演示与真实 Bridge 相同的安全 schema/状态机：
plan → preflight → HPC_DEPLOY 授权 → 部署 → 提交草稿 → HPC_SUBMIT 授权
→ 幂等提交 → 状态轮询 → terminal → HPC_COLLECT 授权 → 白名单回收。

用法：cd backend && python scripts/demo_fake_hpc.py
"""

from app.hpc.fake import FakeHpcBridge


def main() -> None:
    bridge = FakeHpcBridge(enabled=True)
    bundle = {
        "01_relax/INCAR": "SYSTEM = demo\nNELM = 60\n".encode(),
        "01_relax/KPOINTS": b"k-points\n0\nGamma\n1 1 1\n0 0 0\n",
        "01_relax/submit.sh": b"#!/bin/bash\n",
    }
    print("== Fake HPC Bridge 演示（与真实 Bridge 同一 schema）==")
    plan = bridge.plan_deployment(bundle)
    print("1) 部署计划:", plan.deployment_status, "文件数", plan.file_count,
          "字节", plan.total_bytes, "目标", plan.target_relative_path)
    pf = bridge.preflight(plan.deployment_id)
    print("2) preflight:", pf.passed, [c.check_id for c in pf.checks])
    gd = bridge.authorize_deploy(plan.deployment_id)
    print("3) 一次性 HPC_DEPLOY grant:", gd.grant_id, "single_use", gd.single_use)
    m = bridge.execute_deploy(plan.deployment_id, gd.grant_id, "demo-deploy-key")
    print("4) 部署:", m.verified, "manifest", m.manifest_id, "next", m.next_action)
    d = bridge.create_submission_draft(plan.deployment_id, "01_relax")
    print("5) 提交草稿:", d.submission_draft_id, "幂等键", d.idempotency_key[:24])
    print("   action_preview:", d.action_preview)
    gs = bridge.authorize_submit(d.submission_draft_id)
    print("6) 一次性 HPC_SUBMIT grant:", gs.grant_id)
    r, job = bridge.submit(d.submission_draft_id, gs.grant_id, d.idempotency_key)
    print("7) 已提交:", r.scheduler_job_id, "->", job.status)
    print("   状态轮询(1):", bridge.get_job(job.remote_job_id).status)
    print("   状态轮询(2):", bridge.get_job(job.remote_job_id).status)
    j = bridge.advance(job.remote_job_id)
    print("8) terminal:", j.status, "exit", j.state.exit_code, "可回收", j.collectable)
    gc = bridge.authorize_collection(job.remote_job_id)
    print("9) 一次性 HPC_COLLECT grant，denied:", gc.denied_patterns)
    remote = {"OSZICAR": b"1 F= -100", "OUTCAR": b"ok", "run.log": b"done",
              "POTCAR": b"not-for-doctor", "WAVECAR": b"big", "CHGCAR": b"big"}
    col = bridge.collect(job.remote_job_id, gc.grant_id, remote)
    print("10) 回收:", [f.relative_path for f in col.manifest_files],
          "排除", col.excluded, "diagnosis", col.diagnosis_id)
    print("审记事件数:", len(bridge.audit_log))


if __name__ == "__main__":
    main()
