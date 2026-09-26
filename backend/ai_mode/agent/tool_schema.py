def _all_tools_text() -> str:
    """完整工具说明文本（prompt 内使用的 schema 正文）。"""
    return (
        "- get_state：查看当前计算流程状态（phase/规划/作业/precheck/草稿）。args: {}\n"
        "- ws_list：列出任务本地工作区文件（只读快照，有界）。args: {}\n"
        "- ws_read：读取本地工作区某个文件全文（只读、有界）。args: {\"path\":\"相对路径\"}\n"
        "- mp_search：按化学式只读搜索 Materials Project，返回材料 ID、空间群号和能量信息；结果按热力学稳定性升序（energy_above_hull 从小到大），第一条通常是基态相。返回的 material_id 就是当前 API 的有效形式，导入时必须原样使用；不要凭记忆编造或沿用旧数字 ID。若候选里没有你预期的常见相，先看前几条的能量与空间群，不要据此断言该材料不存在（同化学式条目可能很多，可加大 limit 或让用户给出确切 ID）。后端使用智能设置里的 MP key，不向你暴露密钥。args: {\"formula\":\"BaTiO3\",\"limit\":5}\n"
        "- mp_import_poscar：按明确材料 ID 获取真实结构，由确定性代码生成 POSCAR 预览和一次性确认卡；确认前不写本地文件，绝不上传或提交。material_id 必须是本轮 mp_search 实际返回的值（形如 mp-aaaditqj），旧数字 ID 已被 MP 停用、会得到 MP_ID_STALE。默认保存任务本地工作区根目录 POSCAR，可用已规划 job_key 指定子目录。多个候选须先让用户选 ID/晶相，不得擅自挑选。args: {\"material_id\":\"mp-aaaditqj\",\"job_key\":\"\"}\n"
        "- hpc_list：列出超算工作区（hpc_dir）远端目录内容（只读；计算发生地，超算上的文件一律用它看）。args: {\"path\":\"相对子目录，可空\"}\n"
        "- hpc_read：读取超算工作区内某个文本文件（只读、有界）。args: {\"path\":\"相对路径\"}\n"
        "- hpc_upload：请求把已登记的本地工作区文件上传到超算工作区；默认只生成逐次确认卡，确认前绝不写远端（若用户在智能设置里开启了「上传」免批，则由 Toolbox 直接执行并返回 [AUTO_APPROVED] 回执）。一次调用只传一个文件——要传多个文件，请在**同一条回复里连续写多个 hpc_upload 标记**（协议支持一次多个工具），系统会把它们合并成一组让用户一次点击整批批准；不要一个文件一轮。args: {\"artifact_id\":\"用户已登记文件 ID\",\"job_key\":\"作业 key\"}\n"
        "- generate_potcar：在指定作业目录用 vaspkit 生成 POTCAR。用哪套赝势由 vaspkit 自己的默认规则决定，你不能指定元素变体、不能传命令或参数；该目录里必须已有 POSCAR。每次都会先弹确认卡、**永不自动批准**；生成后回执只给数据集名、与 POSCAR 的元素顺序是否一致、大小与哈希，不回显 POTCAR 内容。缺 POTCAR 时用它，不要要求用户手工拼接。args: {\"job_key\":\"relax\",\"attempt_id\":\"从get_state获取\"}\n"
        "- deploy_submit_script：把**用户在智能设置里配置的模板脚本**逐字节复制到作业目录（目标名＝模板文件名）。只复制、不修改、不执行；脚本内容对你不可见。若该目录里已有其它 *.sh 会被拒绝（不覆盖、不删除），需要你如实告诉用户。复制后仍需用户认领该脚本才能进预检/草稿/提交。args: {\"job_key\":\"relax\",\"attempt_id\":\"从get_state获取\"}\n"
        "- stop_monitor：终止当前计算流程（用户明确表示不做了/换思路/作业作废时调用）：全部未完成作业置 canceled、停止后台监控与后续提交流程；已在超算运行的作业会给出 scancel 建议。args: {}\n"
        "- plan：自主制定计算计划并落库（作业数/类型/顺序由你决定）。作业 key 请用语义化英文名（如 relax/static/band/dos），label 用中文。有先后依赖的作业必须用 requires 声明依赖（如 {\"key\":\"relax/static\",\"requires\":[\"relax\"]}）；前序 completed 只会解锁后续，重新预检并由用户再次确认后才能提交。依赖链作业 key/目录用嵌套路径依次往下建（relax → relax/static → relax/static/dos），独立作业才并列。args: {\"strategy\":\"策略\",\"jobs\":[{\"key\":\"relax\",\"label\":\"结构优化\",\"kind\":\"relax\"}]}\n"
        "- copy_inputs：请求把用户已登记输入复制到计算目录；只接受 artifact_id 与作业 key，确认前不写文件。args: {\"artifact_ids\":[\"用户已登记文件 ID\"],\"job_key\":\"relax\"}\n"
        "- propose_incar：提交有序、强类型 INCAR 参数草稿；只生成确定性 diff 与一次性确认卡，确认后才原子写入。args: {\"job_key\":\"relax\",\"entries\":[{\"tag\":\"ENCUT\",\"value\":520}]}\n"
        "- generate_kpoints：使用现有确定性生成器提出自动网格 KPOINTS；确认前不写文件。args: {\"job_key\":\"relax\",\"grid\":[6,6,6],\"centering\":\"Gamma\"}\n"
        "- precheck：对选中计算当前尝试硬检查 INCAR/POSCAR/KPOINTS/POTCAR 和已认领提交脚本；任一缺失均阻止提交。args: {\"job_key\":\"relax\",\"attempt_id\":\"从get_state获取\"}\n"
        "- draft：为已规划作业生成只读提交预览；发现脚本候选后必须由用户显式认领，系统不会自动认领或生成脚本。args: {\"job_key\":\"relax\",\"attempt_id\":\"从get_state获取\"}\n"
        "- submit：把流程停在「待你确认提交」边界（同样不会代替用户执行；真实提交由系统在用户确认后执行）。args: {\"job_key\":\"relax\",\"attempt_id\":\"从get_state获取\"}\n"
        "- select_jobs：按用户要求选择本次提交哪些作业/跳过哪些（只调规划不提交；跳过作业不生成草稿也不提交）。args: {\"submit\":[\"relax\"],\"skip\":[\"static\"]}\n"
        "- diagnose_job：查询作业并返回有文件证据的诊断及恢复建议。args: {\"job_key\":\"band\"}\n"
        "- retry_job：仅在用户明确要求重试时调用；只为已有诊断的 failed/not_converged 作业生成恢复确认卡。当前 attempt 存在时必须传 attempt_id；仅旧终态无 attempt_id 时可只传 job_key。批准后仅恢复待准备状态，保留历史与输出；unknown 禁止重试，后续写入、上传、硬预检和提交必须重新逐次授权。args: {\"job_key\":\"band\",\"attempt_id\":\"从get_state获取\"}\n"
        "- remote_file_context：只读查看当前任务的计算/尝试、研究根、人工文件范围和动作摘要。args: {}\n"
        "- remote_inspect：只读查看远端绝对路径，text 受内容来源限制且回执有界。args: {\"path\":\"/research/input\",\"view\":\"list\",\"limit\":100}\n"
        "- remote_file_plan：仅用已有人建的 scope_id/version 及其绑定的 job_key/attempt_id 提出 1–32 项精确文件计划，返回待人工完整审阅卡；不得批准。item.op 仅 copy/symlink/write_text/mkdir，on_conflict 仅 fail；copy/symlink 需精确 source.absolute_path，write_text 需 text。args: {\"scope_id\":\"32位ID\",\"scope_version\":1,\"job_key\":\"relax\",\"attempt_id\":\"现有尝试ID\",\"idempotency_key\":\"本次稳定key\",\"items\":[{\"item_id\":\"input1\",\"op\":\"mkdir\",\"destination\":{\"root_id\":\"32位ID\",\"relative_path\":\"relax\"},\"on_conflict\":\"fail\"}]}\n"
        "- remote_file_status：只读查文件动作卡状态/逐项摘要，不显示完整 manifest。args: {\"action_id\":\"32位ID\"}\n"
        "- remote_file_history：只读列文件动作活动及历史摘要。args: {\"limit\":20,\"cursor\":\"0\"}\n"
        "- remote_file_reconcile：仅对已有 unknown 动作核对远端证据，不重新执行文件操作。args: {\"action_id\":\"32位ID\"}\n"
        "文件范围来自用户在任务里选定工作区这一次授权，并由系统在规划落地后自动为每个待准备作业派生（见 remote_file_context 的 file_scopes，选 job_key/attempt_id 匹配的那条）；你不需要、也不能自己创建或激活它。文件计划首次批准必须由用户在同任务 Toolbox 完整审阅并确认；文件执行完成不代表科学适用。软链接可能让后续计算回写根外来源。你不能设置研究根、创建/撤销 scope、批准或拒绝文件卡，也不能请求通用 HTTP、shell 或全局远端 mkdir。"
    )


#: 依赖外部配置的工具：未就绪时既不该出现在提示词里，也要明确告诉模型该引导用户做什么。
_MP_TOOLS = ("mp_search", "mp_import_poscar")
_HPC_TOOLS = ("hpc_list", "hpc_read", "hpc_upload")
_POTCAR_TOOLS = ("generate_potcar",)
_SCRIPT_TOOLS = ("deploy_submit_script",)

_MP_NOT_READY = (
    "- 注意：当前尚未配置 Materials Project API key，mp_search / mp_import_poscar 本轮不可用。"
    "需要结构时，请引导用户改用本地 POSCAR/CIF 文件，或先到智能设置里填写 MP key；"
    "不要反复重试这两个工具。\n"
)

_HPC_NOT_READY = (
    "- 注意：当前尚未配置超算（SSH 主机/用户名），hpc_list / hpc_read / hpc_upload 本轮不可用。"
    "需要远端文件操作时，请先引导用户到 Toolbox 执行设置里完成站点配置；"
    "不要反复重试这些工具，也不要把本地工作区快照当成超算内容。\n"
)

_POTCAR_NOT_READY = (
    "- 注意：POTCAR 自动生成未开启（智能设置 → POTCAR）。需要 POTCAR 时，请引导用户去打开该开关"
    "（打开时会显示风险提示与免责声明），或让用户自己把 POTCAR 放进作业目录；"
    "不要反复重试 generate_potcar，也不要尝试用自由命令拼接 POTCAR。\n"
)

_SCRIPT_NOT_READY = (
    "- 注意：提交脚本复制未开启，或还没配置模板路径（智能设置 → 提交脚本）。需要脚本时，"
    "请引导用户填写超算上的模板路径（.sh）并打开开关，或让用户自己把脚本放进作业目录；"
    "不要反复重试 deploy_submit_script，也不得自己生成或修改脚本。\n"
)


def tool_schema_text(*, ssh_ready: bool = True, mp_ready: bool = True,
                     potcar_ready: bool = True, script_ready: bool = True) -> str:
    """给 LLM 的工具说明；按实际可用性裁剪依赖外部配置的工具。

    默认（全部 flag 为 True）返回完整说明，便于测试与非配置场景复用；
    生产调用方按 SSH / MP / POTCAR / 脚本模板的配置情况传参，
    避免模型看到注定失败的工具（开关关掉时连工具说明都不给）。
    """
    text = _all_tools_text()
    if ssh_ready and mp_ready and potcar_ready and script_ready:
        return text
    gated = set()
    if not mp_ready:
        gated |= set(_MP_TOOLS)
    if not ssh_ready:
        gated |= set(_HPC_TOOLS)
    if not potcar_ready:
        gated |= set(_POTCAR_TOOLS)
    if not script_ready:
        gated |= set(_SCRIPT_TOOLS)
    kept = [line for line in text.splitlines(keepends=True)
            if not any(line.startswith(f"- {name}：") for name in gated)]
    notes = ""
    if not mp_ready:
        notes += _MP_NOT_READY
    if not ssh_ready:
        notes += _HPC_NOT_READY
    if not potcar_ready:
        notes += _POTCAR_NOT_READY
    if not script_ready:
        notes += _SCRIPT_NOT_READY
    kept.append(notes)
    return "".join(kept)
