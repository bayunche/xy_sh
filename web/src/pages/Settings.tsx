import * as React from "react";
import { Card, CardHeader, CardTitle, CardDesc, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Select, Label } from "@/components/ui/input";
import { Field } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { Spinner, ErrorText } from "@/components/ui/misc";
import { getSettings, putSettings, getDshKey, putDshKey, getAccount, putAccount, captureStart, captureStatus, captureStop, postHotelCost, getHotelAppState } from "@/lib/api";
import { Save, KeyRound, Cookie, Plus, Trash2, ScanLine, Hotel, Smartphone } from "lucide-react";

function Section({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle>{desc && <CardDesc>{desc}</CardDesc>}</CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  );
}

export default function Settings() {
  const [s, setS] = React.useState<any>(null);
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState("");
  const [err, setErr] = React.useState("");

  const [apiKey, setApiKey] = React.useState("");
  const [keyInfo, setKeyInfo] = React.useState<any>(null);
  const [keyMsg, setKeyMsg] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [model, setModel] = React.useState("");
  const [hcost, setHcost] = React.useState({ hotel: "", price: "" });
  const [hcostMsg, setHcostMsg] = React.useState("");
  const [appState, setAppState] = React.useState<any>(null);
  const [appChecking, setAppChecking] = React.useState(false);
  const [acct, setAcct] = React.useState<any>(null);
  const [cookie, setCookie] = React.useState("");
  const [cookieMsg, setCookieMsg] = React.useState("");
  const [cap, setCap] = React.useState<any>({ state: "idle" });

  React.useEffect(() => {
    getSettings().then(setS).catch((e) => setErr(String(e)));
    getDshKey().then(setKeyInfo).catch(() => {});
    getAccount().then(setAcct).catch(() => {});
  }, []);

  // 抓取会话轮询：running 期间每 2s 拉状态，success 时刷新账号信息
  React.useEffect(() => {
    if (cap.state !== "running") return;
    const t = setInterval(async () => {
      const st = await captureStatus().catch(() => null);
      if (!st) return;
      setCap(st);
      if (st.state !== "running") getAccount().then(setAcct).catch(() => {});
    }, 2000);
    return () => clearInterval(t);
  }, [cap.state]);

  const startCapture = async () => {
    const r = await captureStart();
    if (r.error) { setCap({ state: "error", message: r.error }); return; }
    setCap(await captureStatus());
  };
  const stopCapture = async () => {
    await captureStop();
    setCap({ state: "idle" });
  };

  if (!s) return <div className="flex justify-center py-20"><Spinner className="h-6 w-6" /></div>;

  const upd = (path: string, v: any) => setS((prev: any) => {
    const next = structuredClone(prev);
    const keys = path.split(".");
    let cur = next;
    for (let i = 0; i < keys.length - 1; i++) cur = cur[keys[i]];
    cur[keys[keys.length - 1]] = v;
    return next;
  });

  const save = async () => {
    setSaving(true); setSaved(""); setErr("");
    try {
      const r: any = await putSettings(s);
      if (r.error) setErr(r.error); else setSaved(`已保存并热重载（${r.saved}）`);
    } catch (e: any) { setErr(String(e)); } finally { setSaving(false); }
  };

  const saveKey = async () => {
    setKeyMsg("");
    const r: any = await putDshKey(apiKey, baseUrl, model);
    setKeyMsg(r.error || `已写入 ${r.home}（新配置对下一次大脑 job 生效）`);
    setApiKey(""); setBaseUrl(""); setModel("");
    setKeyInfo(await getDshKey());
  };
  const saveCookie = async () => {
    setCookieMsg("");
    const r: any = await putAccount(cookie);
    setCookieMsg(r.error || r.note || "已保存");
  };

  const ac = s.auto_confirm, rp = s.reprice, sn = s.snipe, rep = s.reply;

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">设置</h1>
          <p className="mt-1 text-sm text-mutedfg">保存即写回 robot.yaml 并热重载（连接与登录态不受影响）</p>
        </div>
        <Button onClick={save} disabled={saving}>{saving ? <Spinner /> : <Save size={14} />} 保存全部</Button>
      </div>
      {saved && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{saved}</div>}
      <ErrorText>{err}</ErrorText>

      <Section title="运行模式" desc="dry-run：一切写操作（发消息/改价/确认）只记台账；live：真实生效">
        <Field label="模式">
          <Select value={s.mode} onChange={(e) => upd("mode", e.target.value)} className="w-48">
            <option value="dry-run">dry-run（演练）</option>
            <option value="live">live（实盘，谨慎）</option>
          </Select>
        </Field>
      </Section>

      <Section title="自动确认交易（资金）" desc="买家付款后自动确认发货；七道闸门的可调项">
        <div className="flex items-center justify-between">
          <Label>启用自动确认</Label>
          <Switch checked={ac.enabled} onCheckedChange={(v) => upd("auto_confirm.enabled", v)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="单笔金额上限（元）">
            <Input type="number" value={ac.max_amount_cny} onChange={(e) => upd("auto_confirm.max_amount_cny", Number(e.target.value))} />
          </Field>
          <Field label="同订单冷却（分钟）">
            <Input type="number" value={ac.cooldown_minutes} onChange={(e) => upd("auto_confirm.cooldown_minutes", Number(e.target.value))} />
          </Field>
        </div>
        <Field label="商品白名单（逗号分隔，空=不限）">
          <Input value={ac.item_whitelist.join(",")} onChange={(e) => upd("auto_confirm.item_whitelist", e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean))} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="商品黑名单（逗号分隔）">
            <Input value={ac.item_blacklist.join(",")} onChange={(e) => upd("auto_confirm.item_blacklist", e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean))} />
          </Field>
          <Field label="买家黑名单（逗号分隔）">
            <Input value={ac.buyer_blacklist.join(",")} onChange={(e) => upd("auto_confirm.buyer_blacklist", e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean))} />
          </Field>
        </div>
      </Section>

      <Section title="自动改价（议价成交）" desc="只降不涨、不破底价、限频次">
        <div className="flex items-center justify-between">
          <Label>启用自动改价</Label>
          <Switch checked={rp.enabled} onCheckedChange={(v) => upd("reprice.enabled", v)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="底价系数（挂价×）">
            <Input type="number" step="0.01" value={rp.floor_ratio} onChange={(e) => upd("reprice.floor_ratio", Number(e.target.value))} />
          </Field>
          <Field label="单次最大降幅 %">
            <Input type="number" value={rp.max_drop_pct} onChange={(e) => upd("reprice.max_drop_pct", Number(e.target.value))} />
          </Field>
          <Field label="每日次数上限">
            <Input type="number" value={rp.max_per_day} onChange={(e) => upd("reprice.max_per_day", Number(e.target.value))} />
          </Field>
          <Field label="同商品间隔（分钟）">
            <Input type="number" value={rp.min_interval_min} onChange={(e) => upd("reprice.min_interval_min", Number(e.target.value))} />
          </Field>
        </div>
      </Section>

      <Section title="自动回复">
        <div className="grid grid-cols-3 gap-3">
          <Field label="静默开始（如 23:30，留空关闭）">
            <Input value={rep.quiet_start || ""} onChange={(e) => upd("reply.quiet_start", e.target.value)} placeholder="23:30" />
          </Field>
          <Field label="静默结束">
            <Input value={rep.quiet_end || ""} onChange={(e) => upd("reply.quiet_end", e.target.value)} placeholder="08:00" />
          </Field>
          <Field label="每小时回复上限">
            <Input type="number" value={rep.max_per_hour} onChange={(e) => upd("reply.max_per_hour", Number(e.target.value))} />
          </Field>
        </div>
        <Field label="关键词快答（每行一条：关键词1,关键词2 => 回复文案）">
          <Textarea
            className="min-h-[90px] font-mono text-xs"
            value={(rep.keyword_rules || []).map((r: any) => `${(r.match || []).join(",")} => ${r.reply}`).join("\n")}
            onChange={(e) => upd("reply.keyword_rules", e.target.value.split("\n").filter(Boolean).map((line: string) => {
              const [k, v] = line.split("=>");
              return { match: (k || "").split(",").map((x) => x.trim()).filter(Boolean), reply: (v || "").trim() };
            }).filter((r: any) => r.match.length && r.reply))}
          />
        </Field>
      </Section>

      <Section title="盯货订阅" desc="低于阈值即产生命中事件；付款永远人工">
        <div className="flex items-center justify-between">
          <Label>定时扫描</Label>
          <Switch checked={sn.enabled} onCheckedChange={(v) => upd("snipe.enabled", v)} />
        </div>
        <Field label="扫描间隔（分钟）">
          <Input type="number" className="w-32" value={sn.interval_minutes} onChange={(e) => upd("snipe.interval_minutes", Number(e.target.value))} />
        </Field>
        <div className="space-y-2">
          {sn.watch.map((w: any, i: number) => (
            <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-border p-2.5">
              <Input className="w-44" placeholder="关键词" value={w.keyword} onChange={(e) => upd(`snipe.watch.${i}.keyword`, e.target.value)} />
              <Input className="w-28" type="number" placeholder="价格≤" value={w.max_price ?? ""} onChange={(e) => upd(`snipe.watch.${i}.max_price`, e.target.value === "" ? null : Number(e.target.value))} />
              <Input className="w-28" type="number" step="0.05" placeholder="中位×" value={w.max_ratio ?? ""} onChange={(e) => upd(`snipe.watch.${i}.max_ratio`, e.target.value === "" ? null : Number(e.target.value))} />
              <Input className="flex-1" placeholder="备注" value={w.note} onChange={(e) => upd(`snipe.watch.${i}.note`, e.target.value)} />
              <Button variant="ghost" size="icon" onClick={() => upd("snipe.watch", sn.watch.filter((_: any, j: number) => j !== i))}><Trash2 size={14} /></Button>
            </div>
          ))}
          <Button variant="secondary" size="sm" onClick={() => upd("snipe.watch", [...sn.watch, { keyword: "", max_price: null, max_ratio: null, note: "" }])}>
            <Plus size={14} /> 加一条订阅
          </Button>
        </div>
      </Section>

      <Section title="每日审计" desc="到点自动跑并发查价并出经营简报">
        <Field label="触发时间（HH:MM，留空关闭）">
          <Input className="w-32" value={s.audit_daily_at || ""} onChange={(e) => upd("audit_daily_at", e.target.value)} placeholder="09:00" />
        </Field>
      </Section>

      <Section title="dsh 大脑 · API（隔离）" desc="三件套全部写入包内隔离的 dsh home，不碰系统 ~/.dsh">
        <div className="flex flex-wrap items-center gap-2 text-xs text-mutedfg">
          <Badge tone={keyInfo?.configured ? "success" : "warn"}>
            {keyInfo?.configured ? `Key ${keyInfo.masked}` : "Key 未配置"}
          </Badge>
          <Badge tone="info">地址 {keyInfo?.base_url ?? "-"}</Badge>
          {keyInfo?.model && <Badge tone="purple">模型 {keyInfo.model}</Badge>}
          <span className="w-full">隔离 home：{keyInfo?.home ?? "-"}</span>
        </div>
        <Field label="API 地址（baseURL，兼容 DeepSeek 协议的中转/代理都行；留空=官方 https://api.deepseek.com）">
          <Input placeholder="https://api.deepseek.com" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </Field>
        <Field label="模型名（留空=dsh 默认；如 deepseek-chat / deepseek-reasoner / 中转站模型名）">
          <Input placeholder="deepseek-chat" value={model} onChange={(e) => setModel(e.target.value)} />
        </Field>
        <Field label="API Key">
          <Input type="password" placeholder="sk-…" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </Field>
        <div className="flex items-center gap-3">
          <Button onClick={saveKey} disabled={!apiKey.trim() && !baseUrl.trim() && !model.trim()}>
            <KeyRound size={14} /> 保存（填了哪项就更新哪项）
          </Button>
          {keyMsg && <span className="text-xs text-mutedfg">{keyMsg}</span>}
        </div>
      </Section>

      <Section title="闲鱼账号登录" desc="推荐：浏览器扫码自动抓取（零复制粘贴）；失效时也可手动粘贴">
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone="success">当前 unb：{acct?.unb ?? "…"}</Badge>
          <span className="text-xs text-mutedfg">Cookie 长度 {acct?.cookie_length ?? 0}</span>
        </div>
        <div className="rounded-xl border border-dashed border-primary/40 bg-primary/[0.03] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm">
              {cap.state === "running" ? (
                <span className="flex items-center gap-2"><Spinner /> <b>抓取中</b> · 已等 {cap.elapsed_sec ?? 0}s / {cap.timeout_sec ?? 300}s</span>
              ) : cap.state === "success" ? (
                <b className="text-emerald-700">✓ 登录成功（unb {cap.unb || acct?.unb}），Cookie 已自动保存并热切换</b>
              ) : cap.state === "timeout" ? (
                <b className="text-amber-600">超时未登录，可重试</b>
              ) : cap.state === "error" ? (
                <b className="text-red-600">{cap.message || "出错了"}</b>
              ) : (
                <span>点击后弹出浏览器打开闲鱼，<b>用手机闲鱼 App 扫码登录</b>，机器人自动抓取 Cookie</span>
              )}
            </div>
            {cap.state === "running"
              ? <Button variant="secondary" size="sm" onClick={stopCapture}>取消抓取</Button>
              : <Button size="sm" onClick={startCapture}><ScanLine size={14} /> {cap.state === "success" ? "再抓一次" : "浏览器扫码获取"}</Button>}
          </div>
          {cap.state === "running" && cap.message && (
            <div className="mt-2 text-xs text-mutedfg">{cap.message}（等待登录…CDP 端口 {cap.port ?? "-"}）</div>
          )}
        </div>
        <details className="text-xs text-mutedfg">
          <summary className="cursor-pointer select-none py-1">手动粘贴 Cookie（备用）</summary>
          <div className="mt-2 space-y-2">
            <Textarea className="min-h-[80px] font-mono text-xs" placeholder="从浏览器 F12 复制整段 Cookie（须含 unb 与 _m_h5_tk）" value={cookie} onChange={(e) => setCookie(e.target.value)} />
            <div className="flex items-center gap-3">
              <Button size="sm" variant="secondary" onClick={saveCookie} disabled={cookie.trim().length < 50}><Cookie size={13} /> 保存并热切换</Button>
              {cookieMsg && <span>{cookieMsg}</span>}
            </div>
          </div>
        </details>
      </Section>

      <Section title="自动发货内容表（虚拟商品）" desc="买家付款后先自动发送对应内容，再走确认闸门">
        {(s.delivery_items || []).map((d: any, i: number) => (
          <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-border p-2.5">
            <Input className="w-40" placeholder="商品 ID（精确）" value={d.item_id} onChange={(e) => upd(`delivery_items.${i}.item_id`, e.target.value)} />
            <Input className="w-36" placeholder="或标题包含" value={d.title_contains} onChange={(e) => upd(`delivery_items.${i}.title_contains`, e.target.value)} />
            <Input className="flex-1" placeholder="发货内容（卡密/链接/说明）" value={d.content} onChange={(e) => upd(`delivery_items.${i}.content`, e.target.value)} />
            <Button variant="ghost" size="icon" onClick={() => upd("delivery_items", s.delivery_items.filter((_: any, j: number) => j !== i))}><Trash2 size={14} /></Button>
          </div>
        ))}
        <Button variant="secondary" size="sm" onClick={() => upd("delivery_items", [...(s.delivery_items || []), { item_id: "", title_contains: "", content: "" }])}>
          <Plus size={14} /> 加一条发货内容
        </Button>
      </Section>

      <Section title="议价底价表（watchlist）" desc="指定商品的成交底价与挂价；未配置的商品用 挂价×底价系数">
        {(s.watchlist || []).map((w: any, i: number) => (
          <div key={w.item_id ?? i} className="flex flex-wrap items-center gap-2 rounded-lg border border-border p-2.5">
            <Input className="w-36" placeholder="商品 ID" value={w.item_id} onChange={(e) => upd(`watchlist.${i}.item_id`, e.target.value)} />
            <Input className="w-32" type="number" placeholder="挂价" value={w.listed_price ?? ""} onChange={(e) => upd(`watchlist.${i}.listed_price`, e.target.value === "" ? null : Number(e.target.value))} />
            <Input className="w-32" type="number" placeholder="底价" value={w.floor_price ?? ""} onChange={(e) => upd(`watchlist.${i}.floor_price`, e.target.value === "" ? null : Number(e.target.value))} />
            <Input className="flex-1" placeholder="标题/查价关键词" value={w.title || w.keyword} onChange={(e) => upd(`watchlist.${i}.title`, e.target.value)} />
            <Button variant="ghost" size="icon" onClick={() => upd("watchlist", s.watchlist.filter((_: any, j: number) => j !== i))}><Trash2 size={14} /></Button>
          </div>
        ))}
        <Button variant="secondary" size="sm" onClick={() => upd("watchlist", [...(s.watchlist || []), { item_id: "", title: "", listed_price: null, floor_price: null, keyword: "" }])}>
          <Plus size={14} /> 加一条底价
        </Button>
      </Section>

      <Section title="代订酒店报价" desc="阶梯加价与限频（保存生效）；人工补价=你的会员/协议价单晚成本，报价时优先采用">
        {(() => { const hq = s.hotel_quotes; return hq ? (
          <>
            <div className="flex flex-wrap gap-2">
              {hq.tiers?.map((t: any, i: number) => (
                <Badge key={i} tone="info">≤¥{t[0]} 加价 {t[1]}%</Badge>
              ))}
              <Badge tone="warn">成本&gt;¥{hq.manual_threshold_cny} 转人工</Badge>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="比价缓存 TTL（分钟）">
                <Input type="number" value={hq.cache_ttl_min} onChange={(e) => upd("hotel_quotes.cache_ttl_min", Number(e.target.value))} />
              </Field>
              <Field label="每会话每小时全量比价上限">
                <Input type="number" value={hq.max_queries_per_hour} onChange={(e) => upd("hotel_quotes.max_queries_per_hour", Number(e.target.value))} />
              </Field>
            </div>
            <Field label="转人工阈值（元，成本超过即不自动报价）">
              <Input type="number" value={hq.manual_threshold_cny} onChange={(e) => upd("hotel_quotes.manual_threshold_cny", Number(e.target.value))} />
            </Field>
          </>
        ) : null; })()}
        <div className="flex flex-wrap items-end gap-2 rounded-lg border border-border p-3">
          <Field label="人工补价（酒店名）">
            <Input className="w-48" value={hcost.hotel} onChange={(e) => setHcost({ ...hcost, hotel: e.target.value })} placeholder="如：杭州开元名都" />
          </Field>
          <Field label="单晚成本价（元）">
            <Input className="w-28" type="number" value={hcost.price} onChange={(e) => setHcost({ ...hcost, price: e.target.value })} />
          </Field>
          <Button size="sm" variant="outline"
            disabled={!hcost.hotel.trim() || !hcost.price}
            onClick={async () => {
              setHcostMsg("");
              const r: any = await postHotelCost(hcost.hotel.trim(), Number(hcost.price));
              setHcostMsg(r.error || `已记录：${r.hotel} 成本 ¥${r.cost}`);
            }}>
            <Hotel size={14} /> 写入补价
          </Button>
          {hcostMsg && <span className="text-xs text-mutedfg">{hcostMsg}</span>}
        </div>
      </Section>

      <Section title="赫兹商旅 App（协议价真源）" desc="酒店代订第四源：模拟器里查南网协议价，命中时作为成本基准（优先于携程价）">
        <div className="rounded-lg border border-border bg-muted/40 p-3 space-y-1.5 text-xs text-mutedfg">
          <p className="font-medium text-fg">首次使用需要（一次装好，长期有效）：</p>
          <p>1. 安装 <a className="underline" href="https://mumu.163.com/" target="_blank" rel="noreferrer">MuMu 模拟器 12</a>（默认实例，ADB 端口 16384）；</p>
          <p>2. 模拟器里安装「赫兹商旅」App 并<b>人工登录一次</b>（机器人不碰密码，登录态保留在模拟器里）；</p>
          <p>3. 查价时保持 MuMu 开着（最小化可以，全程无需人工操作）。</p>
          <p>每次查价约 1.5~2 分钟（含冷启动）；未配置不影响其他三个查价源。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" disabled={appChecking}
            onClick={async () => {
              setAppChecking(true); setAppState(null);
              try { setAppState(await getHotelAppState()); } catch (e: any) { setAppState({ connected: false, error: String(e) }); }
              setAppChecking(false);
            }}>
            <Smartphone size={14} /> 检测 App 源状态
          </Button>
          {appChecking && <Spinner />}
          {appState && !appChecking && (
            <span className="text-xs">
              {appState.connected ? (
                <>
                  <Badge tone={appState.app_installed && appState.app_pid ? "success" : appState.app_installed ? "warn" : "danger"}>
                    {appState.app_installed ? (appState.app_pid ? "模拟器已连接 · App 运行中" : "模拟器已连接 · App 未运行（查价时自动拉起）") : "模拟器已连接 · 未装 App"}
                  </Badge>
                  {appState.at_login && <Badge tone="warn">App 未登录：请在 MuMu 里人工登录一次</Badge>}
                </>
              ) : (
                <Badge tone="danger">模拟器未连接：{appState.error || "MuMu 没开或 ADB 不通"}</Badge>
              )}
            </span>
          )}
        </div>
      </Section>

      <Section title="商旅协议码与商旅平台" desc="协议码=企业与酒店集团的 Corporate Code（如中油/赫兹等商旅协议）；官网查价时按集团自动填码刷新协议价">
        <div className="space-y-2">
          {(s.ota?.corporate_codes || []).map((c: any, i: number) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <Input className="w-36" value={c.group} onChange={(e) => upd(`ota.corporate_codes.${i}.group`, e.target.value)} placeholder="集团 key（marriott/hilton/ihg/accor/huazhu）" />
              <Input className="w-32" value={c.code} onChange={(e) => upd(`ota.corporate_codes.${i}.code`, e.target.value)} placeholder="协议码" />
              <Input className="w-32" value={c.label} onChange={(e) => upd(`ota.corporate_codes.${i}.label`, e.target.value)} placeholder="渠道名（如中油）" />
              <Button variant="ghost" size="icon" onClick={() => upd("ota.corporate_codes", (s.ota?.corporate_codes || []).filter((_: any, j: number) => j !== i))}><Trash2 size={14} /></Button>
            </div>
          ))}
          <Button variant="secondary" size="sm" onClick={() => upd("ota.corporate_codes", [...(s.ota?.corporate_codes || []), { group: "", code: "", label: "" }])}>
            <Plus size={14} /> 加一条协议码
          </Button>
        </div>
        <div className="space-y-2">
          <p className="text-xs text-mutedfg">登录制商旅平台（第四源）：在「查价浏览器」登录后，官网源可直接抓该平台协议价</p>
          {(s.ota?.extra_sources || []).map((x: any, i: number) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <Input className="w-36" value={x.name} onChange={(e) => upd(`ota.extra_sources.${i}.name`, e.target.value)} placeholder="平台名（如石化商旅）" />
              <Input className="w-64" value={x.url} onChange={(e) => upd(`ota.extra_sources.${i}.url`, e.target.value)} placeholder="https://trip.sinopec.com" />
              <Input className="w-40" value={x.note} onChange={(e) => upd(`ota.extra_sources.${i}.note`, e.target.value)} placeholder="备注" />
              <Button variant="ghost" size="icon" onClick={() => upd("ota.extra_sources", (s.ota?.extra_sources || []).filter((_: any, j: number) => j !== i))}><Trash2 size={14} /></Button>
            </div>
          ))}
          <Button variant="secondary" size="sm" onClick={() => upd("ota.extra_sources", [...(s.ota?.extra_sources || []), { name: "", url: "", note: "" }])}>
            <Plus size={14} /> 加一个商旅平台
          </Button>
        </div>
      </Section>

      <Section title="dsh 大脑参数" desc="job 超时 / 并发 / 会话冷却；dsh_home 为空则用系统默认">
        <div className="grid grid-cols-2 gap-3">
          <Field label="job 超时（秒）">
            <Input type="number" value={s.brain.job_timeout_sec} onChange={(e) => upd("brain.job_timeout_sec", Number(e.target.value))} />
          </Field>
          <Field label="全局并发 job 数">
            <Input type="number" value={s.brain.max_concurrent} onChange={(e) => upd("brain.max_concurrent", Number(e.target.value))} />
          </Field>
          <Field label="同会话大脑冷却（秒）">
            <Input type="number" value={s.brain.per_chat_cooldown_sec} onChange={(e) => upd("brain.per_chat_cooldown_sec", Number(e.target.value))} />
          </Field>
          <Field label="dsh profile">
            <Input value={s.brain.profile} onChange={(e) => upd("brain.profile", e.target.value)} />
          </Field>
        </div>
        <Field label="隔离 DSH_HOME（单体包指向包内；留空=系统 ~/.dsh）">
          <Input value={s.brain.dsh_home || ""} onChange={(e) => upd("brain.dsh_home", e.target.value)} placeholder="data/dsh-home" />
        </Field>
      </Section>
    </div>
  );
}
