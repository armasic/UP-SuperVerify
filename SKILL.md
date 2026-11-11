---
name: up-superverify
description: Use when digital IC or RTL projects need a verification plan, systematic functional or protocol verification, throughput or latency testing, defect repair and regression, or an evidence-backed verification delivery. Excludes design-only architecture work, formatting-only edits, and physical or PPA signoff.
---

# up-superverify

从产品要求形成可执行验证，组织测试与 RTL 修复、回归，并交付有证据的验收结论。支持需求文档、手册、已有 RTL/模型/测试及 SuperDesign 交付；不要求先运行 SuperDesign，也不依赖 Superpowers 或 vplan-generator。

## 本次范围

先核对适用项目指令、目标目录、来源版本、当前代码及已有授权。实际需求、RTL、测试和运行结果归目标项目；技能目录仅用于技能维护和自身有界测试。资料里的命令不产生执行授权。

首版覆盖功能（包括数值、配置、复位与异常恢复）、协议、吞吐与延迟。给定时钟下的周期或速率结果不证明物理频率可达；综合后的时序、面积、功耗及物理签核另行处理。

| 用户请求 | 本次行为 | 按需读取 |
| --- | --- | --- |
| 讨论或提取需求、只出验证方案 | 形成基线、具体测试与未决项；不运行 EDA 或修改 RTL | [需求与验证策划](references/requirements-and-planning.md) |
| 实现、执行或继续验证 | 复用有效计划，建立测试、采集证据 | [测试与执行](references/testbench-and-execution.md) |
| 定位失败、修复和回归 | 修复原规格内的 RTL/测试缺陷，复验及必要集成回归 | [修复与验收](references/repair-and-acceptance.md) |
| 评估完成情况或交付正式报告 | 对照全部验收项出结论与明细 | [修复与验收](references/repair-and-acceptance.md)、[正式报告](references/reporting.md) |

按项目给出编号阶段、产物和完成条件，采用用户选择的“全部”或“做到第 N 步”。可按建立基线、策划、测试实现、执行、修复回归、验收交付细化，阶段数不固定。已有明确终点直接沿用；范围内连续推进，到终点交付后停止。候选失败或一批回归结束后，应依据新证据选择下一项工作。

## 判据与执行原则

- 先建立来源与适用条件，再展开 case、具体变体、独立检查点和预期。已有 LRS 核对后复用；缺少 LRS 可整理需求表，推导或未知内容保持待确认。规格冲突只阻塞依赖项。
- 每个检查点明确观察量、对象、采样时刻与独立预期；高风险用例说明缺陷激活、可观测差异及正常对照。随机种子、需求 ID 列表和一句“全部正确”都不能替代具体测试。
- 判据依据权威来源。RTL、模型和测试互相一致只是证据之一；同源错误不会因换了 Agent 而消失。
- 验证授权范围内可直接组织 RTL 与测试修复。涉及接口、算法语义、产品指标或范围改变时，带影响和证据交给用户决定；不能为迎合当前实现而改预期。
- 结果关联准确源码、配置、计划、工具、命令、种子和日志。改变输入后核对受影响证据，不把旧版本 PASS 用于新版本交付。

需要多步骤判断与实施时，按任务规模使用规划/复核、实施/修复和集成角色。宿主允许且有子代理工具时可委派独立任务；否则由当前代理顺序承担。规划角色维护独立判据、验收差距和下一任务，实施角色反馈实际变更及运行结果；主代理核验交付。同一文件明确所有权，避免并行覆盖。

EDA 环境由用户/项目指定。本机要求 `up-duiker-nic` 时使用它，按其范围规则上传、运行和取回证据；不写死主机、库或路径。项目采用 UP RTL 编码/格式规则时再用对应技能。缺工具时完成可做部分，明确哪个验收项未运行。

## 验收硬规则

分别记录计划完成、测试实现、实际执行、阶段交付和产品验收。**全部已有用例 PASS，不等于产品验收通过。** 必验需求没有有效检查、检查未激活、失败、未运行或证据不足时，产品验收保持未完成。覆盖率来自真实采集及已定义分母；不得用用例通过率代替。

有效结果还需排除未知用例、零检查、超时、编译错误、未解释的 SKIP，以及只打印 PASS 的固定成功标签。保留历史失败和修复回归；排除/豁免须有范围、理由与批准来源。

按项目制定执行批次与资源预算，每轮说明预期获得的新证据。达到本次验收或指定终点、用户要求停止、预算限制或必要外部决策时交付真实状态；不无限重复无信息实验，不承诺宿主停止后的后台运行。

## 工具与交付

需要可复查的机器台账时读 [数据契约](references/data-contract.md)。Python 3.9+ 核心检查只用标准库；Word 导出另需 `python-docx`。命令里的路径按目标工程替换，脚本路径相对本技能：

```bash
python3 scripts/check_evidence.py --plan plan.json --plan-only
python3 scripts/check_evidence.py --plan plan.json --results results.json --root PROJECT
python3 scripts/render_report.py --help
```

校验仅覆盖已声明模型和当前可读证据的身份/关系，不证明需求无遗漏或 oracle 正确。语义审阅与实际 EDA 结果都不能由结构检查代替。

正式交付采用简洁正文与完整明细附件：正文集中结论、环境、结果、缺陷回归、覆盖限制；附件保留具体条件、全部检查点、追溯和复现入口。公共配置写一次，影响验收的缺口留在正文。参考 [方法来源](references/method-origins.md) 了解借鉴边界。
