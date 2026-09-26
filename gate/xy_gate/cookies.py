"""Cookie/设备ID/消息ID 等登录态工具（与闲鱼网页端约定一致）。"""
from __future__ import annotations

import random
import time
import uuid
from typing import Dict, Optional


def parse_cookies(cookies_str: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in (cookies_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                cookies[k.strip()] = v.strip()
    return cookies


def cookie_value(cookies_str: str, key: str) -> str:
    return parse_cookies(cookies_str).get(key, "")


def h5_sign_token(cookies_str: str) -> str:
    """mtop 签名用的 token：取 _m_h5_tk 首段。缺失返回空串（首次调用会拿到
    FAIL_SYS_TOKEN_EMPTY，由 mtop.py 用响应 Set-Cookie 刷新后重试）。"""
    tk = cookie_value(cookies_str, "_m_h5_tk")
    return tk.split("_")[0] if tk else ""


def merge_cookie_str(base: str, updates: Dict[str, str]) -> str:
    cookies = parse_cookies(base)
    cookies.update({k: v for k, v in updates.items() if v})
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def generate_device_id(user_id: str) -> str:
    """设备 ID：UUIDv4 形态 + "-" + 用户ID。仅在 /reg 与 IM token 请求中使用。"""
    return f"{uuid.uuid4()}-{user_id}"


def generate_mid() -> str:
    """LWP 消息 ID。"""
    return f"{int(1000 * random.random())}{int(time.time() * 1000)} 0"


def generate_uuid() -> str:
    return f"-{int(time.time() * 1000)}1"
