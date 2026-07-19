/**
 * rebase.ts — 对比看板图表纯函数引擎（Phase 2）。
 *
 * 所有 rebase / 回撤 / 滚动超额 / 月度超额计算在此完成，输入为月度收益序列 + 全局 RBA。
 * 严禁插值/外推/回填；缺失如实呈现为 null（PDD 2.8）。
 *
 * 状态（由调用方决定 axisMonths 与 anchor）：
 *  A 全部区间 / B 固定窗口：rebasePlain，各基金自其（窗口内）首月起 base=1.0。
 *  C 锚定模式：rebaseAnchored，拼接 rebase（PDD 2.3）。
 */

export interface FundReturns {
  fund_id: string
  fund_name: string
  dates: string[] // YYYY-MM-DD 月末
  returns: number[] // 逐月收益，对齐 dates
}

export interface RebasedPoint {
  month: string // YYYY-MM
  value: number
}

export interface RebasedSeries {
  fund_id: string
  fund_name: string
  nav: (number | null)[] // 对齐调用方传入的 axisMonths
  isAnchor: boolean
  /** 拼接点（C 态后发基金首点），空心圆标记；tooltip 承接锚定基金上月累计值 */
  splicePoint?: RebasedPoint
  /** 降级：后发基金晚于锚定结束，未拼接，独立 base=1.0 */
  degraded?: boolean
}

/**
 * 按状态算 x 轴显示月份（NavChart / RollingExcessChart / CompareTable 共用，F3 去重）。
 *  A full：全历史并集；B 1y/3y：末段 12/36 月；B common：选中基金月份交集；
 *  C 锚定：[t_A, max(所有基金末月)]（修正2，容纳降级基金延伸至锚定之后）。
 *  period 取 string 以避免与 store 循环依赖；调用方传 Period 字面量。
 */
export function computeAxisMonths(
  months: string[], funds: FundReturns[], period: string, anchorFundId: string | null,
): string[] {
  if (anchorFundId) {
    const anchor = funds.find(f => f.fund_id === anchorFundId)
    if (!anchor) return months
    const tA = anchor.dates[0].slice(0, 7)
    const end = funds.map(f => f.dates[f.dates.length - 1].slice(0, 7)).sort().pop()!
    return months.filter(m => m >= tA && m <= end)
  }
  if (period === 'full') return months
  if (period === '1y') return months.slice(-12)
  if (period === '3y') return months.slice(-36)
  // common：选中基金月份交集
  const sets = funds.map(f => new Set(f.dates.map(d => d.slice(0, 7))))
  let common = sets[0] ?? new Set<string>()
  for (const s of sets.slice(1)) common = new Set([...common].filter(m => s.has(m)))
  return months.filter(m => common.has(m))
}

/** RBA 年化利率 -> 月化基准收益（修正B：单一工具，热力图 + 滚动超额共用，杜绝分叉） */
export function monthlyBench(rbaAnnual: number): number {
  return rbaAnnual / 12.0
}

/** 分位数（线性插值，输入须升序）。热力图用来裁剪色标：避免个别危机月
 *  （如 2008/2020 单月超额 ±10%+）把色标拉到自己独占满色，其余正常月份
 *  全被稀释成看不出深浅的淡色。 */
export function percentile(sortedAsc: number[], p: number): number {
  if (sortedAsc.length === 0) return 0
  const idx = p * (sortedAsc.length - 1)
  const lo = Math.floor(idx)
  const hi = Math.ceil(idx)
  if (lo === hi) return sortedAsc[lo]
  return sortedAsc[lo] + (sortedAsc[hi] - sortedAsc[lo]) * (idx - lo)
}

/** 由月收益序列自建 NAV：nav[i] = base × ∏_{0..i}(1+r_s)。概念基点 base 位于 dates[0] 之前。 */
export function buildNav(returns: number[], base = 1.0): number[] {
  const nav: number[] = []
  let v = base
  for (const r of returns) {
    v = v * (1 + r)
    nav.push(v)
  }
  return nav
}

/** 上一月（YYYY-MM -> YYYY-MM） */
export function monthBefore(ym: string): string {
  const [y, m] = ym.split('-').map(Number)
  if (m === 1) return `${y - 1}-12`
  return `${y}-${String(m - 1).padStart(2, '0')}`
}

/**
 * 给 axisMonths 补上每条线自己需要、轴上还没有的"起点前一月"基点位置（配合
 * alignedNav 的 baseMonth 恒等点用）。锚定态：tFund<=tA 的基金被裁剪对齐到 tA
 * （见 rebaseAnchored"早于或等于 A"分支），基点位置统一用 monthBefore(max(tA,tFund))；
 * tFund>tA 的后发基金（拼接或降级）用自己的 monthBefore(tFund)——两种情况下
 * drawdownSeries 用的 rebasePlain 与 NAV 用的 rebaseAnchored 需要的都是同一个位置，
 * 一次补齐两边共用（NavChart 用同一份 axisMonths 喂两者）。
 * 非锚定态（A/B）：每支基金各自 monthBefore(own dates[0])。
 */
export function withLeadingBaseMonths(
  axisMonths: string[], funds: FundReturns[], anchorFundId: string | null,
): string[] {
  const tA = anchorFundId
    ? funds.find(f => f.fund_id === anchorFundId)?.dates[0].slice(0, 7) ?? null
    : null
  const needed = funds.map(f => {
    const tFund = f.dates[0].slice(0, 7)
    const ref = tA && tFund <= tA ? tA : tFund
    return monthBefore(ref)
  })
  const have = new Set(axisMonths)
  const missing = [...new Set(needed)].filter(m => !have.has(m))
  if (missing.length === 0) return axisMonths
  return [...axisMonths, ...missing].sort()
}

function returnsByMonth(fund: FundReturns): Map<string, number> {
  const m = new Map<string, number>()
  fund.dates.forEach((d, i) => m.set(d.slice(0, 7), fund.returns[i]))
  return m
}

/**
 * 在 axisMonths 上从 startMonth 起以 base 复利构建 nav；startMonth 之前与基金无数据月为 null，
 * 除了 startMonth 的前一月（monthBefore(startMonth)）——若轴上有这一格，画出 base 本身。
 *
 * 这不是编造 NAV：回报的定义就是"上月底到本月底"的变化量，上月底=base 是数学上必然
 * 成立的恒等参照点，与基金那天是否有可验证的真实 NAV 观测无关（权威机构 Growth of
 * $X / MSCI-Bloomberg 总回报指数重建同样惯例：N 个月度回报对应 N+1 个指数点）。
 * 基金序列已由摄取层保证无内部缺口（Phase 1 零容忍），故 null 仅出现在首月前/末月后。
 */
function alignedNav(fund: FundReturns, axisMonths: string[], base: number,
                   startMonth: string): (number | null)[] {
  const byMonth = returnsByMonth(fund)
  const nav: (number | null)[] = []
  let v: number | null = null
  let started = false
  const baseMonth = monthBefore(startMonth)
  for (const m of axisMonths) {
    if (m === baseMonth) {
      v = base
      started = true
      nav.push(v)
      continue
    }
    if (m < startMonth) {
      nav.push(null)
      continue
    }
    const r = byMonth.get(m)
    if (r == null) {
      // 基金该月无数据（末月之后）；缺口零容忍保证不会出现在区间内部
      nav.push(null)
      continue
    }
    v = started ? (v as number) * (1 + r) : base * (1 + r)
    started = true
    nav.push(v)
  }
  return nav
}

/**
 * State A/B：各基金自其（窗口内）首个有效月起 base=1.0。
 * axisMonths：A=全历史并集，B=窗口月份（调用方裁剪 full 数据）。
 */
export function rebasePlain(fund: FundReturns, axisMonths: string[]): RebasedSeries {
  const startMonth = fund.dates.length ? fund.dates[0].slice(0, 7) : ''
  return {
    fund_id: fund.fund_id,
    fund_name: fund.fund_name,
    nav: alignedNav(fund, axisMonths, 1.0, startMonth),
    isAnchor: false,
  }
}

/**
 * State C：拼接 rebase（PDD 2.3）。锚定基金 A，发行月 t_A = A.dates[0]。
 *  - A 自身：base=1.0 @ t_A。
 *  - 早于 A 的 Y（t_Y < t_A）：裁剪 t_A 前，base=1.0 @ t_A（与 A 同起跑线）。
 *  - 晚于 A 的 X（t_X > t_A）：base_X = V_A(t_X−1)；nav=base_X × ∏(1+r_X)。拼接点空心圆于 X 首点。
 *  - t_X == t_A：base=1.0 @ t_A，无拼接点。
 *  - X 晚于 A 结束（base_X 缺失）：降级 base=1.0 @ t_X，degraded=true。
 */
export function rebaseAnchored(funds: FundReturns[], axisMonths: string[],
                               anchorFundId: string): RebasedSeries[] {
  const anchor = funds.find(f => f.fund_id === anchorFundId)
  if (!anchor) {
    // 锚定基金不在数据中（被取消选中）：退化为 plain
    return funds.map(f => rebasePlain(f, axisMonths))
  }
  const tA = anchor.dates[0].slice(0, 7)

  // 锚定 nav（base=1.0 @ t_A）及其按月查表，供热发基金求 base_X
  const anchorNavArr = alignedNav(anchor, axisMonths, 1.0, tA)
  const anchorNavByMonth = new Map<string, number>()
  axisMonths.forEach((m, i) => {
    if (anchorNavArr[i] != null) anchorNavByMonth.set(m, anchorNavArr[i] as number)
  })

  const result: RebasedSeries[] = []
  for (const fund of funds) {
    if (fund.fund_id === anchorFundId) {
      result.push({ fund_id: fund.fund_id, fund_name: fund.fund_name,
                    nav: anchorNavArr, isAnchor: true })
      continue
    }
    const tFund = fund.dates[0].slice(0, 7)
    if (tFund > tA) {
      // 后发基金：拼接。base_X = V_A(t_X − 1)
      const baseMonth = monthBefore(tFund)
      const baseX = anchorNavByMonth.get(baseMonth)
      if (baseX == null) {
        // 锚定基金在 X 发行前已结束，无重叠：降级独立 base=1.0
        result.push({ fund_id: fund.fund_id, fund_name: fund.fund_name,
                      nav: alignedNav(fund, axisMonths, 1.0, tFund),
                      isAnchor: false, degraded: true })
      } else {
        const nav = alignedNav(fund, axisMonths, baseX, tFund)
        const spliceIdx = nav.findIndex(v => v != null)
        const splicePoint = spliceIdx >= 0
          ? { month: axisMonths[spliceIdx], value: nav[spliceIdx] as number }
          : undefined
        result.push({ fund_id: fund.fund_id, fund_name: fund.fund_name,
                      nav, isAnchor: false, splicePoint })
      }
    } else {
      // 早于或等于 A：裁剪到 t_A，base=1.0 @ t_A
      result.push({ fund_id: fund.fund_id, fund_name: fund.fund_name,
                    nav: alignedNav(fund, axisMonths, 1.0, tA),
                    isAnchor: false })
    }
  }
  return result
}

/**
 * 回撤曲线（PDD 2.4）：永远基于各基金自身 nav（rebasePlain 输出，非拼接序列）。
 * DD_i(t) = V_i(t) / max_{s ∈ [窗口起点, t]} V_i(s) − 1。C 态后发基金从自身发行月起算。
 */
export function drawdownSeries(nav: (number | null)[]): (number | null)[] {
  const dd: (number | null)[] = []
  let peak: number | null = null
  for (const v of nav) {
    if (v == null) {
      dd.push(null)
      continue
    }
    if (peak == null || v > peak) peak = v
    dd.push(v / peak - 1)
  }
  return dd
}

/**
 * 滚动 12 月超额（PDD 2.5）：月份 t，基金在 [t−11, t] 连续 12 月均有收益且 RBA 时
 * RollExcess(t) = ∏(1+r_fund) − ∏(1+monthlyBench(rba))。任一月缺失（含 RBA null）⇒ 该 t 为 null。
 *
 * 在 full 月轴上计算（方案a），调用方再按窗口裁剪显示——近1年窗口下曲线完整。
 * RBA null 月使含它的 12 个窗口全 null（修正1：不跳过凑 11 月）。
 */
export function rollingExcess(fund: FundReturns, months: string[],
                              rba: (number | null)[], window = 12): (number | null)[] {
  const byMonth = returnsByMonth(fund)
  const rbaByMonth = new Map<string, number>()
  months.forEach((m, i) => {
    if (rba[i] != null) rbaByMonth.set(m, rba[i] as number)
  })
  const result: (number | null)[] = []
  for (let i = 0; i < months.length; i++) {
    if (i < window - 1) {
      result.push(null) // 历史不足 12 月
      continue
    }
    const windowMonths = months.slice(i - window + 1, i + 1)
    let prodFund = 1.0
    let prodBench = 1.0
    let ok = true
    for (const m of windowMonths) {
      const r = byMonth.get(m)
      const rb = rbaByMonth.get(m)
      if (r == null || rb == null) {
        ok = false
        break
      }
      prodFund *= 1 + r
      prodBench *= 1 + monthlyBench(rb)
    }
    result.push(ok ? prodFund - prodBench : null)
  }
  return result
}

export interface MonthlyExcessPoint {
  month: string // YYYY-MM
  year: number
  monthNum: number
  excess: number | null
  fundReturn: number | null
  rbaRate: number | null
}

/** 月度超额（PDD 2.6 热力图）：e_t = r_fund − monthlyBench(rba)。缺失月 excess=null。 */
export function monthlyExcess(fund: FundReturns, months: string[],
                              rba: (number | null)[]): MonthlyExcessPoint[] {
  const byMonth = returnsByMonth(fund)
  const rbaByMonth = new Map<string, number>()
  months.forEach((m, i) => {
    if (rba[i] != null) rbaByMonth.set(m, rba[i] as number)
  })
  return months.map(m => {
    const r = byMonth.get(m) ?? null
    const rb = rbaByMonth.get(m) ?? null
    const [y, mon] = m.split('-').map(Number)
    return {
      month: m, year: y, monthNum: mon,
      excess: r != null && rb != null ? r - monthlyBench(rb) : null,
      fundReturn: r, rbaRate: rb,
    }
  })
}
