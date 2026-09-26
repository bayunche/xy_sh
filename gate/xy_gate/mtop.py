"""mtop 网关统一调用（h5api.m.goofish.com）。

约定（协议细节见 docs/protocol-notes.md）：
- URL: https://h5api.m.goofish.com/h5/{api}/{version}/
- 签名: md5("{token}&{t}&{appKey}&{data}")，token 取 Cookie _m_h5_tk 首段，appKey=34839810
- 令牌过期（闲鱼历史拼写 EXOIRED / 标准 EXPIRED / EMPTY）：从响应 Set-Cookie 取新
  _m_h5_tk 合并回 Cookie 后重签重试（最多 3 次）
- 风控（FAIL_SYS_USER_VALIDATE / RGV587 / punish 等）：抛 RiskControlError 转人工，
  不自动重试
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple

import aiohttp

H5_API_BASE = "https://h5api.m.goofish.com/h5"
APP_KEY = "34839810"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

# 令牌过期/缺失 → 刷新 _m_h5_tk 后重试
TOKEN_EXPIRED_MARKERS = (
    "FAIL_SYS_TOKEN_EXOIRED",
    "FAIL_SYS_TOKEN_EXPIRED",
    "FAIL_SYS_TOKEN_EMPTY",
)
# 会话过期（Cookie 失效）→ 需要重新扫码/换 Cookie，不重试
SESSION_EXPIRED_MARKERS = ("FAIL_SYS_SESSION_EXPIRED", "SESSION_EXPIRED")

# 风控/验证 → 转人工
RISK_MARKERS = (
    "FAIL_SYS_USER_VALIDATE",
    "RGV587",
    "FAIL_SYS_ILLEGAL_ACCESS",
    "punish",
    "x5sec",
)


class MtopError(RuntimeError):
    pass


class SessionExpiredError(MtopError):
    """闲鱼登录态过期，需要重新扫码获取 Cookie。"""


class RiskControlError(MtopError):
    """触发滑块/风控验证，需要人工处理 Cookie。"""

    def __init__(self, message: str, punish_url: str = ""):
        super().__init__(message)
        self.punish_url = punish_url


def generate_sign(t: str, token: str, data: str, app_key: str = APP_KEY) -> str:
    return hashlib.md5(f"{token}&{t}&{app_key}&{data}".encode("utf-8")).hexdigest()


class MtopClient:
    """持有账号 Cookie 状态的 mtop 调用器（Set-Cookie 会回写 self.cookies_str）。"""

    def __init__(self, cookies_str: str, proxy: Optional[str] = None):
        self.cookies_str = cookies_str
        self.proxy = proxy
        self._session: Optional[aiohttp.ClientSession] = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False),
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def call(
        self,
        api: str,
        version: str = "1.0",
        data: Any = None,
        extra_params: Optional[Dict[str, str]] = None,
        referer: str = "https://www.goofish.com/",
        extra_headers: Optional[Dict[str, str]] = None,
        timeout_sec: int = 20,
    ) -> Dict[str, Any]:
        """调用 mtop 接口，返回解析后的 JSON。令牌过期自动重试。"""
        from .cookies import h5_sign_token, merge_cookie_str

        data_val = data if isinstance(data, str) else json.dumps(
            data if data is not None else {}, separators=(",", ":"), ensure_ascii=False,
        )

        session = await self.session()
        last_err = ""
        for _attempt in range(3):
            t = str(int(time.time() * 1000))
            token = h5_sign_token(self.cookies_str)
            sign = generate_sign(t, token, data_val)
            params = {
                "jsv": "2.7.2",
                "appKey": APP_KEY,
                "t": t,
                "sign": sign,
                "v": version,
                "type": "originaljson",
                "accountSite": "xianyu",
                "dataType": "json",
                "timeout": "20000",
                "api": api,
                "sessionOption": "AutoLoginOnly",
            }
            if extra_params:
                params.update(extra_params)
            headers = {
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "cookie": self.cookies_str.replace("\n", "").replace("\r", ""),
                "referer": referer,
                "user-agent": DEFAULT_UA,
            }
            if extra_headers:
                headers.update(extra_headers)

            url = f"{H5_API_BASE}/{api}/{version}/"
            try:
                async with session.post(
                    url, params=params, data={"data": data_val},
                    headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_sec),
                    proxy=self.proxy,
                ) as resp:
                    set_cookies: Dict[str, str] = {}
                    for hdr in resp.headers.getall("Set-Cookie", []):
                        if "=" not in hdr:
                            continue
                        name, value = hdr.split(";", 1)[0].split("=", 1)
                        set_cookies[name.strip()] = value.strip()
                    try:
                        res = await resp.json(content_type=None)
                    except Exception:
                        raise MtopError(f"{api} 响应非 JSON: {(await resp.text())[:200]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = f"{api} 网络异常: {e}"
                continue

            if set_cookies:
                self.cookies_str = merge_cookie_str(self.cookies_str, set_cookies)

            ret = res.get("ret") or [""]
            ret_str = str(ret[0]) if ret else ""
            if any(m in ret_str for m in SESSION_EXPIRED_MARKERS):
                raise SessionExpiredError(
                    f"{api} 登录态过期（FAIL_SYS_SESSION_EXPIRED）——请在后台「设置→浏览器扫码获取」重新登录")
            if any(m in ret_str for m in TOKEN_EXPIRED_MARKERS):
                last_err = f"{api} 令牌过期（已用 Set-Cookie 刷新后重试）: {ret_str}"
                continue  # 下一轮用新 _m_h5_tk 重签
            if any(m in ret_str for m in RISK_MARKERS):
                punish = ""
                data_node = res.get("data")
                if isinstance(data_node, dict):
                    punish = str(data_node.get("url") or "")
                raise RiskControlError(f"{api} 触发风控验证: {ret_str}", punish)
            return res

        raise MtopError(f"{api} 重试 3 次仍失败: {last_err or '未知错误'}")


def find_key_recursive(obj: Any, *keys: str) -> Optional[Any]:
    """在嵌套结构中广度优先找第一个命中的 key（闲鱼响应层级多变，兜底用）。"""
    from collections import deque
    queue = deque([obj])
    while queue:
        cur = queue.popleft()
        if isinstance(cur, dict):
            for k in keys:
                if k in cur and cur[k] not in ("", None):
                    return cur[k]
            queue.extend(cur.values())
        elif isinstance(cur, list):
            queue.extend(cur)
    return None
