/**
 * fundCodes.ts - 基金短码映射（F3 修复）。
 *
 * 图例 / tooltip 系列名 / 热力图标题统一使用「编号·缩写」短码，避免全名在图例区
 * 横向堆砌并与回撤子图 x 轴标签重叠。全名仅保留于 tooltip 首行与 CompareTable 名称列。
 *
 * 编号按 fund_name 字母升序稳定分配（Bentham=1 … Stake=6），与既定 6 只基金顺序一致；
 * 基金增删时编号按同一规则重算。缩写由 name 关键词规则解析，匹配不到则取首字母兜底。
 */

export interface FundLike {
  fund_id: string
  fund_name: string
}

/** name -> 缩写规则（顺序匹配，首个命中生效） */
const ABBR_RULES: { match: RegExp; abbr: string }[] = [
  { match: /Bentham/i, abbr: 'BGIF' },
  { match: /Coolabah.*Assisted/i, abbr: 'FRHY-A' },
  { match: /Coolabah.*Institutional/i, abbr: 'FRHY-I' },
  { match: /Macquarie/i, abbr: 'MAFI' },
  { match: /Smarter Money/i, abbr: 'SMLS' },
  { match: /Stake/i, abbr: 'STAK' },
]

/** 单只基金缩写（无编号）。 */
export function fundAbbr(name: string): string {
  const rule = ABBR_RULES.find(r => r.match.test(name))
  if (rule) return rule.abbr
  // 兜底：取各词首字母，最多 4 位
  return name.split(/\s+/).filter(Boolean).map(w => w[0]).join('').slice(0, 4).toUpperCase()
}

/**
 * 构建 fund_id -> 「编号·缩写」短码映射。编号按 fund_name 字母升序分配。
 * 调用方传入当前 funds 列表，得到稳定短码；空列表返回空 Map。
 */
export function buildShortCodeMap(funds: FundLike[]): Map<string, string> {
  const sorted = [...funds].sort((a, b) => a.fund_name.localeCompare(b.fund_name))
  const map = new Map<string, string>()
  sorted.forEach((f, i) => map.set(f.fund_id, `${i + 1}·${fundAbbr(f.fund_name)}`))
  return map
}
