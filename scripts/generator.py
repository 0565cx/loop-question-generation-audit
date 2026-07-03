#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出题人(qwen-plus)：按表格自带的出题/出答案提示词生成新题与三层答案。

编排人只把必要字段填进提示词占位符，出题规则完全来自表格自带提示词，不额外改写。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict

import config as C
import io_utils as IO
import llm_client as LLM
from io_utils import QuestionItem


@dataclass
class GenResult:
    ok: bool
    question: str = ""
    answer1: str = ""
    answer2: str = ""
    answer3: str = ""
    duration: int = 60
    error: str = ""


_JSON_HINT = (
    "\n\n请严格只输出 JSON，不要包裹 ```，格式为："
    '{"question": "题干", "answer1": "第一层", "answer2": "第二层", "answer3": "第三层", "time": 60}'
)


def _gen_question(client: Any, item: QuestionItem) -> str:
    if not item.question_prompt:
        raise RuntimeError(f"第 {item.row_index} 行缺少出题提示词")
    prompt = IO.fill_prompt(item.question_prompt, item.fields, num=1)
    avoid = "、".join(item.language_problem_types) if item.language_problem_types else "无"
    messages = [
        {"role": "system", "content": "你是资深面试出题人，只按给定提示词出题，严格输出 JSON。"},
        {"role": "user", "content": (
            f"{prompt}\n\n【需规避的上一轮问题类型】{avoid}"
            f"\n【原题仅供避重，不要照抄】{item.original_question}"
            f"{_JSON_HINT}"
        )},
    ]
    raw = LLM.chat_json(client, C.GENERATOR_MODEL, messages, temperature=0.7)
    data = LLM.parse_json(raw)
    q = IO.clean_cell(data.get("question") or data.get("问题"))
    if not q:
        # 兼容 questions 列表包装
        qs = data.get("questions")
        if isinstance(qs, list) and qs:
            q = IO.clean_cell(qs[0].get("question") or qs[0].get("问题"))
    if not q:
        raise RuntimeError("出题人未返回题干")
    return q


def _gen_answer(client: Any, item: QuestionItem, question: str) -> Dict[str, Any]:
    if not item.answer_prompt:
        raise RuntimeError("缺少出答案提示词（输入表 出答案 sheet）")
    prompt = IO.fill_prompt(item.answer_prompt, item.fields, question=question)
    messages = [
        {"role": "system", "content": "你是资深面试出答案人，只按给定提示词生成三层参考答案，严格输出 JSON。"},
        {"role": "user", "content": f"{prompt}{_JSON_HINT}"},
    ]
    raw = LLM.chat_json(client, C.GENERATOR_MODEL, messages, temperature=0.5)
    data = LLM.parse_json(raw)
    if "questions" in data and isinstance(data["questions"], list) and data["questions"]:
        data = data["questions"][0]
    return data


def generate_one(client: Any, item: QuestionItem) -> GenResult:
    try:
        question = _gen_question(client, item)
        adata = _gen_answer(client, item, question)
        a1 = IO.clean_cell(adata.get("answer1") or adata.get("参考答案-第一层"))
        a2 = IO.clean_cell(adata.get("answer2") or adata.get("参考答案-第二层"))
        a3 = IO.clean_cell(adata.get("answer3") or adata.get("参考答案-第三层"))
        if not (a1 and a2 and a3):
            return GenResult(ok=False, question=question, error="三层答案不完整")
        return GenResult(
            ok=True, question=question, answer1=a1, answer2=a2, answer3=a3,
            duration=IO.duration_for(item.fields.get("难度", "")),
        )
    except Exception as exc:  # noqa: BLE001
        return GenResult(ok=False, error=str(exc))
