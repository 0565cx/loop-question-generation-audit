#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cursor 审题模式：先用 qwen3.7-plus 批量出题，生成待审文件与分批 JSON。

不调用外部 GPT API。后续由 Cursor 当前模型/子代理读取 batch_*.json 审题，并输出 audit_batch_*.json。
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

import config as C
import io_utils as IO
import llm_client as LLM
from checkpoint import Checkpoint
from generator import generate_one


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def generated_record(item: IO.QuestionItem, round_no: int, gen: Any) -> Dict[str, Any]:
    f = item.fields
    return {
        "任务ID": item.slot_id,
        "轮次": round_no,
        "来源行号": item.row_index,
        "问题": gen.question,
        "参考答案-第一层": gen.answer1,
        "参考答案-第二层": gen.answer2,
        "参考答案-第三层": gen.answer3,
        "难度": f.get("难度", ""),
        "建议作答时间": gen.duration,
        "行业": f.get("行业", ""),
        "岗位名称": f.get("岗位名称", ""),
        "岗位编码": f.get("岗位编码", ""),
        "技能分类": f.get("技能分类", ""),
        "技能": f.get("技能", ""),
        "知识点": f.get("知识点", ""),
        C.SCENE_INPUT_COLUMN: f.get(C.SCENE_INPUT_COLUMN, ""),
        "JD": f.get("JD", ""),
        "出题状态": "成功" if gen.ok else "失败",
        "出题错误": gen.error,
        "原问题": item.original_question,
        "提示词": item.question_prompt,
        "出答案提示词": item.answer_prompt,
    }


def split_batches(records: List[Dict[str, Any]], out_dir: Path, batch_size: int) -> List[Path]:
    batch_dir = out_dir / "audit_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    ok_records = [r for r in records if r.get("出题状态") == "成功"]
    for i in range(0, len(ok_records), batch_size):
        chunk = ok_records[i:i + batch_size]
        path = batch_dir / f"batch_{i // batch_size + 1:03d}.json"
        payload = {
            "说明": "请按 references/knowledge-audit-prompt.txt 和 references/language-audit-rules.md 对 items 审题；输出 audit_batch_同编号.json。",
            "输出格式": {
                "items": [
                    {
                        "任务ID": "原任务ID",
                        "知识审核是否删除": "是/否",
                        "知识审核错误类型": "如A1/B2，可空",
                        "知识审核删除原因": "具体原因，可空",
                        "语言审核处理结果": "不变/修改/重出",
                        "语言审核问题类型": "无明显语言问题/具体类型，可多个分号",
                        "最终标记": "通过/重出"
                    }
                ]
            },
            "items": chunk,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(path)
    return paths


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = Checkpoint(Path(args.checkpoint).expanduser() if args.checkpoint else out_dir / C.CHECKPOINT_FILENAME)

    items = IO.read_input_table(input_path)
    if args.limit:
        items = items[:args.limit]
    if args.resume:
        # Cursor 审题模式这里只跳过已经最终通过的任务；重出/失败仍可重新生成。
        items = [it for it in items if not any(ckpt.is_success(it.slot_id, r) for r in range(1, args.round_no + 1))]

    if not items:
        raise RuntimeError("没有需要出题的 item")

    client = LLM.make_generator_client()
    print(f"准备出题：{len(items)} 道；round={args.round_no}；workers={args.workers}", flush=True)

    records: List[Dict[str, Any]] = []

    def task(it: IO.QuestionItem):
        gen = generate_one(client, it)
        return it, gen

    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        fmap = {ex.submit(task, it): it for it in items}
        for idx, fut in enumerate(futures.as_completed(fmap), 1):
            it, gen = fut.result()
            rec = generated_record(it, args.round_no, gen)
            records.append(rec)
            if not gen.ok:
                ckpt.upsert({
                    "任务ID": it.slot_id, "轮次": args.round_no, "状态": C.CHECKPOINT_STATUS_GEN_FAIL,
                    "问题": gen.question, "参考答案-第一层": "", "参考答案-第二层": "", "参考答案-第三层": "",
                    "建议作答时间": gen.duration, "岗位名称": it.fields.get("岗位名称", ""), "知识点": it.fields.get("知识点", ""),
                    "审核说明": f"出题失败:{gen.error}", "语言问题类型": f"出题失败:{gen.error}",
                })
            print(f"[{idx}/{len(items)}] {'成功' if gen.ok else '失败'} | {it.slot_id} | {it.fields.get('知识点')} | {gen.error}", flush=True)
            # 每题落一次生成结果，便于中断检查
            pd.DataFrame(records).to_excel(out_dir / "generated_for_cursor_audit_checkpoint.xlsx", index=False)

    generated_xlsx = out_dir / f"generated_for_cursor_audit_round{args.round_no}_{ts()}.xlsx"
    pd.DataFrame(records).to_excel(generated_xlsx, index=False)
    batch_paths = split_batches(records, out_dir, args.batch_size)
    manifest = out_dir / "cursor_audit_manifest.json"
    manifest.write_text(json.dumps({
        "round_no": args.round_no,
        "generated_xlsx": str(generated_xlsx),
        "batch_paths": [str(p) for p in batch_paths],
        "output_audit_dir": str(out_dir / "audit_results"),
        "checkpoint": str(ckpt.path),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "audit_results").mkdir(exist_ok=True)

    print(f"生成完成：{generated_xlsx}", flush=True)
    print(f"待审批次：{len(batch_paths)} 个，目录：{out_dir / 'audit_batches'}", flush=True)
    print(f"manifest：{manifest}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="生成待 Cursor 审题的批次文件")
    p.add_argument("--input", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--round-no", type=int, default=1)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--checkpoint", default="")
    p.add_argument("--resume", action="store_true")
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
