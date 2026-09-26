"""IM Token 获取（WebSocket /reg 用的 accessToken）。"""
from __future__ import annotations

from typing import Dict

from .mtop import MtopClient, find_key_recursive

IM_TOKEN_API = "mtop.taobao.idlemessage.pc.login.token"
IM_APP_KEY = "444e9908a51d1cb236a27862abc769c9"


async def fetch_im_token(client: MtopClient, device_id: str) -> str:
    """调 IM Token 接口，返回 accessToken。

    data: {"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"<did>"}
    """
    res = await client.call(IM_TOKEN_API, "1.0", data={
        "appKey": IM_APP_KEY,
        "deviceId": device_id,
    }, extra_params={
        "spm_cnt": "a21ybx.im.0.0",
        "spm_pre": "a21ybx.home.sidebar.1",
    })
    token = find_key_recursive(res, "accessToken", "access_token")
    if not token:
        raise RuntimeError(f"IM Token 接口未返回 accessToken: {str(res)[:300]}")
    return str(token)
