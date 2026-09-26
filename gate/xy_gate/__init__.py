"""xy-gate：闲鱼卖家机器人协议层。

分层（见 docs/architecture.md）：
- mtop/imtoken/wsclient：闲鱼协议（WS 私信长连接 + mtop 网关签名调用）
- inbound：入站 syncPushPackage → 结构化事件
- risk：风控闸门（自动回复限速、自动确认交易前置校验）
- store：sqlite 状态（聊天史/去重/确认台账/事件）
- daemon：事件 → SOP 路由；brain：dsh headless 桥
- server/cli：本地 HTTP API 与 xy-gate 命令行（dsh 大脑的动作工具）
"""

__version__ = "0.1.0"
