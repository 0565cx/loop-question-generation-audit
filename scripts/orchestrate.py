#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排人主脚本：循环出题 -> 审题 -> 整理重出表 -> 再出题 ... 直到全部通过。

流程（固定）：
1. 读取上一轮被标记“重出”的题目表（sheet 出题/出答案）。
2. 出题人(qwen3.7-plus) 按每行自带提示词出新题和三层答案。
3. 审题人(GPT5.5) 对新题同步做知识审核 + 语言审核。
   - 语言审核处理结果为“修改”和“重出”的，最终都标记为“重出”。
4. 通过的题进入好题池；未通过的题整理成新的“重出表”。
5. 用新的重出表继续下一轮，直到没有需要重出的题。
6. 导出最终好题表（列对齐 525提测/题目与知识点岗位的关联关系表）。

用法：
  python3 orchestrate.py --input <重出表.xlsx> --out-dir <输出目录> [--max-rounds 6] [--workers 6]
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import config as C
import io_utils as IO
import llm_client as LLM
from auditor import audit_one
from checkpoint import Checkpoint
from generator import generate_one
from io_utils import QuestionItem


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _regen_row_from_item(item: QuestionItem, question: str, a1: str, a2: str, a3: str,
                         duration: int, audit_note: str) -> Dict[str, Any]:
    """把未通过的题写回重出表 出题 sheet：保留字段+提示词，附最新题/答案与审核问题类型。"""
    f = item.fields
    return {
        C.SLOT_ID_COLUMN: item.slot_id,
        "问题": question,
        "参考答案-第一层": a1,
        "参考答案-第二层": a2,
        "参考答案-第三层": a3,
        "难度": f.get("难度", ""),
        "建议作答时间": duration,
        "行业": f.get("行业", ""),
        "岗位名称": f.get("岗位名称", ""),
        "岗位编码": f.get("岗位编码", ""),
        "技能分类": f.get("技能分类", ""),
        "技能": f.get("技能", ""),
        "知识点": f.get("知识点", ""),
        C.SCENE_INPUT_COLUMN: f.get(C.SCENE_INPUT_COLUMN, ""),
        "JD": f.get("JD", ""),
        "问题类型": audit_note,
        C.PROMPT_COLUMN: item.question_prompt,
    }


def _make_checkpoint_record(it: QuestionItem, round_no: int, status: str,
                            question: str, a1: str, a2: str, a3: str,
                            duration: int, note: str, lang_types: str) -> Dict[str, Any]:
    return {
        "任务ID": it.slot_id,
        "轮次": round_no,
        "状态": status,
        "问题": question,
        "参考答案-第一层": a1,
        "参考答案-第二层": a2,
        "参考答案-第三层": a3,
        "建议作答时间": duration,
        "岗位名称": it.fields.get("岗位名称", ""),
        "知识点": it.fields.get("知识点", ""),
        "审核说明": note,
        "语言问题类型": lang_types,
    }


def _next_item_from(it: QuestionItem, question: str, lang_types: str) -> QuestionItem:
    return QuestionItem(
        row_index=it.row_index, fields=it.fields,
        question_prompt=it.question_prompt, answer_prompt=it.answer_prompt,
        original_question=question,
        language_problem_types=[lang_types] if lang_types else [],
        slot_id=it.slot_id,
    )


def run_round(round_no: int, items: List[QuestionItem], gen_client: Any,
              audit_client: Any, audit_model: str, workers: int,
              good_rows: List[Dict[str, Any]], out_dir: Path,
              ckpt: Checkpoint, resume: bool) -> List[QuestionItem]:
    """跑一轮：出题+审题，逐题写 checkpoint，返回需要进入下一轮的 items。"""
    print(f"\n===== 第 {round_no} 轮：{len(items)} 道题 =====", flush=True)
    regen_rows: List[Dict[str, Any]] = []
    next_items: List[QuestionItem] = []
    answer_prompt = items[0].answer_prompt if items else ""

    # resume：分离本轮已完成的题，只对未完成的题调用模型
    pending: List[QuestionItem] = []
    skipped = resumed_pass = 0
    for it in items:
        if resume and ckpt.has_done(it.slot_id, round_no):
            skipped += 1
            rec = ckpt.get(it.slot_id, round_no) or {}
            status = str(rec.get("状态", "")).strip()
            if status == C.CHECKPOINT_STATUS_PASS:
                resumed_pass += 1  # 好题会在 main 里统一从 checkpoint 恢复
            else:
                # 已完成但未通过 -> 直接进入下一轮
                note = str(rec.get("审核说明", ""))
                lang = str(rec.get("语言问题类型", ""))
                next_items.append(_next_item_from(it, str(rec.get("问题", "")), lang))
                regen_rows.append(_regen_row_from_item(
                    it, str(rec.get("问题", "")), str(rec.get("参考答案-第一层", "")),
                    str(rec.get("参考答案-第二层", "")), str(rec.get("参考答案-第三层", "")),
                    IO.duration_for(it.fields.get("难度", "")), note))
            continue
        pending.append(it)
    if skipped:
        print(f"  断点续跑：跳过本轮已完成 {skipped} 道（其中通过 {resumed_pass}）", flush=True)

    def task(it: QuestionItem):
        gen = generate_one(gen_client, it)
        if not gen.ok:
            return it, gen, None
        audit = audit_one(audit_client, audit_model, it.fields,
                          gen.question, gen.answer1, gen.answer2, gen.answer3)
        return it, gen, audit

    passed = failed = 0
    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(task, it): it for it in pending}
        for i, fut in enumerate(futures.as_completed(future_map), 1):
            it, gen, audit = fut.result()
            if not gen.ok:
                failed += 1
                note = f"出题失败:{gen.error}"
                ckpt.upsert(_make_checkpoint_record(
                    it, round_no, C.CHECKPOINT_STATUS_GEN_FAIL,
                    gen.question, "", "", "", gen.duration, note, note))
                next_items.append(_next_item_from(it, gen.question, note))
                regen_rows.append(_regen_row_from_item(it, gen.question, "", "", "",
                                                        gen.duration, note))
                print(f"  [{i}/{len(pending)}] 出题失败 | {it.fields.get('知识点')} | {gen.error}", flush=True)
                continue
            if audit.passed:
                passed += 1
                ckpt.upsert(_make_checkpoint_record(
                    it, round_no, C.CHECKPOINT_STATUS_PASS,
                    gen.question, gen.answer1, gen.answer2, gen.answer3,
                    gen.duration, "通过", ""))
                good_rows.append(IO.build_final_row(it, gen.question, gen.answer1,
                                                     gen.answer2, gen.answer3, gen.duration))
                print(f"  [{i}/{len(pending)}] 通过 | {it.fields.get('知识点')}", flush=True)
            else:
                failed += 1
                note = "；".join(filter(None, [
                    f"知识:{audit.knowledge_error_types}" if audit.knowledge_delete else "",
                    f"语言:{audit.language_problem_types}({audit.language_result})" if audit.language_result in C.LANGUAGE_REGEN_RESULTS else "",
                    audit.error,
                ]))
                ckpt.upsert(_make_checkpoint_record(
                    it, round_no, C.CHECKPOINT_STATUS_REGEN,
                    gen.question, gen.answer1, gen.answer2, gen.answer3,
                    gen.duration, note, audit.language_problem_types))
                next_items.append(_next_item_from(it, gen.question, audit.language_problem_types))
                regen_rows.append(_regen_row_from_item(it, gen.question, gen.answer1,
                                                       gen.answer2, gen.answer3, gen.duration, note))
                print(f"  [{i}/{len(pending)}] 重出 | {it.fields.get('知识点')} | {note}", flush=True)

    print(f"  本轮新出通过 {passed}，需重出 {failed}", flush=True)
    if regen_rows:
        regen_path = out_dir / f"重出表_第{round_no}轮_{_timestamp()}.xlsx"
        IO.write_regen_table(regen_path, regen_rows, answer_prompt)
        print(f"  重出表已保存：{regen_path}", flush=True)
    return next_items


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    items = IO.read_input_table(input_path)
    if not items:
        raise RuntimeError("输入表没有题目")
    items_by_slot = {it.slot_id: it for it in items}

    gen_client = LLM.make_generator_client()
    audit_client = LLM.make_auditor_client()
    audit_model = LLM.auditor_model()

    ckpt_path = Path(args.checkpoint).expanduser() if args.checkpoint else out_dir / C.CHECKPOINT_FILENAME
    ckpt = Checkpoint(ckpt_path)
    resume = args.resume or args.retry_failures
    if args.retry_failures:
        removed = ckpt.drop_failures_for_retry(None)
        print(f"失败重跑模式：清除所有轮次未通过记录 {removed} 条，仅重跑这些题（已通过的保留）。", flush=True)

    print(f"出题人={C.GENERATOR_MODEL} | 审题人={audit_model} | 初始题量={len(items)} | checkpoint={ckpt_path}", flush=True)
    if resume:
        print("已开启断点续跑：本轮已完成的题会被跳过。", flush=True)

    round_no = 0
    while items and round_no < args.max_rounds:
        round_no += 1
        items = run_round(round_no, items, gen_client, audit_client, audit_model,
                          args.workers, [], out_dir, ckpt, resume)
        # 好题池始终从 checkpoint 权威恢复，避免续跑时重复或遗漏
        good_rows = ckpt.collect_passed_final_rows(items_by_slot)
        good_path = out_dir / f"好题表_累计_{_timestamp()}.xlsx"
        IO.write_final_table(good_path, good_rows)

    good_rows = ckpt.collect_passed_final_rows(items_by_slot)
    final_path = out_dir / f"题目与知识点岗位的关联关系表_{_timestamp()}.xlsx"
    IO.write_final_table(final_path, good_rows)
    remaining = len(items)
    print(f"\n===== 完成 =====", flush=True)
    print(f"好题 {len(good_rows)} 道；未收敛剩余 {remaining} 道（轮次上限 {args.max_rounds}）", flush=True)
    print(f"最终好题表：{final_path}", flush=True)
    print(f"checkpoint：{ckpt_path}", flush=True)
    if remaining:
        print("提示：仍有题目未通过，可提高 --max-rounds、或用 --resume 续跑、或用 --retry-failures 只重跑失败题。", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="循环出题-审题工作流（编排人主脚本）")
    p.add_argument("--input", required=True, help="上一轮标记重出的题目表 xlsx（sheet: 出题/出答案）")
    p.add_argument("--out-dir", required=True, help="输出目录")
    p.add_argument("--max-rounds", type=int, default=6, help="最大循环轮数，防止死循环")
    p.add_argument("--workers", type=int, default=6, help="出题+审题并发数")
    p.add_argument("--checkpoint", default="", help="checkpoint xlsx 路径；默认输出目录下 checkpoint.xlsx")
    p.add_argument("--resume", action="store_true", help="断点续跑：跳过 checkpoint 中已完成的题")
    p.add_argument("--retry-failures", action="store_true", help="只重跑所有轮次未通过的题，已通过的保留")
    return p.parse_args()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("用户中断", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败：{exc}", file=sys.stderr)
        sys.exit(1)
