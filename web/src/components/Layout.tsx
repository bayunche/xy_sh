import * as React from "react";
import { NavLink, Outlet } from "react-router-dom";
import { cn } from "@/lib/utils";
import { getStatus } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import {
  LayoutDashboard, MessagesSquare, Fish, Package, ReceiptText, Radar,
  Settings, Bot, RefreshCw,
} from "lucide-react";

const NAV = [
  { to: "/", label: "仪表盘", icon: LayoutDashboard },
  { to: "/chat", label: "对话 dsh", icon: Bot },
  { to: "/messages", label: "消息事件", icon: MessagesSquare },
  { to: "/listings", label: "商品 · 查价", icon: Package },
  { to: "/trade", label: "交易", icon: ReceiptText },
  { to: "/snipe", label: "盯货捡漏", icon: Radar },
  { to: "/settings", label: "设置", icon: Settings },
];

export function useStatus(pollMs = 10000) {
  const [status, setStatus] = React.useState<any>(null);
  const refresh = React.useCallback(() => getStatus().then(setStatus).catch(() => setStatus({ error: "连接失败" })), []);
  React.useEffect(() => { refresh(); const t = setInterval(refresh, pollMs); return () => clearInterval(t); }, [pollMs]);
  return { status, refresh };
}

export default function Layout() {
  const { status } = useStatus();
  const live = status?.mode === "live";
  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 flex w-56 flex-col border-r border-border bg-white">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-fg">
            <Fish size={19} />
          </div>
          <div>
            <div className="text-sm font-bold leading-tight">闲鱼卖家机器人</div>
            <div className="text-[11px] text-mutedfg">xy-dsh-robot</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to} to={to} end={to === "/"}
              className={({ isActive }) => cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                isActive ? "bg-primary/10 font-medium text-primary" : "text-mutedfg hover:bg-muted hover:text-foreground"
              )}
            >
              <Icon size={16} /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-border p-4 text-[11px] leading-relaxed text-mutedfg">
          <div className="mb-1.5 flex items-center gap-1.5">
            <span className={cn("h-2 w-2 rounded-full", status?.ws_connected ? "bg-emerald-500 animate-pulse" : "bg-red-400")} />
            {status?.ws_connected ? "私信长连接在线" : "长连接离线"}
          </div>
          <div>账号 unb：{status?.user_id ?? "…"}</div>
        </div>
      </aside>

      <div className="ml-56 flex min-h-screen flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-border bg-white/80 px-8 backdrop-blur">
          <div className="flex items-center gap-3 text-sm text-mutedfg">
            <RefreshCw size={13} className="text-mutedfg/60" />
            机器人控制台
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={live ? "danger" : "warn"}>{live ? "LIVE 实盘" : "DRY-RUN 演练"}</Badge>
            <Badge tone={status?.auto_confirm_enabled ? "success" : "default"}>自动确认 {status?.auto_confirm_enabled ? "开" : "关"}</Badge>
            <Badge tone={status?.reprice_enabled ? "success" : "default"}>自动改价 {status?.reprice_enabled ? "开" : "关"}</Badge>
          </div>
        </header>
        <main className="flex-1 p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
