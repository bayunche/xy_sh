import * as React from "react";
import { Card, CardHeader, CardTitle, CardDesc, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, THead, TBody } from "@/components/ui/table";
import { Empty, Spinner } from "@/components/ui/misc";
import { getSnipeHits, postSnipeRun, postSnipeHandle } from "@/lib/api";
import { fmtTime } from "@/lib/utils";
import { Radar, ExternalLink, CheckCircle2, RefreshCw } from "lucide-react";
import { useStatus } from "@/components/Layout";

export default function Snipe() {
  const { status } = useStatus();
  const [hits, setHits] = React.useState<any[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [scanInfo, setScanInfo] = React.useState("");

  const load = React.useCallback(() => getSnipeHits().then((r: any) => setHits(r.hits || [])).catch(() => {}), []);
  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => { const t = setInterval(load, 20000); return () => clearInterval(t); }, [load]);

  const scan = async () => {
    setBusy(true); setScanInfo("");
    try {
      const r: any = await postSnipeRun();
      setScanInfo(`本轮命中 ${r.hits?.length ?? 0} 条`);
      load();
    } finally { setBusy(false); }
  };

  const handle = async (itemId: string) => { await postSnipeHandle(itemId); load(); };
  const unhandled = hits.filter((h: any) => !h.handled).length;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">盯货捡漏</h1>
          <p className="mt-1 text-sm text-mutedfg">
            订阅关键词自动扫描低价货源；<b>付款永远人工</b>——点「去购买」核实后自己拍下
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={status?.snipe_enabled ? "success" : "default"}>
            定时扫描 {status?.snipe_enabled ? "开" : "关"}
          </Badge>
          <Button size="sm" disabled={busy} onClick={scan}>
            {busy ? <Spinner /> : <Radar size={14} />} 立即扫描
          </Button>
          <Button size="sm" variant="secondary" onClick={load}><RefreshCw size={14} /></Button>
        </div>
      </div>

      {scanInfo && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{scanInfo}</div>}

      <Card>
        <CardHeader>
          <CardTitle>命中记录（{hits.length} 条 · 待处理 {unhandled}）</CardTitle>
          <CardDesc>阈值在「设置 → 盯货」调整；同一商品只报一次</CardDesc>
        </CardHeader>
        <CardContent className="p-0">
          {hits.length === 0 ? <Empty>还没有命中——去设置里加订阅关键词</Empty> : (
            <Table>
              <THead><tr><th>时间</th><th>摘要</th><th>商品</th><th>状态</th><th>操作</th></tr></THead>
              <TBody>
                {hits.map((h: any, i: number) => (
                  <tr key={h.id ?? i} className={h.handled ? "opacity-50" : ""}>
                    <td className="whitespace-nowrap text-xs text-mutedfg">{fmtTime(h.ts)}</td>
                    <td className="max-w-md whitespace-pre-wrap text-xs">{h.summary}</td>
                    <td className="text-[11px] text-mutedfg">{h.item_id}</td>
                    <td>{h.handled ? <Badge tone="success">已处理</Badge> : <Badge tone="warn">待处理</Badge>}</td>
                    <td>
                      <div className="flex gap-1.5">
                        {h.url && (
                          <a href={h.url} target="_blank" rel="noreferrer">
                            <Button size="sm" variant="outline"><ExternalLink size={13} /> 去购买</Button>
                          </a>
                        )}
                        {!h.handled && (
                          <Button size="sm" variant="ghost" onClick={() => handle(h.item_id)}>
                            <CheckCircle2 size={13} /> 标记已处理
                          </Button>
                        )}
                      </div>
                    </td>
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
