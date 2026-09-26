import * as React from "react";
import { Card, CardHeader, CardTitle, CardDesc, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, THead, TBody } from "@/components/ui/table";
import { Dialog, Empty, Spinner } from "@/components/ui/misc";
import { getEvents, getHistory, getConversations, markConversationRead } from "@/lib/api";
import { fmtTime, cn } from "@/lib/utils";
import { RefreshCw, MessagesSquare, User, ArrowUpRight, ArrowDownLeft } from "lucide-react";

const KINDS = ["全部", "chat", "chat_skipped", "order_paid", "confirm", "reprice", "snipe_hit", "admin_chat"];

export default function Messages() {
  const [events, setEvents] = React.useState<any[]>([]);
  const [convs, setConvs] = React.useState<any[]>([]);
  const [kind, setKind] = React.useState("全部");
  const [loading, setLoading] = React.useState(false);
  const [hist, setHist] = React.useState<{ chat: string; msgs: any[] } | null>(null);
  const [selChat, setSelChat] = React.useState("");

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const [evs, cs] = await Promise.all([getEvents(200), getConversations() as any]);
      setEvents((evs as any[]) || []);
      setConvs(cs?.conversations || []);
    } finally { setLoading(false); }
  }, []);
  React.useEffect(() => {
    load();
    const t = setInterval(load, 15000);   // 15s 轮询：新消息/未读自动刷新
    return () => clearInterval(t);
  }, [load]);

  const filtered = kind === "全部" ? events : events.filter((e: any) =>
    e.kind === kind || e.kind?.replace(":", ".").startsWith(kind) || e.kind.includes(kind));

  const openHistory = async (chatId: string, buyerName?: string) => {
    const r: any = await getHistory(chatId, 60);
    setHist({ chat: chatId, msgs: r.messages || [] });
    setSelChat(buyerName || chatId);
    await markConversationRead(chatId);
    setConvs((prev) => prev.map((c: any) => c.chat_id === chatId ? { ...c, unread: 0 } : c));
  };

  const cur = convs.find((c: any) => c.chat_id === hist?.chat);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">消息</h1>
          <p className="mt-1 text-sm text-mutedfg">按买家分组的会话（未读高亮）；下方为机器人事件流</p>
        </div>
        <Button variant="secondary" size="sm" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={14} />} 刷新</Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>买家会话（{convs.length}）</CardTitle>
          <CardDesc>点开查看完整聊天记录并标记已读；未读 = 客户发来尚未查看的消息</CardDesc>
        </CardHeader>
        <CardContent className="p-0">
          {convs.length === 0 ? <Empty>还没有买家消息（登录闲鱼后自动接收）</Empty> : (
            <div className="divide-y divide-border">
              {convs.map((c: any) => (
                <button key={c.chat_id}
                  onClick={() => openHistory(c.chat_id, c.buyer_name)}
                  className={cn("flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/60",
                    c.unread > 0 && "bg-primary/5")}>
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted ring-1 ring-border">
                    <User size={16} className="text-mutedfg" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={cn("truncate text-sm", c.unread > 0 ? "font-bold" : "font-medium")}>
                        {c.buyer_name || c.chat_id}
                      </span>
                      {c.unread > 0 && <Badge tone="danger">{c.unread} 未读</Badge>}
                      <span className="ml-auto shrink-0 text-[10px] text-mutedfg">{fmtTime(c.last_ts)}</span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1 text-xs text-mutedfg">
                      {c.last_direction === "out"
                        ? <ArrowUpRight size={12} className="shrink-0 text-primary" />
                        : <ArrowDownLeft size={12} className="shrink-0" />}
                      <span className="truncate">{c.last_msg || "（无文本）"}</span>
                      <span className="ml-auto shrink-0">共 {c.count} 条</span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-1.5">
        {KINDS.map((k) => (
          <button key={k} onClick={() => setKind(k)}
            className={cn("rounded-full px-3 py-1 text-xs font-medium transition-colors",
              kind === k ? "bg-primary text-primary-fg" : "bg-white text-mutedfg ring-1 ring-border hover:bg-muted")}>
            {k}
          </button>
        ))}
      </div>

      <Card>
        <CardHeader><CardTitle>事件流（最近 {filtered.length} 条）</CardTitle></CardHeader>
        <CardContent className="p-0">
          {filtered.length === 0 ? <Empty /> : (
            <Table>
              <THead><tr><th>时间</th><th>类型</th><th>会话</th><th>摘要</th><th>大脑</th><th></th></tr></THead>
              <TBody>
                {filtered.map((e: any) => (
                  <tr key={e.id} className="align-top">
                    <td className="whitespace-nowrap text-xs text-mutedfg">{fmtTime(e.ts)}</td>
                    <td><Badge tone="info">{e.kind}</Badge></td>
                    <td className="text-xs">{e.chat_id || "-"}</td>
                    <td className="max-w-[440px] whitespace-pre-wrap text-xs">{e.summary}</td>
                    <td className="text-xs">{e.brain_status || "-"}</td>
                    <td>{e.chat_id && (
                      <Button variant="ghost" size="sm" onClick={() => openHistory(e.chat_id)}>
                        <MessagesSquare size={13} /> 聊天史
                      </Button>
                    )}</td>
                  </tr>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!hist} onClose={() => setHist(null)}
        title={`与 ${selChat || hist?.chat} 的聊天记录${cur?.buyer_id ? `（ID: ${cur.buyer_id}）` : ""}`} wide>
        <div className="space-y-2.5">
          {hist?.msgs.length === 0 && <Empty>暂无消息</Empty>}
          {hist?.msgs.map((m: any, i: number) => (
            <div key={i} className={cn("flex", m.direction === "out" ? "justify-end" : "justify-start")}>
              <div className={cn("max-w-[80%] rounded-xl px-3.5 py-2 text-sm",
                m.direction === "out" ? "bg-primary/10 text-primary" : "border border-border bg-muted/60")}>
                <div className="text-[10px] text-mutedfg">{m.direction === "out" ? "机器人" : m.sender} · {fmtTime(m.ts)}</div>
                <div className="mt-0.5 whitespace-pre-wrap">{m.text}</div>
              </div>
            </div>
          ))}
        </div>
      </Dialog>
    </div>
  );
}
