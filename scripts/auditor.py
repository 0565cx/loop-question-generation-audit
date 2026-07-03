#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审题人(GPT5.5)：对每道题同步做知识审核和语言审核。

关键约束：审核规则必须逐字来自 references 下两个材料，脚本不改写、不缩写、不扩展，
只把材料原文整段注入系统提示词。
- 知识审核规则：references/knowledge-audit-prompt.txt
- 语言审核规则：references/language-audit-rules.md
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict

import config as C
import io_utils as IO
import llm_client as LLM


@lru_cache(maxsize=1)
def _knowledge_rules() -> str:
    return C.KNOWLEDGE_AUDIT_PROMPT_FILE.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _language_rules() -> str:
    return C.LANGUAGE_AUDIT_RULES_FILE.read_text(encoding="utf-8")


@dataclass
class AuditResult:
    # 知识审核
    knowledge_delete: bool = False
    knowledge_error_types: str = ""
    knowledge_reason: str = ""
    # 语言审核
    language_result: str = ""  # 不变 / 修改 / 重出
    language_problem_types: str = ""
    modified_question: str = ""
    # 汇总（语言“修改”和“重出”都并入“重出”）
    final_mark: str = ""  # 通过 / 重出
    passed: bool = False
    raw: str = ""
    error: str = ""


def _knowledge_audit(client: Any, model: str, question: str, a1: str, a2: str, a3: str, job: str) -> Dict[str, Any]:
    rules = _knowledge_rules().replace("{{岗位名称}}", job or "该岗位")
    payload = {"问题": question, "参考答案第一层": a1, "参考答案第二层": a2, "参考答案第三层": a3}
    messages = [
        {"role": "system", "content": rules},
        {"role": "user", "content": (
            "请严格按上述审核规则审核下面这道题，只输出 JSON："
            '{"是否删除": "是/否", "错误类型": "字母，如A1/B2，可多个", "删除原因": "具体错处+正确应为", "疑似与肯定": "肯定无/疑似无/疑似有/肯定有"}'
            "\n\n待审题目：\n"
            + IO_dump(payload)
        )},
    ]
    raw = LLM.chat_json(client, model, messages, temperature=0.1)
    return LLM.parse_json(raw)


def _language_audit(client: Any, model: str, item_fields: Dict[str, str], question: str) -> Dict[str, Any]:
    rules = _language_rules()
    ctx = {k: item_fields.get(k, "") for k in ["难度", "行业", "岗位名称", "岗位编码", "技能分类", "技能", "知识点"]}
    messages = [
        {"role": "system", "content": rules},
        {"role": "user", "content": (
            "请严格按上述语言审核规则审核下面这道题的题干（只看题干，不看参考答案），只输出 JSON："
            '{"处理结果": "不变/修改/重出", "修改后的问题": "仅当处理结果为修改时填写完整题干，否则留空", "问题类型": "命中的问题类型词，多个用分号"}'
            "\n\n题干及允许读取字段：\n"
            + IO_dump({"问题": question, **ctx})
        )},
    ]
    raw = LLM.chat_json(client, model, messages, temperature=0.1)
    return LLM.parse_json(raw)


def IO_dump(obj: Dict[str, Any]) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


def audit_one(client: Any, model: str, item_fields: Dict[str, str],
              question: str, a1: str, a2: str, a3: str) -> AuditResult:
    res = AuditResult()
    job = item_fields.get("岗位名称", "")
    try:
        k = _knowledge_audit(client, model, question, a1, a2, a3, job)
        res.knowledge_delete = IO.clean_cell(k.get("是否删除")) == "是"
        res.knowledge_error_types = IO.clean_cell(k.get("错误类型"))
        res.knowledge_reason = IO.clean_cell(k.get("删除原因"))

        lang = _language_audit(client, model, item_fields, question)
        res.language_result = IO.clean_cell(lang.get("处理结果"))
        res.language_problem_types = IO.clean_cell(lang.get("问题类型"))
        res.modified_question = IO.clean_cell(lang.get("修改后的问题"))

        # 汇总口径：知识审核判删除 => 重出；语言“修改”或“重出” => 都标记为重出
        language_regen = res.language_result in C.LANGUAGE_REGEN_RESULTS
        if res.knowledge_delete or language_regen:
            res.final_mark = "重出"
            res.passed = False
        else:
            res.final_mark = "通过"
            res.passed = True
    except Exception as exc:  # noqa: BLE001
        res.error = str(exc)
        res.final_mark = "重出"
        res.passed = False
    return res
