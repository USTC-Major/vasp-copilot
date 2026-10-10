# PP-22A 能量 API 合同

实现合同：2026-10-11；独立前缀 `/api/v1/toolbox/postprocessing/energy`。所有 JSON 响应带 `mode: "toolbox"`。错误沿用 Toolbox `{error:{code,message,...}}`。独立 `ec_<uuid>` 比较集、`es_<uuid>` 来源样本；不进入 DOS/band listing。

## 接口

| Method/path | 请求 | 响应 |
|---|---|---|
| GET `/collections` | 无 | `{mode,collections:Collection[]}` |
| POST `/collections` | `{title?:string}` | 201 `{mode,collection}` |
| GET `/collections/{id}` | 无 | `{mode,collection}` |
| DELETE `/collections/{id}` | 无 | `{mode,deleted:true}` |
| POST `/collections/{id}/outcar` | 原始字节；query `name=OUTCAR`, `relative_path?`, `expected_revision` | 201 `{mode,collection}`；同步解析，新独立 sample ID |
| POST `/collections/{id}/manual` | `{expected_revision,name,composition,energy_fields,energy_basis,unit:"eV",reference_note?:string}` | 201 `{mode,collection}` |
| POST `/collections/{id}/csv` | UTF-8 CSV原始字节；query `expected_revision` | 201 `{mode,collection}`；整批验证后原子纳入 |
| PUT `/collections/{id}/configuration` | `Configuration`（见下） | `{mode,collection}`；整体保存用户确认表，清除旧结果 |
| POST `/collections/{id}/calculate` | `{expected_revision}` | `{mode,collection}`；计算并持久保存，硬错400/409且没有新结果 |
| GET `/collections/{id}/export?format=json\|csv` | 无 | 附件；包含原始样本、覆盖值、组、公式明细和警告；只有最新有效结果可导出 |
| POST `/task-sources/preview` | `{project_id,task_id,job_key,attempt_id}` | `{mode,preview:{id,source,files:[{name,available,size_bytes,reason?}],expires_at,warnings}}` |
| POST `/collections/{id}/task-sources/import` | `{expected_revision,preview_id,name?:string}` | 201 `{mode,collection}`；仅取完整稳定 OUTCAR，新样本不可变缓存 |
| POST `/collections/{id}/reuse` | `{expected_revision,source_collection_id,sample_id}` | 201 `{mode,collection}`；核验完整缓存后离线复制，新ID保留原身份；不访问SSH |

上传逐份执行；同一 collection 最多100样本，64 MiB/文件、128 MiB/collection原始文件、能量存储总1 GiB。批量多份同名 OUTCAR 每份独立 UUID；中文空格相对路径是展示元数据，不能用作落盘路径。上传不替换旧来源；修改来源用新样本，再修改组引用。

## 精确对象

```json
{
  "id":"ec_<uuid>","schema_version":"pp.energy.v1","title":"吸附比较",
  "revision":1,"created_at":"ISO UTC","updated_at":"ISO UTC",
  "samples":[],"groups":[],"result":null,
  "limits":{"max_file_bytes":67108864,"max_collection_bytes":134217728,"max_samples":100}
}
```

Sample 的 `parsed` 保留解析原值，`override` 为单独人工补充或覆盖；effective 值用于计算并列明来源。各字段始终返回，null 表示未知。

```json
{
  "id":"es_<uuid>","name":"OUTCAR","revision":1,
  "source":{"kind":"local_upload","original_name":"OUTCAR","relative_path":"表面/位点 A/OUTCAR","sha256":"...","size_bytes":123,"imported_at":"ISO UTC"},
  "parsed":{
    "energy_fields":{"sigma_to_zero_ev":-112.0,"without_entropy_ev":-112.1,"free_energy_toten_ev":-112.2},
    "composition":{"Pt":4,"C":1,"O":1},
    "status":{"completion":"completed","electronic_converged":true,"ionic_converged":null,"ionic_applicability":"not_applicable"},
    "metadata":{"parameters":{},"support":{"automatic_comparison":true,"reasons":[]}},
    "provenance":{"run_segment":1,"selected_ionic_step":1,"field_lines":{}},
    "errors":[],"warnings":[]
  },
  "override":null,
  "role_suggestion":{"role":"adsorbed","reasons":["名称提示，仅供核对"],"confidence":"hint"},
  "role":null,"included":false,"confirmed":false,"accepted_warnings":false,"confirmation_fingerprint":null
}
```

`source.kind` 为 `local_upload|manual|csv|task_result`。任务来源另含 project/task/job/attempt、submission_action_id、slurm_id、scheduler_target、remote_metadata、snapshot_sha256、cached_at；导出移除绝对远端目录及SSH路径。`role` 为 `clean_slab|adsorbate|adsorbed|material|element_reference|null`；建议不会自动确认。manual/csv 样本 status 均 unknown，需显式接受未知状态；manual 的 `energy_basis` 必须对应一个有值字段，组级不混用字段。人工组成只允许正整数元素计数。

Configuration 是完整组列表及可部分更新的样本行；用户须在核对后显式设置 `confirmed:true`，字段口径在每组设置 `basis_confirmed:true`。

```json
{
  "expected_revision":7,"title":"比较组",
  "samples":[{"sample_id":"es_a","name":"构型 A","role":"adsorbed","included":true,"confirmed":true,"accepted_warnings":true,"override":null}],
  "groups":[
    {"id":"g_ads","name":"CO 吸附","kind":"adsorption","energy_basis":"sigma_to_zero_ev","basis_confirmed":true,
     "clean_sample_id":"es_slab","adsorbate_sample_id":"es_co","reference_units":1,
     "targets":[{"sample_id":"es_a","adsorbate_count":1}],"reference_note":"每个 CO 分子"},
    {"id":"g_form","name":"材料形成能","kind":"formation","energy_basis":"sigma_to_zero_ev","basis_confirmed":true,
     "element_references":{"Al":"es_al","O":"es_o2"},"targets":[{"sample_id":"es_mat"}],"reference_note":"用户指定元素参考态"}
  ]
}
```

样本 override 非null时形状 `{composition?:{...},energy_fields?:{...},energy_basis?:EnergyBasis,unit:"eV",note:string}`，明确保留人工依据；缺能量/组成允许补充，但运行类型不支持、多段/身份歧义、已知方法/赝势/U冲突不能被覆盖或 warning 接受消除。能量三个键可为有限数或null；manual/CSV要求声明一个有值口径。

没有任何可归属完整能量块的OUTCAR是硬错，不能用覆盖值伪装自动读取成功；用户可以创建独立manual样本并在reference_note记录原文件引用。有完整归属块的字段补充与可恢复的组成缺失，允许通过带note的override补充，保留parsed原值及未知状态。

修改能量、组成、来源、role、group的参考/系数/field使旧result清除。受影响确认自动失效；本次请求中重新提交 `confirmed:true` 和 `accepted_warnings:true` 可在新指纹上重新确认。未提交行不会沿用已失效确认。每次有效写入revision递增，错误revision返回409 `ENERGY_REVISION_CONFLICT`。

## 计算结果

```json
{
 "schema_version":"pp.energy.result.v1","input_fingerprint":"...","calculated_at":"ISO UTC",
 "groups":[{"id":"g_ads","kind":"adsorption","name":"CO 吸附","energy_basis":"sigma_to_zero_ev",
  "rows":[{"sample_id":"es_a","name":"构型 A","delta_ev":-2.0,"normalized_ev":-2.0,"normalization":"per_adsorbate","unit":"eV/adsorbate",
   "formula":"E_target - E_clean - n * E_reference / m",
   "terms":[{"sample_id":"es_a","coefficient":1.0,"energy_ev":-112.0,"contribution_ev":-112.0}],"warnings":[]}],
  "warnings":[]}],"warnings":[]
}
```

形成能的 `normalization="per_atom"`, `unit="eV/atom"`；delta为每目标计算胞eV，terms包含目标及每元素参考系数/能量/贡献，记录元素与计数。结果继承所有参与样本warning和状态未知/未完成。不同吸附物数量可明确计算，组级warning提示覆盖度不等，不能声称同条件排名。没有能量/组成/参考、非有限值、组成不守恒和已知科学冲突硬阻断。

CSV入口列：`name,relative_path,composition,energy_basis,energy_ev,unit,reference_note`，composition 为 JSON 字符串，例如 `"{""Al"":2,""O"":3}"`；不自动从名称推组成。空unit不默认为eV。CSV导出所有用户文本防公式执行（以 =,+,-,@ 或制表控制符开头的文本加单引号）；数值保持数字。JSON严格有限值。

任务专用状态允许已提交的 queued/running/completed/failed/not_converged/cancelled；仍校验当前attempt、executed提交action、draft、slurm_id、SSH目标绑定。传输前后身份和远端文件元数据稳定、完整字节SHA一致才发布本地缓存，源变化返回409。旧DOS/band仍要求终态，任务download_result语义不变。
