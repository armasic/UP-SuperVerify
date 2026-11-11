# up-superverify

数字 IC 产品验证技能：从需求提取计划，实现测试，执行 EDA，修复 RTL 或测试缺陷并回归，交付正式报告和完整证据。首版覆盖功能、协议、吞吐和延迟。

## 使用

```text
使用 $up-superverify，读取需求与当前 RTL，先给验证计划，不执行仿真。
使用 $up-superverify，复核这批 PASS 是否足以支持产品验收，列出证据缺口。
使用 $up-superverify，按已确认计划执行到最终交付，修复原规格内缺陷并回归。
使用 $up-superverify，先做到测试环境完成，报告检查器自检与剩余工作。
```

已有 LRS、模型、测试及 SuperDesign 交付可复用；缺项按影响管理。技能会给出阶段及完成条件，用户可以选择全部或指定终点。阶段内持续推进；需要改变产品语义或指标时再决策。实际工程放目标项目，本仓库只维护技能和有界自检。

**全部已有用例通过，不等于产品验收通过。** 必验需求缺少有效检查、结果无效或证据属于旧版本时，验收保持未完成。

## 结构与依赖

入口 `SKILL.md`，内部参考位于 `references/`。`scripts/check_evidence.py` 检查计划/运行数据与当前输入身份；`scripts/render_report.py` 生成简洁正文和完整明细。精确格式见 [数据契约](references/data-contract.md)，交付写法见 [正式报告](references/reporting.md)。

核心脚本使用 Python 3.9+ 标准库；Word 导出需要 `python-docx`，PDF 需要可用渲染工具。技能本身不依赖 Superpowers、vplan-generator 或固定 UVM 框架。EDA 环境按项目配置，适用时使用独立的 up-duiker-nic 技能。

正式报告正文集中结论、环境、结果、缺陷回归、覆盖限制；附件保存具体用例、检查点和复现材料。结构检查不能证明来源无遗漏、自然语言预期正确或 RTL 没有未发现的缺陷。

## 本机安装

将仓库根作为名为 up-superverify 的技能包安装，或让宿主的技能发现目录指向仓库根。创建入口前检查同名位置，不覆盖已有技能。入口存在与宿主已发现是不同检查，必要时刷新会话。安装不授予自动执行 EDA 或修改项目的权限，实际按调用范围处理。

## 自检

```bash
python3 tests/check_package.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

行为场景见 [scenarios.md](tests/behavior/scenarios.md)，报告见 [behavior.md](tests/reports/behavior.md)。RTL 自检包含寄存器控制和握手数据通路，规范与输入放 `tests/fixtures/`，实际运行结果见 [rtl-fixtures.md](tests/reports/rtl-fixtures.md)。这些是有界技能验证，不代表任意产品已验收。

包、证据工具及报告导出的检查结果见 [package.md](tests/reports/package.md)。

## 维护与发布

开发分支保留过程，main 只接收用户确认的发布提交。首次发布以验收快照建立独立根提交，后续压缩提交；本地 docs、.local、.runs 不发布。推送、标签和正式发布需当次授权。原始用户附件、私有工程、库与运行秘密不随包分发。
