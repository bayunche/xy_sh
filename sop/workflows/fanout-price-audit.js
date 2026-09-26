// fanout-price-audit.js —— 多商品并发查价 workflow（dsh workflow 工具脚本）
//
// 用法：大脑在审计 job 中通过 workflow 工具执行本脚本，args 传入商品清单：
//   args = { items: [ { item_id, title, listed_price, keyword }, ... ] }
// 脚本约定（dsh workflow 引擎）：
//   - 纯 JS，顶层 await，结尾 return <json>
//   - agent(prompt) 派一个子代理，返回其最终答复文本
//   - parallel([...]) 并发执行；基础设施误用会当场报错，
//     子代理普通失败返回 null，由脚本自行兜底
//
const items = (args && args.items) || []

const one = async (it) => {
  try {
    const out = await agent(
      `你是闲鱼查价员。执行：\n` +
      `1) 运行命令 uv run --project gate xy-gate search "${it.keyword || it.title}" --rows 30\n` +
      `2) 从返回 JSON 提取 stats.median/p25/p75 与 stats.count\n` +
      `3) 只返回一行 JSON（不要多余文字）：\n` +
      `{"item_id":"${it.item_id}","title":"${it.title}","listed_price":${it.listed_price},` +
      `"median":<数字或null>,"p25":<..>,"p75":<..>,"count":<数字>}\n` +
      `查价失败时 median/p25/p75 置 null 并加 "error":"原因"`
    )
    try {
      const m = out.match(/\{[\s\S]*\}/)
      return m ? JSON.parse(m[0]) : { item_id: it.item_id, title: it.title, error: '子代理输出无JSON' }
    } catch (e) {
      return { item_id: it.item_id, title: it.title, error: '子代理输出解析失败' }
    }
  } catch (e) {
    return { item_id: it.item_id, title: it.title, error: String(e) }
  }
}

const results = await parallel(items.map((it) => () => one(it)))

// 汇总：挂价 vs 市场中位价偏差
const rows = results.map((r) => {
  const listed = Number(r.listed_price) || 0
  const median = Number(r.median) || 0
  const dev = listed && median ? Math.round(((listed - median) / median) * 1000) / 10 : null
  const advice = dev === null ? '样本不足' : dev > 15 ? `建议降价至 ~${median}` : dev < -15 ? `可涨价至 ~${median}` : '保持'
  return { ...r, dev_pct: dev, advice }
})

log(`查价完成：${rows.length} 个商品`)
return { date: new Date().toISOString().slice(0, 10), rows }
