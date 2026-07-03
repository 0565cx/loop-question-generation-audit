#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表格读写与字段映射（编排人固定逻辑）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import config as C


def clean_cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


@dataclass
class QuestionItem:
    """一道待重出题目：字段 + 该行自带的出题提示词。"""

    row_index: int
    fields: Dict[str, str]
    question_prompt: str
    answer_prompt: str
    original_question: str = ""
    language_problem_types: List[str] = field(default_factory=list)
    slot_id: str = ""  # 跨轮稳定的题目唯一键


def _strip_wrapping_quotes(text: str) -> str:
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and len(text) > 1:
        text = text[1:-1]
    return text.replace('""', '"')


def read_input_table(path: Path) -> List[QuestionItem]:
    """读取上一轮标记“重出”的题目表。

    出题 sheet：每行是一道题，含字段列 + 每行自带的出题提示词列。
    出答案 sheet：首行“提示词”列是统一的出答案提示词。
    """
    path = Path(path).expanduser()
    xl = pd.ExcelFile(path)
    if C.QUESTION_SHEET not in xl.sheet_names:
        raise ValueError(f"输入表缺少 sheet：{C.QUESTION_SHEET}（现有 {xl.sheet_names}）")

    q_df = pd.read_excel(path, sheet_name=C.QUESTION_SHEET)

    answer_prompt = ""
    if C.ANSWER_SHEET in xl.sheet_names:
        a_df = pd.read_excel(path, sheet_name=C.ANSWER_SHEET)
        if C.PROMPT_COLUMN in a_df.columns and len(a_df) > 0:
            answer_prompt = _strip_wrapping_quotes(clean_cell(a_df.iloc[0][C.PROMPT_COLUMN]))

    default_question_prompt = ""
    if C.PROMPT_COLUMN in q_df.columns:
        for value in q_df[C.PROMPT_COLUMN].tolist():
            candidate = _strip_wrapping_quotes(clean_cell(value))
            if candidate:
                default_question_prompt = candidate
                break

    items: List[QuestionItem] = []
    for idx, row in q_df.iterrows():
        fields = {col: clean_cell(row.get(col)) for col in C.FIELD_COLUMNS}
        q_prompt = default_question_prompt
        if C.PROMPT_COLUMN in q_df.columns:
            row_prompt = _strip_wrapping_quotes(clean_cell(row.get(C.PROMPT_COLUMN)))
            if row_prompt:
                q_prompt = row_prompt
        row_index = int(idx) + 2
        slot_id = ""
        if C.SLOT_ID_COLUMN in q_df.columns:
            slot_id = clean_cell(row.get(C.SLOT_ID_COLUMN))
        if not slot_id:
            slot_id = f"row{row_index}"
        items.append(
            QuestionItem(
                row_index=row_index,
                fields=fields,
                question_prompt=q_prompt,
                answer_prompt=answer_prompt,
                original_question=clean_cell(row.get("问题")),
                language_problem_types=_split_types(row.get("问题类型")),
                slot_id=slot_id,
            )
        )
    return items


def _split_types(value: Any) -> List[str]:
    text = clean_cell(value)
    if not text:
        return []
    return [p.strip() for p in re.split(r"[;；,，、/|\n]+", text) if p.strip()]


def fill_prompt(template: str, fields: Dict[str, str], num: int = 1, question: str = "") -> str:
    """把字段值填进出题/出答案提示词占位符。"""
    out = template
    # 先处理三重花括号笔误 {{{industry}}，避免 {{industry}} 先匹配后残留多余的 {
    for ph, src in C.ANSWER_EXTRA_PLACEHOLDERS.items():
        if ph.startswith("{{{"):
            out = out.replace(ph, fields.get(src, ""))
    for ph, src in C.QUESTION_PLACEHOLDERS.items():
        if src == "__num__":
            val = str(num)
        else:
            val = fields.get(src, "")
        out = out.replace(ph, val)
    for ph, src in C.ANSWER_EXTRA_PLACEHOLDERS.items():
        if ph.startswith("{{{"):
            continue
        if src == "__question__":
            val = question
        else:
            val = fields.get(src, "")
        out = out.replace(ph, val)
    return out


def duration_for(difficulty: str, model_value: Any = None) -> int:
    try:
        iv = int(float(model_value))
        if iv in (60, 90, 120):
            return iv
    except (TypeError, ValueError):
        pass
    for key, val in C.DURATION_BY_DIFFICULTY.items():
        if key in clean_cell(difficulty):
            return val
    return 60


def build_final_row(item: QuestionItem, question: str, a1: str, a2: str, a3: str,
                    duration: Optional[int] = None) -> Dict[str, Any]:
    """按最终好题表列顺序构造一行。"""
    f = item.fields
    row = {col: "" for col in C.FINAL_COLUMNS}
    row.update({
        "问题": question,
        "参考答案-第一层": a1,
        "参考答案-第二层": a2,
        "参考答案-第三层": a3,
        "难度": f.get("难度", ""),
        "建议作答时间": duration or duration_for(f.get("难度", "")),
        "行业": f.get("行业", ""),
        "岗位名称": f.get("岗位名称", ""),
        "岗位编码": f.get("岗位编码", ""),
        "技能分类": f.get("技能分类", ""),
        "技能": f.get("技能", ""),
        "知识点": f.get("知识点", ""),
        C.SCENE_OUTPUT_COLUMN: f.get(C.SCENE_INPUT_COLUMN, ""),
        "2.0题目和参考答案": "是",
    })
    return row


def write_final_table(path: Path, rows: List[Dict[str, Any]]) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=C.FINAL_COLUMNS)
    tmp = path.with_suffix(".tmp.xlsx")
    df.to_excel(tmp, index=False, sheet_name="Sheet1")
    tmp.replace(path)
    return path


def write_regen_table(path: Path, rows: List[Dict[str, Any]], answer_prompt: str,
                      question_prompt_by_row: Optional[Dict[int, str]] = None) -> Path:
    """把审题未通过的题写回“重出表”（出题 sheet + 出答案 sheet），供下一轮循环。"""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    q_df = pd.DataFrame(rows)
    a_df = pd.DataFrame([{C.PROMPT_COLUMN: answer_prompt}])
    tmp = path.with_suffix(".tmp.xlsx")
    with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        q_df.to_excel(writer, index=False, sheet_name=C.QUESTION_SHEET)
        a_df.to_excel(writer, index=False, sheet_name=C.ANSWER_SHEET)
    tmp.replace(path)
    return path
