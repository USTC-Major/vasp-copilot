# PP-22A 能量 API 合同

实现合同：2026-10-11；独立前缀 `/api/v1/toolbox/postprocessing/energy`。所有 JSON 响应带 `mode: "toolbox"`。错误沿用 Toolbox `{error:{code,message,...}}`。独立 `ec_<uuid>` 比较集、`es_<uuid>` 来源样本；不进入 DOS/band listing。

## 接口

| Method/path | 请求 | 响应 |
|---|---|---|
| GET `/collections` | 无 | `{mode,collections:Collection[]}` |
| POST `/collections` | `{title?:string,analysis_kind?:"adsorption"\|"formation"}` | 201 `{mode,collection}`；新界面必须选择类型，省略仅兼容旧客户端 |
| GET `/collections/{id}` | 无 | `{mode,collection}` |
| DELETE `/collections/{id}` | 无 | `{mode,deleted:true}` |
| POST `/collections/{id}/outcar` | 原始字节；query `name=OUTCAR`, `relative_path?`, `expected_revision` | 201 `{mode,collection}`；同步解析，新独立 sample ID |
| POST `/collections/{id}/manual` | `{expected_revision,name,composition,energy_fields,energy_basis,unit:"eV",reference_note?:string}` | 201 `{mode,collection}` |
| GET `/csv-template` | 无 | UTF-8 BOM CSV 空表头模板，不含科学示例数值 |
| POST `/collections/{id}/csv/preview` | UTF-8 CSV 原始字节；query `expected_revision`, `energy_basis?` | `{mode,preview}`；逐行值与问题、数量及 `can_import`，不写入 |
| POST `/collections/{id}/csv` | 与预览相同 | 201 `{mode,collection}`；使用同一解析器重新整批验证后原子纳入 |
| GET `/collections/{id}/samples.csv` | query `energy_basis`、`value_source=original\|effective`（默认 effective） | 可回导样本表；读已保存样本，不要求计算结果；缺所选字段明确报错，无回退 |
| PUT `/collections/{id}/configuration` | `Configuration`（见下） | `{mode,collection}`；整体保存用户确认表，清除旧结果 |
| POST `/collections/{id}/lock` | `{expected_revision}` | `{mode,collection}`；完整检查参考、目标、口径、事实确认及显式风险接受后锁定 |
| POST `/collections/{id}/unlock` | `{expected_revision}` | `{mode,collection}`；解除编辑锁，保留仍有效的核对及风险接受 |
| POST `/collections/{id}/copy` | `{expected_revision,analysis_kind,title?,group_id?}` | 201 `{mode,collection}`；旧记录逐组显式复制，新 ID，确认／锁定／结果重置，原件保留；多组必须指定 `group_id` |
| POST `/collections/{id}/autofill` | `{expected_revision}` | `{mode,collection}`；批次结束后应用有限规则候选，返回角色／参考来源及歧义，不确认事实或接受风险 |
| POST `/collections/{id}/samples/removal-preview` | `{expected_revision,sample_ids?:string[],clear_all?:boolean}` | `{mode,removal}`；校验选择并预览样本数量及受影响参考／目标，无删除 |
| POST `/collections/{id}/samples/remove` | 与删除预览相同 | `{mode,collection,removal}`；同修订原子移除样本关联，不删除外部原文件；结果过期 |
| POST `/collections/{id}/calculate` | `{expected_revision}` | `{mode,collection}`；计算并持久保存，硬错400/409且没有新结果 |
| GET `/collections/{id}/export?format=json\|csv` | 无 | 附件；包含原始样本、覆盖值、组、公式明细和警告；只有最新有效结果可导出 |
| POST `/task-sources/preview` | `{project_id,task_id,job_key,attempt_id}` | `{mode,preview:{id,source,files:[{name,available,size_bytes,reason?}],expires_at,warnings}}` |
| POST `/collections/{id}/task-sources/import` | `{expected_revision,preview_id,name?:string}` | 201 `{mode,collection}`；仅取完整稳定 OUTCAR，新样本不可变缓存 |
| POST `/collections/{id}/reuse` | `{expected_revision,source_collection_id,sample_id}` | 201 `{mode,collection}`；核验完整缓存后离线复制，新ID保留原身份；不访问SSH |

上传逐份执行；同一 collection 最多100样本，64 MiB/文件、128 MiB/collection原始文件、能量存储总1 GiB。批量多份同名 OUTCAR 每份独立 UUID；中文空格相对路径是展示元数据，不能用作落盘路径。上传不替换旧来源；修改来源用新样本，再修改组引用。

复测修复扩展：Collection 增加 `analysis_kind`（旧混合记录可为 null）、`legacy_mode`、`locked`、`lock_fingerprint`；样本增加独立的 `warning_acceptance_fingerprint`。锁定检查在服务端，所有导入及配置修改在同一修订边界拒绝锁定记录；新类型分析计算／结果导出必须具备当前有效锁。解锁不清空所有确认，真实变更按样本及参考依赖失效；风险接受绑定来源快照及状态提示，不随无关目标变动清空。旧记录先核对旧确认指纹再投影，读取本身不改写磁盘。旧多组在新界面完整只读展示，按组复制进入新流程。

角色分配扩展：样本 `role_origin`、`included_origin` 区分 `manual`／`auto`，`assignment_reasons` 给出依据；组的 `reference_origins` 保护人工参考选择、清空及计量。Collection 的 `assignment_report` 含 `state` 和具体 `issues`，新导入后报告失效，整批完成再调用 autofill。未知目录不臆造，名称只是线索；组成、有限宿主／参考线索及唯一正整数计量只能生成待核对候选，不证明参考态正确。新增证据重新评估旧自动候选，但保留人工指定和排除。删除单个参考留下受保护的空绑定；清空整表允许新批次重新识别。已移除样本的本分析 `.bin` 快照保留至整个分析删除，继续计入全局缓存配额。

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

修改能量、组成、来源、role、group的参考/系数/field使旧result清除，受影响确认按实际依赖失效。新类型分析中，原已确认数据随科学字段一起携带的旧 `confirmed:true` 不能直接续用：先保存编辑，再显式提交确认，最后锁定。风险接受绑定来源及风险内容，不因无关目标变动失效；锁定不会自动接受风险。旧客户端兼容模式保留同次显式确认的原合同，仍受来源及科学校验约束。每次有效写入revision递增，错误revision返回409 `ENERGY_REVISION_CONFLICT`。

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

CSV入口列：`name,relative_path,composition,energy_basis,energy_ev,unit,reference_note`（按表头识别，列顺序可变）。composition 支持 `Al:2 O:3` 或旧 JSON 字符串，例如 `"{""Al"":2,""O"":3}"`；不自动从名称推组成。空unit不默认为eV，空能量不补零。未选界面口径时按文件声明；指定口径只补空字段，已有不同声明逐行报错。预览与导入复用解析及校验，实际导入再次按当前修订原子检查。样本表另外包含取值方式、原始能量、人工修订及来源说明；回导把附带说明与原 `reference_note` 分开保存，去重但不截断，不递归拼入备注。来源详情可核对这些文件自述信息，但不继承源文件验证或确认。CSV导出用户文本防公式执行，合法负数能量仍保持数字；JSON严格有限值。

样本 CSV 可选附带列为 `value_source,original_energy_ev,override_note,source_note,csv_metadata,csv_text_encoding`。`source.csv_metadata` 保存 `source_notes`、`override_notes`、`original_energies` 和 `value_source`；原能量条目含 `energy_basis/energy_ev`。原 `reference_note` 和每条来源说明各最多6000字，人工修订说明每条最多2000字，附带 metadata JSON 总计最多32768字，超限整批拒绝。`csv_text_encoding=apostrophe-prefix` 表示导出采用可逆防公式文本前缀；回导恢复文本原文，再次导出仍应用防护。全部附注为文件自述，不代表应用已核验其历史。

任务专用状态允许已提交的 queued/running/completed/failed/not_converged/cancelled；仍校验当前attempt、executed提交action、draft、slurm_id、SSH目标绑定。传输前后身份和远端文件元数据稳定、完整字节SHA一致才发布本地缓存，源变化返回409。旧DOS/band仍要求终态，任务download_result语义不变。
