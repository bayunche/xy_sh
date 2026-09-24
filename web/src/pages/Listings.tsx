import * as React from "react";
import { Card, CardHeader, CardTitle, CardDesc, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, THead, TBody } from "@/components/ui/table";
import { Dialog, Empty, Spinner, Field } from "@/components/ui/misc";
import { getItems, getFloor, postReprice, postSearch, postOffline } from "@/lib/api";
import { Search, RefreshCw, Tag, PackageX } from "lucide-react";

export default function Listings() {
  const [items, setItems] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [msg, setMsg] = React.useState("");

  // 查价
  const [kw, setKw] = React.useState("");
  const [min, setMin] = React.useState("");
  const [max, setMax] = React.useState("");
  const [searching, setSearching] = React.useState(false);
  const [results, setResults] = React.useState<any>(null);

  // 改价对话框
  const [rp, setRp] = React.useState<{ item: any; floor: any } | null>(null);
  const [newPrice, setNewPrice] = React.useState("");
  const [rpBusy, setRpBusy] = React.useState(false);
  const [rpResult, setRpResult] = React.useState<any>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const r: any = await getItems();
      setItems(r.items || []);
      setMsg(r.error || "");
    } finally { setLoading(false); }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const search = async () => {
    if (!kw.trim()) return;
    setSearching(true); setResults(null);
    try {
      setResults(await postSearch(kw.trim(), Number(min) || undefined, Number(max) || undefined));
    } finally { setSearching(false); }
  };

  const openReprice = async (item: any) => {
    setRpResult(null);
    setNewPrice(String(item.price ?? ""));
    setRp({ item, floor: null });
    setRp({ item, floor: await getFloor(item.item_id) });
  };

  const doReprice = async () => {
    if (!rp) return;
    setRpBusy(true);
    try { setRpResult(await postReprice(rp.item.item_id, Number(newPrice))); }
    finally { setRpBusy(false); load(); }
  };

  const doOffline = async (item: any) => {
    if (!confirm(`确认下架「${item.title.slice(0, 20)}…」？`)) return;
    const r: any = await postOffline([item.item_id]);
    alert(r.simulated ? "[dry-run] 模拟下架成功" : `下架结果：${JSON.stringify(r).slice(0, 200)}`);
    load();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">商品 · 查价</h1>
          <p className="mt-1 text-sm text-mutedfg">在售商品管理（底价 / 改价 / 下架）与市场行情查询</p>
        </div>
        <Button variant="secondary" size="sm" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={14} />} 刷新</Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>我的在售（{items.length}）</CardTitle>
          <CardDesc>改价过四道闸门（底价 / 降幅 / 频次 / 只降不涨）；dry-run 下为模拟</CardDesc>
        </CardHeader>
        <CardContent className="p-0">
          {msg && <div className="px-5 py-2 text-xs text-red-600">{msg}</div>}
          {items.length === 0 && !loading ? <Empty /> : (
            <Table>
              <THead><tr><th className="w-20">图片</th><th>商品</th><th>挂价</th><th>操作</th></tr></THead>
              <TBody>
                {items.map((i: any) => (
                  <tr key={i.item_id}>
                    <td>{i.pic_url
                      ? <img src={i.pic_url.replace("http://", "https://")} className="h-12 w-12 rounded-lg object-cover" alt="" />
                      : <div className="h-12 w-12 rounded-lg bg-muted" />}</td>
                    <td>
                      <div className="max-w-md truncate text-sm font-medium">{i.title}</div>
                      <div className="text-[11px] text-mutedfg">ID {i.item_id} · {i.post_info || "-"}</div>
                    </td>
                    <td className="font-semibold text-primary">¥{i.price ?? "-"}</td>
                    <td>
                      <div className="flex gap-1.5">
                        <Button size="sm" variant="outline" onClick={() => openReprice(i)}><Tag size={13} /> 改价</Button>
                        <Button size="sm" variant="ghost" onClick={() => doOffline(i)}><PackageX size={13} /> 下架</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>市场查价</CardTitle>
          <CardDesc>搜索同款在售，输出价格分布；结果用于报价与审计</CardDesc>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-2">
            <Input className="w-72" placeholder="商品关键词，如：Switch OLED 日版" value={kw}
              onChange={(e) => setKw(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} />
            <Input className="w-24" placeholder="最低价" value={min} onChange={(e) => setMin(e.target.value)} />
            <Input className="w-24" placeholder="最高价" value={max} onChange={(e) => setMax(e.target.value)} />
            <Button onClick={search} disabled={searching || !kw.trim()}>{searching ? <Spinner /> : <Search size={14} />} 查价</Button>
          </div>

          {results?.stats?.count > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              <Badge tone="info">样本 {results.stats.count}</Badge>
              <Badge tone="success">中位 ¥{results.stats.median}</Badge>
              <Badge>P25 ¥{results.stats.p25}</Badge>
              <Badge>P75 ¥{results.stats.p75}</Badge>
              <Badge tone="warn">区间 ¥{results.stats.min} ~ ¥{results.stats.max}</Badge>
            </div>
          )}
          {results?.error && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{results.error}</div>}

          {results?.items?.length > 0 && (
            <div className="mt-4 max-h-96 overflow-auto rounded-lg border border-border">
              <Table>
                <THead><tr><th>价格</th><th>标题</th><th>地区</th><th>想要</th><th>链接</th></tr></THead>
                <TBody>
                  {results.items.slice(0, 20).map((it: any, idx: number) => (
                    <tr key={it.item_id || idx}>
                      <td className="whitespace-nowrap font-medium">¥{it.price}</td>
                      <td className="max-w-sm truncate text-xs">{it.title}</td>
                      <td className="text-xs">{it.area}</td>
                      <td className="text-xs">{it.want_count || 0}</td>
                      <td>{it.url && <a className="text-xs text-primary hover:underline" href={it.url} target="_blank" rel="noreferrer">打开</a>}</td>
                    </tr>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!rp} onClose={() => setRp(null)} title={`改价 · ${rp?.item?.title?.slice(0, 24) ?? ""}…`}>
        {rp && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-lg bg-muted p-3"><div className="text-xs text-mutedfg">当前挂价</div><div className="mt-1 font-bold">¥{rp.item.price}</div></div>
              <div className="rounded-lg bg-emerald-50 p-3"><div className="text-xs text-emerald-700">议价底价</div><div className="mt-1 font-bold text-emerald-700">¥{rp.floor?.floor_price ?? "未知"}</div></div>
            </div>
            <Field label="新价格（只能降价且不破底价）">
              <Input value={newPrice} onChange={(e) => setNewPrice(e.target.value)} type="number" />
            </Field>
            {rpResult && (
              <div className={rpResult.decision === "block" ? "rounded-lg bg-red-50 p-3 text-xs text-red-700" : "rounded-lg bg-emerald-50 p-3 text-xs text-emerald-700"}>
                闸门 {rpResult.decision} · {rpResult.reasons?.join("；")}
                {rpResult.result && <div className="mt-1 opacity-80">{rpResult.result}</div>}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setRp(null)}>关闭</Button>
              <Button onClick={doReprice} disabled={rpBusy || !newPrice}>{rpBusy ? <Spinner size={14} /> : null} 确认改价</Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
