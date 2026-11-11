# 计划与结果数据契约

本契约适用于 `schema_version: 1`，核心脚本要求 Python 3.9+ 标准库。Word 导出另需可选 `python-docx`；脚本不会自动安装依赖。

计划描述声明范围，结果记录执行事实。两者分开保存，不允许结果中的摘要替代逐检查点证据。字段名严格检查，未列出的字段会报错；`configuration`、`parameters` 是项目自定的有限 JSON 对象。所有 JSON 对象禁止重复键和非有限数值。非空字符串不能用空白占位。

## 状态与命令

```sh
python3 scripts/check_evidence.py --plan plan.json --results results.json --root PROJECT
python3 scripts/check_evidence.py --plan plan.json --plan-only
python3 scripts/render_report.py --plan plan.json --results results.json --root PROJECT --output report.md
python3 scripts/render_report.py --plan plan.json --results results.json --root PROJECT --output report.docx --details complete-tests.csv
```

提供 `--root` 时，计划和结果的相对路径以它为基准，绝对路径也必须位于它内部。证据路径全部使用根目录内的相对 POSIX 路径；禁止绝对路径、`..`、`.` 路径段、反斜杠及符号链接。输入根目录必须存在，执行过程中应保持稳定；文件核对不是并发文件系统的事务快照。报告输出路径以命令当前目录为基准，不能覆盖输入或日志，也不能放入声明的输入目录。

| 状态 | 退出码 | 含义 |
| --- | --- | --- |
| `ACCEPTED` | 0 | 声明范围内的验收条件满足，且当前文件与证据身份核对通过 |
| `INCOMPLETE` | 2 | 数据结构有效，但必验检查、运行、量化目标或其他验收条件未完成 |
| `INVALID` | 1 | 数据结构、引用、身份或文件核对错误 |
| `PLAN_VALID` | 0 | 仅 `--plan-only`：计划结构与已声明准备条件有效，未做产品验收 |

`--plan-only` 对未知预期、未实现检查、未建立输入基线及无实际检查的必验需求返回 `INCOMPLETE`。可在没有 RTL 时先建立计划：`inputs.roots`、`inputs.files` 均为空，CP 的 `implementation` 为 `null`。这种计划不能获得 `ACCEPTED`。结果中没有当前运行、没有检查，或者没有提供当前 `root`，同样不能通过。

API：`load_json(path)` 严格读 JSON；`validate_plan(plan) -> list[str]` 返回结构错误；`evaluate(plan, results, root=None) -> dict` 返回 `status`、`errors`、`blockers`、`summary`、`boundary`。汇总统计包含必验/排除需求数、计划/实际有效通过 CP 数、`passed_check_ids`（报告复用的有效 CP ID 集合）及当前运行数。未决预期、未实现、零检查或当前失败的 CP 不计入有效通过。报告调用同一 `evaluate`；`INVALID` 不写报告，`INCOMPLETE` 可写明确未完成的阶段报告并返回 2。

## 计划

顶层必需字段：`schema_version`、`project`、`inputs`、`configuration`、`requirements`、`cases`、`coverage_targets`、`metrics`。

### 输入身份

```json
{
  "inputs": {
    "roots": ["inputs"],
    "files": [{"path": "inputs/design.sv", "sha256": "64位小写SHA256"}]
  },
  "configuration": {"clock_mhz": 100, "mode": "normal"}
}
```

`roots` 声明需要完整核对的输入目录，目录之间不能重叠；`files` 必须精确列出这些目录下所有普通文件。扫描发现新增、缺失或变更文件都会使结果失效。将规格基线、DUT、测试/参考模型、配置与构建脚本等影响验收的输入纳入这些目录；哪些输入属于完整闭包仍需项目审阅。日志、报告与 plan/results 放在输入目录之外，避免自引用哈希。仅列出 DUT 而遗漏检查器或构建配置不能据此宣称输入闭包完整。

文件 SHA-256 按原始字节计算。计划、输入清单及配置身份按以下规范化 JSON 计算，**不对 plan.json 的排版字节求哈希**：

```python
encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
digest = hashlib.sha256(encoded).hexdigest()
```

对应 `value` 分别是整个 `plan`、`plan["inputs"]`、`plan["configuration"]`。改变计划中的任何字段都需要新的当前运行身份；不得事后给旧运行重绑定新身份。输入清单哈希不替代实际磁盘文件核对。

### 需求与检查

需求对象包含：

| 字段 | 内容 |
| --- | --- |
| `id` | 全局唯一稳定 ID |
| `description` | 要求验证的具体行为 |
| `source` | 权威材料的版本、条目或决策引用 |
| `required` | 布尔值，是否属于必验范围 |
| `exclusion` | 仅 `required: false` 时必需：`reason` 和 `authorization` 两个非空字符串 |

排除必须指出具体理由及用户/权威决策来源，不能用降低 `required` 跳过验收。工具检查授权引用存在，不能确认该授权真实或足以批准范围变化。

Case 必需 `id`、`description`、`requirements`（需求 ID 数组）、`variants`（非空）；可选 `isolation`、`high_risk`。每个 variant 必需：

- `id`、`parameters`：唯一 ID 和具体参数对象。
- `preconditions`、`stimulus`、`observe_at`：前置状态、具体事件顺序/对象/数值，以及观测时刻。
- `checks`：独立 CP 的非空数组，每个 CP 可单独失败。

每个 CP 必需 `id`、`description`、`requirements`、`expected`、`oracle`、`implementation`。

- `requirements` 必须是其 case 需求集合的非空子集。每个必验需求至少需要一个实际有效通过的 CP；**仅给 case 挂需求 ID 不构成需求覆盖**。同一需求下已计划的所有 CP 都必须完成。
- `expected` 为可判定预期；未决时用 `null`，或显式 `UNKNOWN`、`TBD`、`待确认`、`未知`。未决预期会阻断验收，脚本不能识别所有自然语言中的隐含不确定性。
- `oracle` 说明独立判据的来源及适用规则；不允许只由 DUT 当前输出反推预期。独立性与正确性由语义审阅确认。
- `implementation` 为 `inputs/checks.sv#check_name` 形式，其文件必须在输入清单中；尚未实现可为 `null`。脚本核对文件引用，不解析 HDL 符号或证明该函数确实执行。
- 可选 `receiver` 与 `role` 成对出现；`role` 只能是 `selected` 或 `unselected`。

计划中的需求、case、variant、CP、覆盖目标和性能目标 ID 全局唯一，使用 ASCII 字母、数字、`_`、`.`、`:`、`-`。所有引用必须存在，引用数组不能重复。

### 隔离与高风险检查

```json
{
  "isolation": {"selected": ["A"], "unselected": ["B"]},
  "high_risk": {
    "activation": "A定址事件；B预置为零",
    "activation_checks": ["CP-A"],
    "discriminating_checks": ["CP-B"],
    "normal_controls": ["VAR-NORMAL"]
  }
}
```

`isolation` 的两组接收者都非空且不相交。该 case 的**每个变体，包括正常对照**都必须为每个接收者分别建立相应角色的独立 CP。选中对象随参数变化时，拆为不同 case，不能沿用错误角色集合。

高风险项要求可复现的缺陷激活说明、实际激活检查引用、能够判别缺陷的 CP 和正常对照变体；三组引用均非空。激活与判别 CP 必须属于该 case，且不能只位于正常对照变体。正常对照必须属于该 case。激活 CP 可与判别 CP 重合，但需语义审阅其判据是否确实足以支持两种用途。

参数对象、刺激顺序、观测与独立 CP 可承载交互场景、寄存器访问/复位/生效/保持等方法。工具不替项目自动枚举交互，也不证明自然语言刺激足够具体。

### 覆盖与性能目标

`coverage_targets` 和 `metrics` 是数组，允许为空，不因空覆盖目标编造覆盖率。每个目标含 `id`、`description`、`unit`、`condition`、`op`、`target`；性能目标另含 `kind`，取 `throughput` 或 `latency`。

`op` 取 `>=`、`<=`、`==`、`>`、`<`，`target` 是有限数值。阈值、单位和计量口径由项目填写，未预设 100% 覆盖或固定性能门槛。`condition` 应包含负载、配置、时钟以及起止事件等适用条件；执行结果必须逐字匹配计划单位与条件，不做隐式单位换算。不同条件分别建立目标，禁止把一个条件下的平均值代表另一个条件。

## 结果

顶层必需 `schema_version`、`current_runs`、`runs`、`observations`、`coverage`、`measurements`。可选 `summary`（完全不采信用于判断）、`defects`、`limitations`（非空文本数组）。

### 运行

每个 run 必需：

| 字段 | 含义 |
| --- | --- |
| `id` | 唯一运行 ID |
| `plan_sha256`、`inputs_sha256`、`configuration_sha256` | 运行当时的计划、输入清单及配置身份 |
| `status` | `PASS`、`FAIL`、`BLOCKED`、`SKIP`、`NOT_RUN`、`ERROR` 或 `TIMEOUT` |
| `exit_code` | 实际进程整数退出码；当前有效运行必须为 0 |
| `checks`、`errors`、`timeouts` | 非负整数，来自实际执行记录 |
| `tool`、`command`、`seed` | 工具及版本、可复现命令、种子字符串；确定性测试可写 `deterministic` |
| `started_at`、`ended_at` | 带时区的 ISO 8601 时间，结束不早于开始 |
| `logs` | 非空 `{path, sha256}` 数组；编译和运行日志均应保留 |

`current_runs` 显式选择用于本次验收的运行 ID。每个当前运行都必须属于同一最终计划、输入清单和配置，并且 `status: PASS`、`exit_code: 0`、`checks > 0`、`errors: 0`、`timeouts: 0`。配置矩阵可以放在同一计划的 configuration/variant 参数中；不同计划或不同交付基线应使用独立结果包，不能合并 PASS。

历史失败 run 可留在 `runs`，但不放入 `current_runs`。它们保留旧身份、状态和日志，不要求与当前基线一致，也不永久阻断修复后的当前回归。所有保存的日志仍须存在且哈希一致；若历史证据单独归档，应在缺陷说明中引用归档，而不要保留悬空的 run。当前结果的 CP、量化实测与缺陷回归只能引用当前运行。若历史失败与当前计划/输入/配置身份完全相同，不能只凭后一次 PASS 忽略它：该历史 run 必须额外保存 `disposition: {reason, regression_runs}`，明确失败处置原因与当前有效回归 ID 数组，否则保持 `INCOMPLETE`。处置只适用于未选入 current_runs 的历史运行；原因是否充分、是否掩盖非确定性故障仍需语义审阅，并在正文披露。

### 实际检查与量化实测

每个 observation 必需：`check_id`、`variant_id`、`run_id`、`status`、`checks`、`actual`。

`check_id` 必须属于 `variant_id`，`run_id` 必须当前有效；同一 run/CP 只能有一条 observation，可用 `checks` 聚合该独立检查的多次采样。所有 CP 都必须有实际 `PASS`，且 `checks > 0`、`actual` 非空。`NOT_RUN`、`SKIP`、`BLOCKED`、`FAIL`、`ERROR`、`TIMEOUT` 或零检查均不能通过。每个当前 run 的 `checks` 必须等于其 observations 的计数总和；不能用额外未声明检查凑总数。多次当前运行中有失败不能被另一次 PASS 掩盖。

`coverage` 与 `measurements` 中每条记录含：

```json
{"target_id": "MET-RATE", "run_id": "RUN-1", "value": 1, "unit": "events/cycle", "condition": "single event; select=A"}
```

覆盖引用 `coverage_targets`，性能引用 `metrics`。同一 target/run 不得重复，必须引用当前有效运行，并匹配单位和条件。每个目标至少一条实测；保留的每条当前实测都必须达到阈值，不能挑选最高值掩盖另一条未达标结果。

工具不通用解析 VCS/UVM 或其他厂商日志。执行适配器或操作者须从真实执行中提取退出状态、有效检查计数、错误/超时、逐 CP 实际值及测量；禁止仅扫描最后一行 PASS。原始日志哈希建立可审阅的证据链，不能证明手工填写的结果为真。

### 缺陷

缺陷对象必需 `id`、`status`、`blocking`（布尔值）、`description`：

- `OPEN`：未关闭；`blocking: true` 阻止验收。非阻断缺陷仍需在报告披露。
- `FIXED`：额外必需 `regression_runs` 非空数组，只能引用当前有效运行；修复原问题与受影响回归的充分性仍由语义审阅确认。
- `WAIVED`：额外必需 `waiver: {reason, authorization}`；批准来源与排除项一样需要确认真实性。

不允许给 OPEN/WAIVED 混填 `regression_runs`，或给 OPEN/FIXED 混填 `waiver`。缺陷 ID 不能重复，也不能与 run ID 重复。

## 报告与完整附件

报告正文包含计算结论、验收缺口、环境配置、逐需求结果、覆盖/吞吐/延迟、缺陷修复回归及范围限制。状态来自 `evaluate`，不会采信 `results.summary`。Word 使用中文黑色标题与紧凑表格，正文不设固定页数。导出 Word 后仍须按文档流程渲染并逐页检查；导出成功不等于视觉质量检查通过。

完整 CSV 每个 CP/当前运行一行；未运行 CP 也保留一行 `NOT_RUN`。它保留需求映射及 `requirement_sources` 来源引用、case/变体 ID、参数、前置状态、具体刺激、观测时刻、预期、oracle、实现引用、接收者角色、激活/判别/对照、实际结果与日志路径，不裁去长文本。`run_metadata` 保存对应运行的完整记录，包括命令、种子、工具、起止时间、输入身份和日志哈希；未运行时为空对象。正文只汇总公共环境、基线和运行时间范围，不按每次运行重复这些栏目。正文与 CSV 会移除当前根路径及可识别的宿主绝对路径；相对复现路径和除法表达式保留。导出只去除可识别的绝对路径，不是任意敏感信息审查器，外发前仍按项目审核字段内容。

小型可复现数据包在 [`tests/fixtures/evidence/`](../tests/fixtures/evidence/plan.json) 与 [results.json](../tests/fixtures/evidence/results.json)。它是虚构的契约示例，日志与数值不代表真实 RTL 测量。可用它验证 CLI、错误拒绝与报告排版，不用它宣称实际产品验证完成。

自动校验仅对已声明范围和当前核对过的文件负责。没有发现结构错误，不证明来源没有遗漏、oracle 独立正确、激活/隔离语义充分、缺陷清单完整、排除真实获批或设计不存在未发现缺陷。这些是独立语义审阅与实际执行的责任。
