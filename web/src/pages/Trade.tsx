import * as React from "react";
import { Card, CardHeader, CardTitle, CardDesc, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/misc";
import { Table, THead, TBody } from "@/components/ui/table";
import { Empty, Spinner } from "@/components/ui/misc";
import { getOrders, getConfirms, postConfirm, postConfirmCheck, postSend } from "@/lib/api";
import { fmtTime } from "@/lib/utils";
import { RefreshCw, ShieldCheck, SendHorizonal } from "lucide-react";

export default function Trade() {
  const [orders, setOrders] = React.useState<any[]>([]);
  const [confirms, setConfirms] = React.useState<any[]>([]);
  const [q, setQ] = React.useState<"NOT_SHIP" | "ALL">("NOT_SHIP");
  const [loading, setLoading] = React.useState(false);

  const [ck, setCk] = React.useState({ order_id: "", item_id: "", buyer_id: "", amount: "" });
  const [ckResult, setCkResult] = React.useState<any>(null);
  const [busy, setBusy] = React.useState(false);

  const [send, setSend] = React.useState({ chat_id: "", to_user_id: "", text: "" });
  const [sendMsg, setSendMsg] = React.useState("");

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const o: any = await getOrders(q);
      setOrders(o.orders || []);
      const c: any = await getConfirms();
      setConfirms(c || []);
    } finally { setLoading(false); }
  }, [q]);
  React.useEffect(() => { load(); }, [load]);

  const check = async () => {
    setBusy(true); setCkResult(null);
    try {
      setCkResult(await postConfirmCheck({
        order_id: ck.order_id, item_id: ck.item_id, buyer_id: ck.buyer_id,
        amount: ck.amount ? Number(ck.amount) : undefined,
      }));
    } finally { setBusy(false); }
  };
  const confirmIt = async () => {
    setBusy(true);
    try {
      setCkResult(await postConfirm({
        order_id: ck.order_id, item_id: ck.item_id, buyer_id: ck.buyer_id,
        amount: ck.amount ? Number(ck.amount) : undefined, source: "后台手动",
      }));
      load();
    } finally { setBusy(false); }
  };

  const doSend = async () => {
    setBusy(true); setSendMsg("");
    try {
      const r: any = await postSend(send.chat_id, send.to_user_id, send.text);
      setSendMsg(r.simulated ? "[dry-run] 模拟发送成功" : r.success ? "已发送" : `失败：${r.error}`);
    } finally { setBusy(false); }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">交易</h1>
          <p className="mt-1 text-sm text-mutedfg">待发货订单、确认交易台账与手动操作（全部走闸门）</p>
        </div>
        <div className="flex gap-2">
          <Button variant={q === "NOT_SHIP" ? "default" : "secondary"} size="sm" onClick={() => setQ("NOT_SHIP")}>待发货</Button>
          <Button variant={q === "ALL" ? "default" : "secondary"} size="sm" onClick={() => setQ("ALL")}>全部</Button>
          <Button variant="secondary" size="sm" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={14} />}</Button>
        </div>
      </div>

      <Card>
        <CardHeader><CardTitle>订单（{orders.length}）</CardTitle><CardDesc>来自闲鱼卖家接口</CardDesc></CardHeader>
        <CardContent className="p-0">
          {orders.length === 0 ? <Empty /> : (
            <Table>
              <THead><tr><th>订单号</th><th>商品</th><th>买家</th><th>金额</th><th>状态</th></tr></THead>
              <TBody>
                {orders.map((o: any, i: number) => (
                  <tr key={o.order_id || i}>
                    <td className="text-xs">{o.order_id}</td>
                    <td className="max-w-xs truncate text-xs">{o.title || o.item_id}</td>
                    <td className="text-xs">{o.buyer_id}</td>
                    <td className="text-sm font-medium">{o.price != null ? `¥${o.price}` : "-"}</td>
                    <td><Badge tone="info">{o.status_text || "-"}</Badge></td>
                  </tr>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>手动确认交易</CardTitle>
            <CardDesc>先预检（confirm-check）看闸门意见，再执行（confirm）</CardDesc>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="订单号"><Input value={ck.order_id} onChange={(e) => setCk({ ...ck, order_id: e.target.value })} /></Field>
              <Field label="金额（元）"><Input value={ck.amount} onChange={(e) => setCk({ ...ck, amount: e.target.value })} type="number" /></Field>
              <Field label="商品 ID（可选）"><Input value={ck.item_id} onChange={(e) => setCk({ ...ck, item_id: e.target.value })} /></Field>
              <Field label="买家 ID（可选）"><Input value={ck.buyer_id} onChange={(e) => setCk({ ...ck, buyer_id: e.target.value })} /></Field>
            </div>
            {ckResult && (
              <div className={ckResult.action === "block" ? "rounded-lg bg-red-50 p-3 text-xs text-red-700"
                : "rounded-lg bg-emerald-50 p-3 text-xs text-emerald-700"}>
                闸门：{ckResult.action} · {(ckResult.reasons || []).join("；")}
                {ckResult.result && <div className="mt-1 opacity-80">{ckResult.result}</div>}
              </div>
            )}
            <div className="flex gap-2">
              <Button variant="secondary" onClick={check} disabled={busy || !ck.order_id}>
                <ShieldCheck size={14} /> 预检
              </Button>
              <Button onClick={confirmIt} disabled={busy || !ck.order_id}>
                {busy ? <Spinner size={14} /> : null} 确认交易
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>给买家发消息</CardTitle>
            <CardDesc>唯一人工发消息出口（dry-run 下模拟）</CardDesc>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="会话 chat_id"><Input value={send.chat_id} onChange={(e) => setSend({ ...send, chat_id: e.target.value })} /></Field>
              <Field label="买家 ID"><Input value={send.to_user_id} onChange={(e) => setSend({ ...send, to_user_id: e.target.value })} /></Field>
            </div>
            <Field label="内容"><Textarea value={send.text} onChange={(e) => setSend({ ...send, text: e.target.value })} /></Field>
            {sendMsg && <div className="rounded-lg bg-muted p-2.5 text-xs">{sendMsg}</div>}
            <Button onClick={doSend} disabled={busy || !send.chat_id || !send.text || !send.to_user_id}>
              <SendHorizonal size={14} /> 发送
            </Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>确认交易台账（{confirms.length}）</CardTitle></CardHeader>
        <CardContent className="p-0">
          {confirms.length === 0 ? <Empty /> : (
            <Table>
              <THead><tr><th>时间</th><th>订单</th><th>金额</th><th>决策</th><th>结果</th><th>模拟</th></tr></THead>
              <TBody>
                {confirms.map((c: any, i: number) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap text-xs text-mutedfg">{fmtTime(c.ts)}</td>
                    <td className="text-xs">{c.order_id}</td>
                    <td className="text-xs">{c.amount != null ? `¥${c.amount}` : "-"}</td>
                    <td><Badge tone={c.decision === "allow" ? "success" : c.decision === "block" ? "danger" : "warn"}>{c.decision}</Badge></td>
                    <td className="max-w-md truncate text-xs">{c.result}</td>
                    <td className="text-xs">{c.simulated ? "是" : "否"}</td>
                  </tr>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
