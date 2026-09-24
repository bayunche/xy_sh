import * as React from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Spinner } from "@/components/ui/misc";
import { getChatHistory, postChat } from "@/lib/api";
import { Bot, SendHorizonal, User } from "lucide-react";
import { cn } from "@/lib/utils";

export default function Chat() {
  const [messages, setMessages] = React.useState<any[]>([]);
  const [input, setInput] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const bottomRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    getChatHistory().then((r: any) => setMessages(r.messages || [])).catch(() => {});
  }, []);
  React.useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const r = await postChat(text);
      setMessages((m) => [...m, { role: "assistant", text: r.reply || r.error || "(空回复)" }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", text: `请求失败：${e?.message ?? e}` }]);
    } finally { setBusy(false); }
  };

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col space-y-4">
      <div>
        <h1 className="text-xl font-bold">对话 dsh</h1>
        <p className="mt-1 text-sm text-mutedfg">
          直接和机器人的大脑（DeepSeek Harness）对话：查状态、跑操作、问建议。它和你共用同一套 CLI 与风控闸门。
        </p>
      </div>

      <Card className="flex flex-1 flex-col overflow-hidden">
        <div className="flex-1 space-y-4 overflow-auto p-6">
          {messages.length === 0 && !busy && (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-mutedfg">
              <Bot size={36} className="opacity-40" />
              <p className="text-sm">试试：「现在机器人什么状态？」「把 B98 的底价策略讲一下」「最近有什么盯货命中？」</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={cn("flex gap-3", m.role === "user" && "flex-row-reverse")}>
              <div className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                m.role === "user" ? "bg-slate-800 text-white" : "bg-primary text-primary-fg"
              )}>
                {m.role === "user" ? <User size={15} /> : <Bot size={15} />}
              </div>
              <div className={cn(
                "max-w-[75%] whitespace-pre-wrap rounded-xl px-4 py-2.5 text-sm leading-relaxed shadow-soft",
                m.role === "user" ? "bg-slate-800 text-white" : "border border-border bg-white"
              )}>{m.text}</div>
            </div>
          ))}
          {busy && (
            <div className="flex gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-fg"><Bot size={15} /></div>
              <div className="flex items-center gap-2 rounded-xl border border-border bg-white px-4 py-2.5 text-sm text-mutedfg shadow-soft">
                <Spinner /> dsh 思考中（一次 headless 会话，约 10–60 秒）…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        <div className="border-t border-border p-4">
          <div className="flex gap-2">
            <Textarea
              value={input}
              placeholder="输入给管理助手的话…（Ctrl+Enter 发送）"
              className="min-h-[52px]"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); }}
            />
            <Button className="h-auto px-4" disabled={busy || !input.trim()} onClick={send}>
              <SendHorizonal size={15} /> 发送
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
