#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回收 Cursor/子代理审题结果，写 checkpoint、好题表和下一轮重出表。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

import config as C
import io_utils as IO
from checkpoint import Checkpoint


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_generated(path: Path) -> Dict[str, Dict[str, Any]]:
    df = pd.read_excel(path).fillna("")
    return {str(r["任务ID"]): r for r in df.to_dict("records")}


def _flatten_audit_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("items", [])
    if not isinstance(data, list):
        return []
    flattened: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, list):
            flattened.extend(x for x in item if isinstance(x, dict))
        elif isinstance(item, dict):
            flattened.append(item)
    return flattened


def _normalize_audit_item(item: Dict[str, Any]) -> Dict[str, Any]:
    slot = str(item.get("任务ID") or item.get("slot_id") or "").strip()
    knowledge_decision = str(item.get("knowledge_decision", "")).strip()
    language_decision = str(item.get("language_decision", "")).strip()
    final_decision = str(item.get("final_decision", "")).strip()

    normalized = dict(item)
    if slot:
        normalized["任务ID"] = slot
    if knowledge_decision and "知识审核是否删除" not in normalized:
        normalized["知识审核是否删除"] = "是" if knowledge_decision == "删除" else "否"
    if knowledge_decision and "知识审核错误类型" not in normalized:
        normalized["知识审核错误类型"] = knowledge_decision
    if language_decision and "语言审核处理结果" not in normalized:
        normalized["语言审核处理结果"] = language_decision
    if language_decision and "语言审核问题类型" not in normalized:
        normalized["语言审核问题类型"] = language_decision
    if final_decision and "最终标记" not in normalized:
        normalized["最终标记"] = final_decision

    reasons = [
        str(item.get("knowledge_reason", "")).strip(),
        str(item.get("language_reason", "")).strip(),
        str(item.get("final_reason", "")).strip(),
    ]
    if "审核说明" not in normalized:
        normalized["审核说明"] = "；".join([r for r in reasons if r])
    return normalized


def load_audit_results(audit_dir: Path) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for path in sorted(audit_dir.glob("audit_batch_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        items = _flatten_audit_items(data)
        if not items:
            raise ValueError(f"{path} 的 items 不是列表或为空")
        for raw_item in items:
            item = _normalize_audit_item(raw_item)
            slot = str(item.get("任务ID", "")).strip()
            if not slot:
                raise ValueError(f"{path} 中存在缺少任务ID的审题结果")
            if slot not in results:
                results[slot] = item
    return results


def regen_row_from_generated(item: IO.QuestionItem, gen: Dict[str, Any], audit: Dict[str, Any]) -> Dict[str, Any]:
    note = "；".join(filter(None, [
        f"知识:{audit.get('知识审核错误类型', '')}" if str(audit.get("知识审核是否删除", "")) == "是" else "",
        f"语言:{audit.get('语言审核问题类型', '')}({audit.get('语言审核处理结果', '')})" if str(audit.get("语言审核处理结果", "")) in C.LANGUAGE_REGEN_RESULTS else "",
        str(audit.get("审核说明", "")),
    ]))
    return {
        C.SLOT_ID_COLUMN: item.slot_id,
        "问题": gen.get("问题", ""),
        "参考答案-第一层": gen.get("参考答案-第一层", ""),
        "参考答案-第二层": gen.get("参考答案-第二层", ""),
        "参考答案-第三层": gen.get("参考答案-第三层", ""),
        "难度": item.fields.get("难度", ""),
        "建议作答时间": gen.get("建议作答时间", IO.duration_for(item.fields.get("难度", ""))),
        "行业": item.fields.get("行业", ""),
        "岗位名称": item.fields.get("岗位名称", ""),
        "岗位编码": item.fields.get("岗位编码", ""),
        "技能分类": item.fields.get("技能分类", ""),
        "技能": item.fields.get("技能", ""),
        "知识点": item.fields.get("知识点", ""),
        C.SCENE_INPUT_COLUMN: item.fields.get(C.SCENE_INPUT_COLUMN, ""),
        "JD": item.fields.get("JD", ""),
        "问题类型": note,
        C.PROMPT_COLUMN: item.question_prompt,
    }


def checkpoint_record(slot: str, round_no: int, status: str, gen: Dict[str, Any], audit: Dict[str, Any]) -> Dict[str, Any]:
    note = "；".join(filter(None, [
        f"知识:{audit.get('知识审核错误类型', '')}" if str(audit.get("知识审核是否删除", "")) == "是" else "",
        f"语言:{audit.get('语言审核问题类型', '')}({audit.get('语言审核处理结果', '')})" if str(audit.get("语言审核处理结果", "")) in C.LANGUAGE_REGEN_RESULTS else "",
        str(audit.get("审核说明", "")),
    ])) or status
    return {
        "任务ID": slot,
        "轮次": round_no,
        "状态": status,
        "问题": gen.get("问题", ""),
        "参考答案-第一层": gen.get("参考答案-第一层", ""),
        "参考答案-第二层": gen.get("参考答案-第二层", ""),
        "参考答案-第三层": gen.get("参考答案-第三层", ""),
        "建议作答时间": gen.get("建议作答时间", ""),
        "岗位名称": gen.get("岗位名称", ""),
        "知识点": gen.get("知识点", ""),
        "审核说明": note,
        "语言问题类型": audit.get("语言审核问题类型", ""),
    }


def normalize_final_mark(audit: Dict[str, Any]) -> str:
    final = str(audit.get("最终标记", "")).strip()
    if final in {"通过", "重出"}:
        return final
    if str(audit.get("知识审核是否删除", "")).strip() == "是":
        return "重出"
    if str(audit.get("语言审核处理结果", "")).strip() in C.LANGUAGE_REGEN_RESULTS:
        return "重出"
    return "通过"


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser()
    generated_path = Path(args.generated).expanduser()
    audit_dir = Path(args.audit_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = Checkpoint(Path(args.checkpoint).expanduser() if args.checkpoint else out_dir / C.CHECKPOINT_FILENAME)

    items = IO.read_input_table(input_path)
    by_slot = {it.slot_id: it for it in items}
    generated = load_generated(generated_path)
    audits = load_audit_results(audit_dir)

    missing = sorted([slot for slot in generated if generated[slot].get("出题状态") == "成功" and slot not in audits])
    if missing and not args.allow_missing:
        raise RuntimeError(f"缺少 {len(missing)} 条审题结果，例如：{missing[:5]}")

    regen_rows: List[Dict[str, Any]] = []
    pass_count = regen_count = 0
    answer_prompt = items[0].answer_prompt if items else ""
    for slot, gen in generated.items():
        if gen.get("出题状态") != "成功":
            continue
        item = by_slot.get(slot)
        if item is None:
            raise RuntimeError(f"输入表找不到任务ID={slot}")
        audit = audits.get(slot)
        if not audit:
            continue
        mark = normalize_final_mark(audit)
        status = C.CHECKPOINT_STATUS_PASS if mark == "通过" else C.CHECKPOINT_STATUS_REGEN
        ckpt.upsert(checkpoint_record(slot, int(gen.get("轮次", args.round_no)), status, gen, audit))
        if mark == "通过":
            pass_count += 1
        else:
            regen_count += 1
            regen_rows.append(regen_row_from_generated(item, gen, audit))

    final_rows = ckpt.collect_passed_final_rows(by_slot)
    final_path = out_dir / f"题目与知识点岗位的关联关系表_{ts()}.xlsx"
    IO.write_final_table(final_path, final_rows)
    regen_path = None
    if regen_rows:
        regen_path = out_dir / f"重出表_第{args.round_no}轮_{ts()}.xlsx"
        IO.write_regen_table(regen_path, regen_rows, answer_prompt)

    print(f"回收完成：本次通过 {pass_count}，重出 {regen_count}", flush=True)
    print(f"最终/累计好题表：{final_path}", flush=True)
    if regen_path:
        print(f"下一轮重出表：{regen_path}", flush=True)
    print(f"checkpoint：{ckpt.path}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回收 Cursor 审题结果")
    p.add_argument("--input", required=True)
    p.add_argument("--generated", required=True)
    p.add_argument("--audit-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--round-no", type=int, default=1)
    p.add_argument("--checkpoint", default="")
    p.add_argument("--allow-missing", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("用户中断", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        sys.exit(1)
