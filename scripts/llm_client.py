#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出题人(qwen3.7-plus)与审题人(GPT5.5)的模型客户端封装。"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import config as C

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


def _first_env(names: List[str], default: str = "") -> str:
    for name in names:
        val = os.getenv(name)
        if val:
            return val
    return default


def _require_openai() -> None:
    if OpenAI is None:
        raise RuntimeError("缺少 openai 依赖，请先运行：python3 -m pip install openai pandas openpyxl")


def make_generator_client() -> Any:
    """出题人：qwen3.7-plus。"""
    _require_openai()
    api_key = _first_env(C.GENERATOR_API_KEY_ENVS)
    if not api_key:
        raise RuntimeError(f"缺少出题人 API Key，请设置环境变量之一：{C.GENERATOR_API_KEY_ENVS}")
    return OpenAI(api_key=api_key, base_url=C.GENERATOR_BASE_URL)


def make_auditor_client() -> Any:
    """审题人：GPT5.5。"""
    _require_openai()
    api_key = _first_env(C.AUDITOR_API_KEY_ENVS)
    if not api_key:
        raise RuntimeError(f"缺少审题人 API Key，请设置环境变量之一：{C.AUDITOR_API_KEY_ENVS}")
    base_url = _first_env(C.AUDITOR_BASE_URL_ENVS, C.AUDITOR_BASE_URL_DEFAULT)
    return OpenAI(api_key=api_key, base_url=base_url)


def auditor_model() -> str:
    return os.getenv(C.AUDITOR_MODEL_ENV) or C.AUDITOR_MODEL_DEFAULT


def chat_json(client: Any, model: str, messages: List[Dict[str, str]],
              temperature: float = 0.3, max_retries: int = 5,
              force_json: bool = True) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            kwargs: Dict[str, Any] = dict(model=model, messages=messages,
                                          temperature=temperature, timeout=120)
            if force_json:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"模型调用失败（{model}）：{last_error}")


def parse_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))
