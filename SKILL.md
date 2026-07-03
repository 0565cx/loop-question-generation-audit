---
name: loop-question-generation-audit
description: 循环出题与审题工作流。编排人调度出题人(qwen-plus)按表格自带提示词出题、审题人(GPT5.5)对每批题同步做知识审核和语言审核，未通过的题标记“重出”后整理回流，循环往复直到全部题目通过知识审核和语言审核，最终导出好题表。Use when 用户提到 循环出题、出题审题闭环、重出题回流、批量出题、题目表格重出、知识审核加语言审核、qwen出题GPT审题、好题表导出。
disable-model-invocation: true
---

# 循环出题-审题工作流

## 业务目标

把“出题 → 审题 → 整理重出 → 再出题”这条闭环做成可反复运行的固定流程，直到一批题目全部通过知识审核和语言审核，最后产出一份好题表。

## 角色分工（固定）

- **编排人（你 + 本 skill 的脚本）**：整理输入表、做字段映射、把字段填进出题人提示词、调度出题人、合并两路审题结果、按“重出”口径分流、控制循环、导出好题表。编排逻辑固定在 `scripts/` 里。
- **出题人：qwen-plus**（DashScope）。只按输入表自带的出题/出答案提示词生成题目和三层参考答案，不额外改写规则。
- **审题人：GPT5.5**。对每道题**同步**做两类审核：知识审核 + 语言审核。审核规则**必须逐字来自** `references/` 下两个材料，脚本不改写、不缩写、不扩展。

## 审核规则来源（禁止改写）

- 知识审核（题目与答案的知识性问题）：`references/knowledge-audit-prompt.txt`（原文来自 `AI审题/AI审题流程与材料/审题提示词.txt`）。
- 语言审核（题干的语言问题）：`references/language-audit-rules.md`（原文来自 `github.com/0565cx/question-language-audit-skill`）。

脚本把这两份材料**整段原文注入**审题人的系统提示词，不做任何摘要或改动。如需更新规则，只替换 `references/` 下文件，不改脚本里的判定文字。

## 标记口径（关键）

- 知识审核判定“是否删除=是” → 该题**重出**。
- 语言审核处理结果为 **`修改`** 和 **`重出`** 的，最终题目标记**都记为“重出”**（`不变` 才算通过）。
- 一道题只要在知识审核或语言审核任一路被标记重出，就进入下一轮重出，不进入好题池。

## 输入表格式

触发本 skill 时，必须先确认用户本次要处理的输入表路径；如果用户没有明确提供表格路径，必须先询问，不得把下面示例路径当作默认输入自动开跑。

输入是上一轮语言审核后被标记“重出”的题目表，例如
`~/Desktop/北森-测评产品经理/AI面试官/专业能力/6月迭代/题目/语言审核/能源行业-自动化技术工程师-语言一审-重出.xlsx`。

两个 sheet：

- `出题`：每行一道题，含字段列 `问题 / 参考答案-第一层~第三层 / 难度 / 建议作答时间 / 行业 / 岗位名称 / 岗位编码 / 技能分类 / 技能 / 知识点 / 招聘场景 / 2.0题目和参考答案 / JD / 提示词`。其中 `提示词` 是出题提示词；若只有首行有提示词，脚本会把首个非空 `提示词` 作为默认出题提示词用于后续空行，`JD` 非必填。
- `出答案`：`提示词` 列首行是统一的**出答案提示词**。

出题人需要的字段：招聘场景、岗位、技能分类/技能/知识点、难度、JD（非必填）。编排脚本把这些字段填入提示词占位符：
`{{industry}} {{job}} {{category}} {{skill}} {{knowledge}} {{scene}} {{level}} {{JD}} {{num}}`（`{{num}}` 固定为 1），出答案提示词额外填 `{{Q}}`=新题干（并兼容源文件笔误 `{{{industry}}`）。

## 输出表格式

最终好题表列顺序对齐
`~/Desktop/北森-测评产品经理/AI面试官/专业能力/6月迭代/525提测/题目与知识点岗位的关联关系表_202605251636.xlsx`：

`问题 / 参考答案-第一层 / 参考答案-第二层 / 参考答案-第三层 / 难度 / 建议作答时间 / 行业 / 岗位名称 / 岗位编码 / 技能分类 / 技能 / 知识点 / 场景 / 2.0题目和参考答案`

注意输入表用 `招聘场景`，最终表用 `场景`，脚本自动对齐；`2.0题目和参考答案` 固定填 `是`。

## 循环流程

```
用户提交“重出”题目表
      │
      ▼
出题人(qwen-plus) 按每行自带提示词出新题 + 三层答案
      │
      ▼
审题人(GPT5.5) 对每题同步做 知识审核 + 语言审核
   ├─ 全部通过 ────────────► 进入好题池
   └─ 知识删除 或 语言(修改/重出) ─► 标记“重出”
      │
      ▼
编排人把重出题整理成新的“重出表”（出题/出答案 sheet，格式同输入）
      │
      ▼
用新的重出表回流，继续下一轮 …… 直到没有需要重出的题
      │
      ▼
导出最终好题表（列对齐 525提测 关联关系表）
```

## 运行步骤

### 模式 A：外部 API 审题人

1. 确认依赖与密钥：
   - `python3 -m pip install openai pandas openpyxl`
   - 出题人：环境变量 `DASHSCOPE_API_KEY`（或 `QWEN_API_KEY`）。
   - 审题人：`AUDITOR_API_KEY`（或 `OPENAI_API_KEY`）；如非官方端点再设 `AUDITOR_BASE_URL`；模型名默认 `gpt-5.5`，可用 `AUDITOR_MODEL` 覆盖。
2. 跑主脚本（编排人固定脚本）：

```bash
cd .cursor/skills/loop-question-generation-audit/scripts
python3 orchestrate.py \
  --input "/Users/risei/Desktop/北森-测评产品经理/AI面试官/专业能力/6月迭代/题目/语言审核/能源行业-自动化技术工程师-语言一审-重出.xlsx" \
  --out-dir "/Users/risei/Desktop/北森-测评产品经理/AI面试官/专业能力/6月迭代/循环产出" \
  --max-rounds 6 --workers 6
```

3. 产物：
   - 每轮一份 `重出表_第N轮_*.xlsx`（格式同输入，可单独复查或手动回流）。
   - 每轮滚动一份 `好题表_累计_*.xlsx`（防中断丢失）。
   - 收敛后一份 `题目与知识点岗位的关联关系表_*.xlsx`（最终好题表）。
   - 一份 `checkpoint.xlsx`（题级进度，用于断点续跑）。

### 模式 B：Cursor/子代理审题人（无需外部 GPT API Key）

当当前 Cursor 模型就是审题人，或希望发起子代理审题时，使用此模式：

1. 先用 qwen-plus 出题并生成待审批次：

```bash
python3 prepare_cursor_audit.py \
  --input "/Users/risei/Desktop/北森-测评产品经理/AI面试官/专业能力/6月迭代/题目/语言审核/能源行业-自动化技术工程师-语言一审-重出.xlsx" \
  --out-dir "/Users/risei/Desktop/北森-测评产品经理/AI面试官/专业能力/6月迭代/循环产出" \
  --round-no 1 --workers 6 --batch-size 20
```

2. 编排人读取 `audit_batches/batch_*.json`，由自己或多个子代理严格按 `references/knowledge-audit-prompt.txt` 与 `references/language-audit-rules.md` 审题，输出到 `audit_results/audit_batch_*.json`。

3. 回收审题结果，写 checkpoint、好题表和下一轮重出表：

```bash
python3 apply_cursor_audit.py \
  --input <本轮输入重出表.xlsx> \
  --generated <generated_for_cursor_audit_roundN_*.xlsx> \
  --audit-dir <out-dir>/audit_results \
  --out-dir <out-dir> \
  --round-no N
```

4. 如果产生下一轮重出表，再以该重出表作为 `--input` 重复 1～3，直到没有重出题。

## 断点续跑（题级）

脚本**每完成一道题（出题+审题）就立即写 `checkpoint.xlsx`**，即使一轮跑到一半中断，已消耗的模型调用也不会丢。

- 唯一键：输入表若有 `任务ID` 列则用它做跨轮稳定键；否则首轮用 `出题` sheet 的行号（如 `row2`）生成，并**自动写入每一份重出表的 `任务ID` 列**，因此从第 2 轮起跨轮键完全稳定，不受行序变化影响。checkpoint 以 `(任务ID, 轮次)` 记录每题每轮的一次结果（通过/重出/出题失败）。
- `--resume`：重启时跳过 checkpoint 中**本轮已完成**的题，只对未完成的题调用模型；已通过的题从 checkpoint 权威恢复进好题池，不重复出题。
- `--retry-failures`：只重跑所有轮次**未通过**（重出/出题失败）的题，已通过的题保留。适合改完提示词后只补跑失败题。
- `--checkpoint <path>`：自定义 checkpoint 路径，默认在 `--out-dir` 下的 `checkpoint.xlsx`。

续跑示例：

```bash
# 中断后接着跑
python3 orchestrate.py --input <重出表.xlsx> --out-dir <目录> --resume

# 只补跑失败题
python3 orchestrate.py --input <重出表.xlsx> --out-dir <目录> --retry-failures
```

注意：好题池始终从 checkpoint 恢复，所以续跑不会重复计入或遗漏已通过的题。

## 脚本结构

- `scripts/config.py`：所有固定配置（模型、字段列、占位符映射、标记口径、表列顺序）。
- `scripts/io_utils.py`：读输入表、字段→提示词映射、写好题表与重出表。
- `scripts/llm_client.py`：出题人/审题人客户端与 JSON 解析。
- `scripts/generator.py`：出题人调用（先出题再出三层答案）。
- `scripts/auditor.py`：审题人调用，整段注入两份审核规则原文，产出知识+语言双审结果并按口径汇总。
- `scripts/checkpoint.py`：题级断点续跑存储，逐题落盘、resume 跳过、retry-failures 重跑、好题池恢复。
- `scripts/orchestrate.py`：外部 API 审题模式的循环主控。
- `scripts/prepare_cursor_audit.py`：Cursor/子代理审题模式下，先用 qwen-plus 生成新题并拆分待审 JSON 批次。
- `scripts/apply_cursor_audit.py`：Cursor/子代理审题模式下，回收 `audit_results/audit_batch_*.json`，合并 checkpoint、好题表和下一轮重出表。

## 注意事项

- 审核规则只在 `references/` 维护，脚本不内联判定文字，保证“严格按材料、不改写”。
- `--max-rounds` 是防死循环上限；到上限仍有题未通过时，脚本会保留最后一份重出表并提示人工介入。
- 出题人只拿到必要字段和该行提示词，不改动出题规则本身。
