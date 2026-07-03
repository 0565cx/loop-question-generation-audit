#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题级断点续跑：逐题落盘，支持 resume 跳过已完成、retry-failures 只重跑失败题。

checkpoint 以 (任务ID, 轮次) 为唯一键。每完成一道题（出题+审题）就写一次，
中途中断后重启不会丢失已消耗的模型调用。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import config as C


def slot_round_key(slot_id: str, round_no: int) -> str:
    return f"{slot_id}@@r{round_no}"


class Checkpoint:
    """线程安全的题级进度存储，落盘为单 sheet 的 xlsx。"""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._records: "Dict[str, Dict[str, Any]]" = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            df = pd.read_excel(self.path, sheet_name=C.CHECKPOINT_SHEET)
        except Exception:
            return
        df = df.fillna("")
        for rec in df.to_dict("records"):
            slot = str(rec.get("任务ID", "")).strip()
            try:
                rnd = int(rec.get("轮次"))
            except (TypeError, ValueError):
                continue
            if slot:
                self._records[slot_round_key(slot, rnd)] = rec

    # ---- 查询 ----
    def get(self, slot_id: str, round_no: int) -> Optional[Dict[str, Any]]:
        return self._records.get(slot_round_key(slot_id, round_no))

    def status(self, slot_id: str, round_no: int) -> str:
        rec = self.get(slot_id, round_no)
        return str(rec.get("状态", "")).strip() if rec else ""

    def has_done(self, slot_id: str, round_no: int) -> bool:
        """该题该轮是否已有结果（用于 resume 跳过）。"""
        return self.status(slot_id, round_no) != ""

    def is_success(self, slot_id: str, round_no: int) -> bool:
        return self.status(slot_id, round_no) == C.CHECKPOINT_STATUS_PASS

    # ---- 写入 ----
    def upsert(self, record: Dict[str, Any]) -> None:
        slot = str(record.get("任务ID", "")).strip()
        rnd = int(record.get("轮次"))
        with self._lock:
            self._records[slot_round_key(slot, rnd)] = record
            self._flush_locked()

    def drop_failures_for_retry(self, round_no: Optional[int] = None) -> int:
        """--retry-failures：删除未通过记录使其被重跑。

        round_no 为 None 时清除所有轮次的非通过记录；否则只清该轮。返回删除条数。
        """
        removed = 0
        with self._lock:
            for key in list(self._records.keys()):
                rec = self._records[key]
                if round_no is not None:
                    try:
                        if int(rec.get("轮次")) != round_no:
                            continue
                    except (TypeError, ValueError):
                        continue
                if str(rec.get("状态", "")).strip() != C.CHECKPOINT_STATUS_PASS:
                    del self._records[key]
                    removed += 1
            if removed:
                self._flush_locked()
        return removed

    def _flush_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = list(self._records.values())
        df = pd.DataFrame(rows, columns=C.CHECKPOINT_COLUMNS)
        tmp = self.path.with_suffix(".tmp.xlsx")
        df.to_excel(tmp, index=False, sheet_name=C.CHECKPOINT_SHEET)
        tmp.replace(self.path)

    # ---- 恢复好题池 ----
    def collect_passed_final_rows(self, items_by_slot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 checkpoint 里把所有“通过”的题恢复成最终好题表的行。"""
        import io_utils as IO
        rows: List[Dict[str, Any]] = []
        for rec in self._records.values():
            if str(rec.get("状态", "")).strip() != C.CHECKPOINT_STATUS_PASS:
                continue
            slot = str(rec.get("任务ID", "")).strip()
            item = items_by_slot.get(slot)
            if item is None:
                continue
            rows.append(IO.build_final_row(
                item,
                str(rec.get("问题", "")),
                str(rec.get("参考答案-第一层", "")),
                str(rec.get("参考答案-第二层", "")),
                str(rec.get("参考答案-第三层", "")),
                _safe_int(rec.get("建议作答时间")),
            ))
        return rows


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
