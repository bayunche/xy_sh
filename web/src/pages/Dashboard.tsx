import * as React from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDesc } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, THead, TBody } from "@/components/ui/table";
import { Spinner, Dialog, Empty } from "@/components/ui/misc";
import { useStatus } from "@/components/Layout";
import { getEvents, getConfirms, getCapability, postAudit, postSnipeRun } from "@/lib/api";
import { fmtTime, fmtDur } from "@/lib/utils";
import { Activity, Radar, ClipboardCheck, ShieldCheck, Wifi, Timer, Bot } from "lucide-react";

const KIND_TONE: Record<string, any> = {
  chat: "info", brain_on_message_md: "purple", brain_on_order_paid_md: "purple",
  order_paid: "success", confirm: "warn", snipe_hit: "purple", admin_chat: "info",
  reply_simulated: "default", reply_sent: "success", chat_skipped: "warn",
  reprice: "warn", brain_daily_audit_md: "purple",
};

export default function Dashboard() {
  const { status, refresh } = useStatus();
  const [events, setEvents] = React.useState<any[]>([]);
  const [confirms, setConfirms] = React.useState<any[]>([]);
  const [cap, setCap] = React.useState<any>(null);
  const [capOpen, setCapOpen] = React.useState(false);
  const [busy, setBusy] = React.useState("");

  const load = React.useCallback(() => {
    getEvents(14).then((r: any) => setEvents(r as any[] || [])).catch(() => {});
    getConfirms().then((r: any) => setConfirms(r || [])).catch(() => {});
  }, []);
  React.useEffect(load, [load]);
  React.useEffect(() => { const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const run = async (name: string, fn: () => Promise<any>) => {
    setBusy(name);
    try { await fn(); load(); refresh(); } finally { setBusy(""); }
  };

  const todayEvents = (events as any[]).length;
  const tiles = [
    { label: "运行模式", value: status?.mode === "live" ? "LIVE" : "DRY-RUN", icon: ShieldCheck, tone: status?.mode === "live" ? "danger" : "warn" },
    { label: "私信连接", value: status?.ws_connected ? "在线" : "离线", icon: Wifi, tone: status?.ws_connected ? "success" : "danger" },
    { label: "运行时长", value: fmtDur(status?.uptime_sec), icon: Timer, tone: "default" },
    { label: "确认台账", value: `${confirms.length} 笔`, icon: ClipboardCheck, tone: "info" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">仪表盘</h1>
        <p className="mt-1 text-sm text-mutedfg">机器人整体状态与最近事件（15 秒自动刷新）</p>
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {tiles.map(({ label, value, icon: Icon, tone }) => (
          <Card key={label} className="p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-xs text-mutedfg">{label}</div>
                <div className="mt-1.5 text-xl font-bold">{value}</div>
              </div>
              <div className="rounded-lg bg-muted p-2"><Icon size={16} className="text-mutedfg" /></div>
            </div>
            <Badge tone={tone as any} className="mt-3">{label === "运行模式" ? (status?.mode === "live" ? "写操作真实生效" : "写操作仅模拟") : "实时"}</Badge>
          </Card>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" disabled={!!busy} onClick={() => run("audit", postAudit)}>
          {busy === "audit" ? <Spinner /> : <Activity size={14} />} 立即跑每日审计
        </Button>
        <Button variant="secondary" size="sm" disabled={!!busy} onClick={() => run("snipe", postSnipeRun)}>
          {busy === "snipe" ? <Spinner /> : <Radar size={14} />} 立即盯货扫描
        </Button>
        <Button variant="outline" size="sm" disabled={!!busy}
          onClick={() => run("cap", async () => { setCap(await getCapability()); setCapOpen(true); })}>
          {busy === "cap" ? <Spinner /> : <ShieldCheck size={14} />} 接口能力探测
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>最近事件</CardTitle>
          <CardDesc>消息 / 订单 / 大脑 job / 改价 / 盯货 · 共 {todayEvents} 条（近窗）</CardDesc>
        </CardHeader>
        <CardContent className="p-0">
          {events.length === 0 ? <Empty /> : (
            <Table>
              <THead><tr><th>时间</th><th>类型</th><th>会话/商品</th><th>摘要</th><th>大脑</th></tr></THead>
              <TBody>
                {(events as any[]).map((e: any) => (
                  <tr key={e.id} className="align-top">
                    <td className="whitespace-nowrap text-xs text-mutedfg">{fmtTime(e.ts)}</td>
                    <td><Badge tone={KIND_TONE[e.kind] ?? "default"}>{e.kind}</Badge></td>
                    <td className="max-w-[120px] truncate text-xs">{e.chat_id || e.item_id || "-"}</td>
                    <td className="max-w-[420px] text-xs">{e.summary}</td>
                    <td className="text-xs">{e.brain_status === "ok" ? <Badge tone="success">OK</Badge> : e.brain_status || "-"}</td>
                  </tr>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={capOpen} onClose={() => setCapOpen(false)} title="接口能力探测">
        <div className="space-y-2">
          {cap?.capabilities && Object.entries(cap.capabilities).map(([k, v]: any) => (
            <div key={k} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <div>
                <div className="text-sm font-medium">{k}</div>
                <div className="text-[11px] text-mutedfg">{Array.isArray(v.detail) ? v.detail.join(" ") : v.detail || (v.ok ? "OK" : "")}</div>
              </div>
              <Badge tone={v.ok ? "success" : v.needs_fish_shop ? "warn" : "danger"}>{v.ok ? "可用" : v.needs_fish_shop ? "需鱼小铺" : "不可用"}</Badge>
            </div>
          ))}
        </div>
      </Dialog>
    </div>
  );
}
