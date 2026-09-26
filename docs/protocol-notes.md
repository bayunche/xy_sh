# 闲鱼协议研究笔记

> 端点/签名/报文结构经 [zhinianboke/xianyu-auto-reply](https://github.com/zhinianboke/xianyu-auto-reply)（AGPL-3.0）
> 源码核实，2026-09 抓取。本项目为独立实现，仅引用协议事实；本笔记是该调研的沉淀。

## 1. 登录态

- Cookie 来源：浏览器登录 goofish.com 后复制整段；**必须包含**：
  - `unb`：卖家数字用户 ID（消息系统里的 myid）
  - `_m_h5_tk`：mtop 签名令牌（`token_时间戳` 形态，签名只取 `_` 前段）
- 设备 ID：`<uuid4>-<unb>`，本地生成即可，用于 IM token 与 WS 注册。

## 2. mtop 网关（HTTP）

- 域名：`https://h5api.m.goofish.com/h5/{api}/{version}/`
- 通用参数：`appKey=34839810`，`t=毫秒时间戳`，`accountSite=xianyu`，
  `sessionOption=AutoLoginOnly`，POST 表单 `data=<JSON字符串>`
- **签名**：`sign = md5("{token}&{t}&{appKey}&{data}")`
- 令牌过期：ret 含 `FAIL_SYS_TOKEN_EXOIRED`（闲鱼历史拼写）/`EXPIRED`/`EMPTY`
  → 从响应 Set-Cookie 取新 `_m_h5_tk` 合并重试
- 风控：ret 含 `FAIL_SYS_USER_VALIDATE`/`RGV587`/`punish` → data.url 为验证链接，
  需人工过滑块后换 Cookie，**不要自动重试**

## 3. 私信长连接（WebSocket）

- URL：`wss://wss-goofish.dingtalk.com/`
- IM token：`mtop.taobao.idlemessage.pc.login.token`，
  data=`{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"<did>"}`，
  返回 data.accessToken
- 注册：`{"lwp":"/reg","headers":{"app-key":"444e9908a51d1cb236a27862abc769c9","token":<accessToken>,"did":<deviceId>,"sync":"0,0;0;0;", …}}`
- 同步位点：`{"lwp":"/r/SyncStatus/ackDiff","body":[{pipeline:sync, pts:<now*1000>, …}]}`
- 心跳：每 15s 发 `{"lwp":"/!","headers":{"mid":…}}`；响应为无 body 的 `code:200`
  （30s 无响应应重连）
- 收消息：`body.syncPushPackage.data[]` 每项 `data` 字段 = 明文 JSON **或**
  base64(MessagePack)；解出后为数字字符串 key 的消息体：
  - 聊天：`["1"]["10"].reminderContent/senderUserId/senderNick`；会话 `["1"]["2"]`
    （`xxx@goofish`）；时间 `["1"]["5"]`；真实载荷 `["1"]["6"]["3"]["5"]`
    （JSON 串：`{"contentType":1,"text":{"text":…}}`）
  - 卡片：`["1"]["1"]["1"]` 发送者，`["1"]["6"]["3"]["2"]` 文本，
    卡片 JSON 在 `["1"]["6"]["3"]["5"]`
  - 商品 ID：`["1"]["10"].reminderUrl`（`itemId=`）/`bizTag`/`extJson`
- 发消息：`{"lwp":"/r/MessageSend/sendByReceiverScope","body":[
  {"cid":"<chat>@goofish","content":{"contentType":101,"custom":{"type":1,"data":<base64>}},…},
  {"actualReceivers":["<to>@goofish","<me>@goofish"]}]}`

## 4. 业务接口（mtop，本项目用到的）

| 用途 | api | data 要点 |
|---|---|---|
| 查价（PC 搜索） | `mtop.taobao.idlemtopsearch.pc.search` | keyword/rowsPerPage/`propValueStr.searchFilter`（`quickFilter:filterPersonal;`、`priceRange:lo,hi;`）。实测：clickParam 挂在 `data.item.main`（不在 exContent），item_id 在 `clickParam.args.item_id`，想要人数兜底 `args.wantNum` |
| 个人版在售列表 | `mtop.idle.web.xyh.item.list` | **groupId 必须带魔数 '58877261' + defaultGroup:true**（空串→稳定空返回）；**pageSize 上限 20，超限静默返回空**；偶发空返回重试即可。普通账号可用 ✅ |
| 卖家平台商品列表 | `mtop.alibaba.idle.seller.pc.common.item.search` | referer 须带 `?site=COMMONPRO`。未开鱼小铺时 ret 可能 SUCCESS 但列表空（软失败） |
| 改价/改库存 | `mtop.alibaba.idle.seller.pc.item.info.update` | `{"itemId", "quantity", "price"(元)}`；响应 `data.code=="success"`。**需鱼小铺**：未开通返回 `permissionCode: item:info:update, 用户无权限访问当前站点[COMMONPRO]`（2026-09 实测） |
| 批量下架 | `mtop.alibaba.idle.seller.pc.item.batch.offline` | `{"itemIds":"id1,id2"}`。需鱼小铺（同上） |
| 已卖出订单 | `mtop.taobao.idle.trade.merchant.sold.get` | `queryCode: ALL|NOT_SHIP`。**2026-09 实测部分 Cookie 返回 PERMISSION_EXCEPTION**（卖家后台授权），此时订单事件依赖 WS 付款卡片 + order.detail 兜底 |
| 订单详情 | `mtop.idle.web.trade.order.detail` | `{"orderId":…}` |
| **确认交易（免邮寄发货）** | `mtop.taobao.idle.logistic.consign.dummy` | `{"orderId":…,"tradeText":"","picList":[],"newUnconsign":true}`（trade 域，按参考项目经验无需鱼小铺；未实测——需要真订单） |
| 小刀单免拼 | `mtop.idle.groupon.activity.seller.freeshipping` | `{"bizOrderId":…,"itemId":…,"buyerId":…}` |
| 评价 | `mtop.taobao.idle.rate.create/4.0` | `{"tradeId", "rate":1, "feedback", "createOrAppend":0}`（权限未实测） |

搜索结果项层级多变：`data.item.main.exContent` / `item.main` / `main` 逐级兜底；
价格在 `main.price[]`（`[{text:"¥"},{text:"1,299"}]`），想要人数在
`main.fishTags`（`"12人想要"`），item_id 在 `main.clickParam.args`。

## 5. 已付款事件识别

系统卡片文本命中 `[买家已付款]` / `[我已付款，等待你发货]` /
`[已付款，待发货]` / `[我已付款，请添加自提信息]` 即视为已付款；
订单号从卡片载荷 JSON 递归提取（orderId/bizOrderId）。

## 6. 未纳入 v1 的（已探明，留扩展）

- 自动上下架/改价：`mtop.alibaba.idle.seller.pc.item.*`
- 自动评价：`mtop.taobao.idle.rate.create`
- 关单：`mtop.taobao.idle.trade.merchant.close.by.seller`
- 发图：走消息 custom contentType + CDN 上传链
