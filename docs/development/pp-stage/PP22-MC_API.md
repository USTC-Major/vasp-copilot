# PP22-MC 多计算卡 API 合同

日期：2026-10-11。实施合同；路由位于 `/api/v1/toolbox/postprocessing/energy`。沿用集合、样本和 `groups[].id`，`groups` 在此工作流表示计算卡。公式、OUTCAR、CSV、任务缓存入口继续复用。

## 数据

创建：`POST /collections`，`{title, analysis_kind: "adsorption"|"formation", workflow:"cards"}`。新记录 `schema_version:"pp.energy.v2"`、`workflow:"cards"`；分析类型不可改变。所有写请求有严格整数 `expected_revision`；冲突409，任何验证失败不部分保存。

集合响应仍是 `{mode:"toolbox",collection}`；`samples` 和原始来源字段保持现有格式。样本的旧 `role/included/confirmed/accepted_warnings` 仅兼容字段，不限制卡片用途，也不参与新卡计算确认。

`groups` 每卡沿用既有 Group 配置：`id,name,kind,energy_basis,basis_confirmed,clean_sample_id,adsorbate_sample_id,reference_units,element_references,targets,reference_note,reference_origins`。吸附目标 `{sample_id,adsorbate_count}`；形成目标 `{sample_id}`（兼容默认数量1）。同一份分析只容许同类型卡。

新增及保存允许参考/目标尚未填写的草稿。吸附参考的空字符串与null规范化为null，元素参考中空字符串条目视作尚未指定并省略；缺必需参考/目标在卡确认锁定时返回对应字段错误。未知非空样本ID及重复目标仍在草稿保存阶段拒绝。

每卡另有 `locked:boolean, confirmed:boolean, confirmation_fingerprint:string|null, risk_accepted:boolean, risk_acceptance_fingerprint:string|null, result:EnergyResult|null, status:"draft"|"locked"|"result"|"stale"`，及实际引用来源的 `risks:[{sample_id,name,warnings:string[]}]`。结果沿用 `pp.energy.result.v1` 格式且只含该卡；卡片、实际引用样本科学输入和风险组成指纹，名称不参与；`reference_note` 表达参考态/单元定义，属于科学依据。解锁保留有效确认和结果；修改科学配置清除该卡确认/风险/锁/结果。未引用样本和其他卡修改不影响它。

## 卡片路由

以下响应均返回完整 collection；新增/复制201，其余200。

| 路由 | 请求体 | 语义 |
|---|---|---|
| `POST /collections/{id}/cards` | `{expected_revision,card:Group}` | 新增，客户端生成稳定合法Group ID；只接收配置，不接收状态 |
| `PUT /collections/{id}/cards/{card_id}` | `{expected_revision,card:Group}` | 只保存指定卡配置；ID必须相符；锁定卡科学修改409，纯重命名允许 |
| `POST /collections/{id}/cards/{card_id}/copy` | `{expected_revision,name?:string}` | 新ID，配置复制，清空确认、风险、锁和结果 |
| `DELETE /collections/{id}/cards/{card_id}` | `{expected_revision}` | 删除卡，保留全部样本 |
| `POST /collections/{id}/cards/{card_id}/autofill` | `{expected_revision}` | 仅当前卡规则填入；保留人工引用/数量/目标，不自动追加目标到已有人工卡 |
| `POST /collections/{id}/cards/{card_id}/lock` | `{expected_revision,accepted_warnings:boolean}` | 集中确认并锁定；当前引用样本有风险时必须显式true，硬错误始终拒绝 |
| `POST /collections/{id}/cards/{card_id}/unlock` | `{expected_revision}` | 只解锁当前卡，保留有效依据和结果 |
| `POST /collections/{id}/cards/{card_id}/calculate` | `{expected_revision}` | 只计算当前卡；需要有效卡锁，与其他卡是否就绪无关 |
| `GET /collections/{id}/cards/{card_id}/export?format=json|csv` | 无 | 导出当前有效卡结果及实际引用来源；失效结果409；解锁后有效结果可导出 |

## 共享样本修改和影响确认

样本补丁只允许 `{sample_id,name?:string,override?:Override|null}`，字段省略表示保留。源文件和解析值不可借此修改。添加来源继续用现有manual/OUTCAR/CSV/task/reuse接口，不重置已有卡。

`POST /collections/{id}/samples/change-preview` 请求 `{expected_revision,samples:[补丁],title?:string}`，响应 `{mode:"toolbox",impact}`。标题省略时保留；标题修改不产生科学影响。

`PUT /collections/{id}/samples/configuration` 请求 `{expected_revision,samples:[相同补丁],title?:string,preview_id?:string,acknowledge_locked_cards?:boolean}`。科学修改影响已锁定卡时，必须带该预览返回的token和显式true；token绑定修订、完整补丁和影响集合。纯名称修改不使卡失效。保存科学修改使实际依赖卡进入stale并清除确认/锁/结果，无关卡保持原样。

`impact = {preview_id,expected_revision,sample_ids,affected_cards:[{card_id,name,locked}],requires_acknowledgement}`。`affected_cards` 包含全部受影响卡，且 `locked` 标明保护对象。预览不写磁盘；取消或冲突无副作用。

现有 `POST /samples/removal-preview` 的请求仍为 `{expected_revision,sample_ids}` 或 `{expected_revision,clear_all:true}`；新UI追加 `workflow:"cards"`，以显式选择旧记录迁移语义（v2可省略）。返回原 `removal` 并增加 `removal.impact`。`POST /samples/remove` 追加可选 `preview_id,acknowledge_locked_cards,workflow:"cards"`；有锁定依赖时执行相同保护。删除不改源文件/任务来源，清除对应卡引用并使依赖卡stale。

## 错误定位

错误仍为 `{error:{code,message,retryable,field_errors:[{card_id:string|null,sample_id:string|null,field:string,code:string,message:string}]}}`。

路径相对于卡：`targets.{sample_id}.adsorbate_count`、`reference_units`、`clean_sample_id`、`adsorbate_sample_id`、`element_references.{element}`、`energy_basis`、`accepted_warnings`、`targets`；样本补丁用 `samples.{sample_id}.override.composition` 等。全局字段 `expected_revision`、`preview_id`、`card`。未知/全局错误不得映射到数量输入。Pydantic422也提供同结构，尽量包含路由card ID和请求target/sample ID。

稳定错误码沿用科学码，并新增 `ENERGY_CARD_NOT_FOUND`、`ENERGY_CARD_LOCKED`、`ENERGY_CARD_LOCK_REQUIRED`、`ENERGY_CARD_CONFIRMATION_STALE`、`ENERGY_IMPACT_ACK_REQUIRED`、`ENERGY_IMPACT_PREVIEW_INVALID`、`ENERGY_LEGACY_READ_ONLY`、`ENERGY_CARD_WORKFLOW_REQUIRED`。

## 旧记录

旧v1读取不写磁盘，原groups保持完整；附加 `card_migration:{required:true,read_only:boolean,reason:string|null}` 和 `card_projection:[卡配置及状态]`。同类型旧多组逐组投影；状态仅在旧确认/风险/结果可靠验证等价时保留，否则stale要求重确认。首次新卡/共享修改写入时，将原始metadata逐字节保存为 `metadata.pp.energy.v1.backup.json`，再原子保存v2。混合类型记录read_only，任何新编辑拒绝，原始读取/样本导出保留。

显式复制 `POST /collections/{id}/copy`：旧参数 `{expected_revision,analysis_kind,title?,group_id?}` 新增 `workflow:"cards"`。指定类型且未指定group_id时复制该类型所有卡和实际引用样本到独立v2分析；复制清空确认/锁/结果，不覆盖原件。

旧记录完全没有比较组时，显式指定类型的cards复制保留全部未绑定样本池，生成空卡列表，不猜测参考或用途。未指定workflow的旧复制行为保留。

旧未指定workflow的调用继续v1兼容路径；v2记录拒绝旧全局configuration/lock/unlock/calculate/autofill/result export写法，防止绕过卡保护。samples CSV和来源导入保留。
