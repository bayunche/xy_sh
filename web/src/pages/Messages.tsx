import * as React from "react";
import { Card, CardHeader, CardTitle, CardDesc, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, THead, TBody } from "@/components/ui/table";
import { Dialog, Empty, Spinner } from "@/components/ui/misc";
import { getEvents, getHistory } from "@/lib/api";
import { fmtTime, cn } from "@/lib/utils";
import { RefreshCw, MessagesSquare } from "lucide-react";

const KINDS = ["全部", "chat", "chat_skipped", "order_paid", "confirm", "reprice", "snipe_hit", "admin_chat"];

export default function Messages() {
  const [events, setEvents] = React.useState<any[]>([]);
  const [kind, setKind] = React.useState("全部");
  const [loading, setLoading] = React.useState(false);
  const [hist, setHist] = React.useState<{ chat: string; msgs: any[] } | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try { setEvents((await getEvents(200) as any[]) || []); } finally { setLoading(false); }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const filtered = kind === "全部" ? events : events.filter((e: any) =>
    e.kind === kind || e.kind?.replace(":", ".").startsWith(kind) || e.kind.includes(kind));

  const openHistory = async (chatId: string) => {
    const r: any = await getHistory(chatId, 40);
    setHist({ chat: chatId, msgs: r.messages || [] });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">消息与事件</h1>
          <p className="mt-1 text-sm text-mutedfg">全部机器人事件流；点带会话的事件可查看聊天上下文</p>
        </div>
        <Button variant="secondary" size="sm" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={14} />} 刷新</Button>
      </div>

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

      <Dialog open={!!hist} onClose={() => setHist(null)} title={`会话 ${hist?.chat} 聊天史`} wide>
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
