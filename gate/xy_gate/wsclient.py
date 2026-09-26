"""闲鱼私信 WebSocket 长连接。

流程（协议细节见 docs/protocol-notes.md）：
1. 连 wss://wss-goofish.dingtalk.com/
2. 取 IM token（mtop.taobao.idlemessage.pc.login.token）
3. 发 /reg 注册（app-key=444e…, token, did）
4. 发 /r/SyncStatus/ackDiff 同步位点
5. 每 15s 发 {"lwp":"/!"} 心跳；30s 无响应/连续失败 → 重连（指数退避）
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp

from .cookies import generate_device_id, generate_mid, generate_uuid
from .imtoken import IM_APP_KEY, fetch_im_token
from .inbound import is_sync_package, sync_package_to_events
from .mtop import MtopClient

log = logging.getLogger("xy_gate.ws")

WS_URL = "wss://wss-goofish.dingtalk.com/"
HEARTBEAT_INTERVAL = 15
HEARTBEAT_TIMEOUT = 30
WS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

EventCallback = Callable[[List[Dict]], Awaitable[None]]


class XianyuWS:
    def __init__(
        self,
        mtop: MtopClient,
        user_id: str,
        on_events: EventCallback,
        device_id: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self.mtop = mtop
        self.my_id = user_id
        self.on_events = on_events
        self.on_status = on_status or (lambda s: None)
        self.device_id = device_id or generate_device_id(user_id)
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._stop = asyncio.Event()
        self.connected = False
        self.last_pong = 0.0
        self.last_message_at = 0.0

    def _send_status(self, s: str) -> None:
        self.connected = s == "connected"
        try:
            self.on_status(s)
        except Exception:
            pass

    async def start(self) -> None:
        """长驻任务：断线自动重连。"""
        backoff = 5
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("WS 连接异常退出: %s", e)
            self._send_status("disconnected")
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff + random.random() * 3)
            backoff = min(backoff * 2, 120)

    async def stop(self) -> None:
        self._stop.set()
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def _run_once(self) -> None:
        self._session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        try:
            async with self._session.ws_connect(
                WS_URL,
                headers={"User-Agent": WS_UA, "Origin": "https://www.goofish.dingtalk.com"},
                heartbeat=None,
                timeout=aiohttp.ClientWSTimeout(ws_close=10),
            ) as ws:
                self._ws = ws
                await self._register(ws)
                self._send_status("connected")
                heartbeat = asyncio.create_task(self._heartbeat_loop(ws))
                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.last_message_at = time.time()
                            await self._on_frame(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                finally:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except (asyncio.CancelledError, Exception):
                        pass
        finally:
            if self._session and not self._session.closed:
                await self._session.close()
            self._ws = None

    async def _register(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        token = await fetch_im_token(self.mtop, self.device_id)
        await ws.send_json({
            "lwp": "/reg",
            "headers": {
                "cache-header": "app-key token ua wv",
                "app-key": IM_APP_KEY,
                "token": token,
                "ua": WS_UA,
                "dt": "j",
                "wv": "im:3,au:3,sy:6",
                "sync": "0,0;0;0;",
                "did": self.device_id,
                "mid": generate_mid(),
            },
        })
        await asyncio.sleep(1)
        now = int(time.time() * 1000)
        await ws.send_json({
            "lwp": "/r/SyncStatus/ackDiff",
            "headers": {"mid": generate_mid()},
            "body": [{
                "pipeline": "sync", "tooLong2Tag": "PNM,1", "channel": "sync",
                "topic": "sync", "highPts": 0, "pts": now * 1000, "seq": 0,
                "timestamp": now,
            }],
        })
        log.info("WS 注册完成（did=%s…）", self.device_id[:8])

    async def _heartbeat_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        fails = 0
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self.last_pong and time.time() - self.last_pong > HEARTBEAT_TIMEOUT:
                raise ConnectionError(f"心跳 {HEARTBEAT_TIMEOUT}s 无响应，主动断开重连")
            try:
                await ws.send_json({"lwp": "/!", "headers": {"mid": generate_mid()}})
                fails = 0
            except Exception as e:
                fails += 1
                if fails >= 3:
                    raise ConnectionError(f"心跳连续失败 3 次: {e}")

    async def _on_frame(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict):
            return
        if "body" not in data and data.get("code") == 200:
            self.last_pong = time.time()   # 心跳响应
            return
        if is_sync_package(data):
            events = sync_package_to_events(data, self.my_id)
            if events:
                try:
                    await self.on_events(events)
                except Exception:
                    log.exception("事件回调处理失败（不影响连接）")

    async def send_msg(self, chat_id: str, to_user_id: str, content: str) -> Dict:
        """发文本消息（contentType 101 custom，载荷为 base64(JSON)）。"""
        ws = self._ws
        if ws is None or ws.closed:
            return {"success": False, "error": "WS 未连接，消息未发出"}
        msg_content = {"contentType": 1, "text": {"text": content}}
        content_b64 = base64.b64encode(
            json.dumps(msg_content, ensure_ascii=False).encode("utf-8")
        ).decode("utf-8")
        payload = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {"mid": generate_mid()},
            "body": [
                {
                    "uuid": generate_uuid(),
                    "cid": f"{chat_id}@goofish",
                    "conversationType": 1,
                    "content": {"contentType": 101, "custom": {"type": 1, "data": content_b64}},
                    "redPointPolicy": 0,
                    "extension": {"extJson": "{}"},
                    "ctx": {"appVersion": "1.0", "platform": "web"},
                    "mtags": {},
                    "msgReadStatusSetting": 1,
                },
                {"actualReceivers": [f"{to_user_id}@goofish", f"{self.my_id}@goofish"]},
            ],
        }
        try:
            await ws.send_str(json.dumps(payload, ensure_ascii=False))
            return {"success": True, "chat_id": chat_id, "to": to_user_id, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}
