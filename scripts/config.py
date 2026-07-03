#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""循环出题-审题工作流的固定配置。

角色分工（固定，不要改动语义）：
- 编排人：本套脚本。负责整理表格、字段映射、调度出题人、合并审题结果、循环控制、导出好题表。
- 出题人：qwen-plus（DashScope）。只按表格自带的出题/出答案提示词生成题目与三层答案。
- 审题人：GPT5.5。对每批题目同步做知识审核与语言审核，审核规则必须严格来自 references 下的两个材料。
"""

from __future__ import annotations

from pathlib import Path

# ---- 目录 ----
SKILL_DIR = Path(__file__).resolve().parent.parent
REFERENCES_DIR = SKILL_DIR / "references"

# 审核规则材料（逐字使用，禁止改写/缩写/扩展）
KNOWLEDGE_AUDIT_PROMPT_FILE = REFERENCES_DIR / "knowledge-audit-prompt.txt"
LANGUAGE_AUDIT_RULES_FILE = REFERENCES_DIR / "language-audit-rules.md"

# ---- 出题人（qwen-plus）----
GENERATOR_MODEL = "qwen-plus"
GENERATOR_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
GENERATOR_API_KEY_ENVS = ["DASHSCOPE_API_KEY", "QWEN_API_KEY"]

# ---- 审题人（GPT5.5）----
AUDITOR_MODEL_ENV = "AUDITOR_MODEL"
AUDITOR_MODEL_DEFAULT = "gpt-5.5"
AUDITOR_BASE_URL_ENVS = ["AUDITOR_BASE_URL", "OPENAI_BASE_URL"]
AUDITOR_BASE_URL_DEFAULT = "https://api.openai.com/v1"
AUDITOR_API_KEY_ENVS = ["AUDITOR_API_KEY", "OPENAI_API_KEY", "GPT_API_KEY"]

# ---- 输入表结构 ----
# 输入 = 上一轮被标记“重出”的题目表（sheet: 出题 / 出答案）
QUESTION_SHEET = "出题"
ANSWER_SHEET = "出答案"
PROMPT_COLUMN = "提示词"  # 出题 sheet 每行自带出题提示词；出答案 sheet 首行是出答案提示词

# 出题人需要的原始字段（来自输入表 出题 sheet）
FIELD_COLUMNS = [
    "行业", "岗位名称", "岗位编码", "技能分类", "技能",
    "知识点", "难度", "招聘场景", "JD",
]

# ---- 最终好题表结构（对齐 525提测/题目与知识点岗位的关联关系表）----
FINAL_COLUMNS = [
    "问题", "参考答案-第一层", "参考答案-第二层", "参考答案-第三层",
    "难度", "建议作答时间", "行业", "岗位名称", "岗位编码",
    "技能分类", "技能", "知识点", "场景", "2.0题目和参考答案",
]

# 输入表用“招聘场景”，最终表用“场景”，此处做名称对齐
SCENE_INPUT_COLUMN = "招聘场景"
SCENE_OUTPUT_COLUMN = "场景"

# ---- 提示词占位符 ----
# 出题提示词占位符 -> 字段来源
QUESTION_PLACEHOLDERS = {
    "{{industry}}": "行业",
    "{{job}}": "岗位名称",
    "{{category}}": "技能分类",
    "{{skill}}": "技能",
    "{{knowledge}}": "知识点",
    "{{scene}}": "招聘场景",
    "{{level}}": "难度",
    "{{JD}}": "JD",
    "{{num}}": "__num__",  # 固定为 1
}
# 出答案提示词额外占位符：{{Q}} = 新题干；{{{industry}} 是源文件笔误，一并兼容
ANSWER_EXTRA_PLACEHOLDERS = {
    "{{Q}}": "__question__",
    "{{{industry}}": "行业",
}

# ---- 审题结果标记口径 ----
# 语言审核处理结果为“修改”和“重出”，在最终题目标记中都记为“重出”。
LANGUAGE_REGEN_RESULTS = {"修改", "重出"}
LANGUAGE_PASS_RESULT = "不变"

# 建议作答时间与难度的兜底映射
DURATION_BY_DIFFICULTY = {"简单": 60, "中等": 90, "困难": 120}

# ---- 断点续跑 ----
# 输入表若带此列则作为跨轮稳定的题目唯一键；否则用 出题 sheet 的行号。
SLOT_ID_COLUMN = "任务ID"
CHECKPOINT_FILENAME = "checkpoint.xlsx"
CHECKPOINT_SHEET = "进度"
# checkpoint 每行 = 某道题在某一轮的一次出题+审题结果
CHECKPOINT_COLUMNS = [
    "任务ID", "轮次", "状态",  # 状态：通过 / 重出 / 出题失败
    "问题", "参考答案-第一层", "参考答案-第二层", "参考答案-第三层",
    "建议作答时间", "岗位名称", "知识点", "审核说明", "语言问题类型",
]
CHECKPOINT_STATUS_PASS = "通过"
CHECKPOINT_STATUS_REGEN = "重出"
CHECKPOINT_STATUS_GEN_FAIL = "出题失败"
