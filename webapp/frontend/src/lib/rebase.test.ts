import { describe, it, expect } from 'vitest'
import {
  monthlyBench, buildNav, monthBefore, rebasePlain, rebaseAnchored,
  drawdownSeries, rollingExcess, monthlyExcess, computeAxisMonths, percentile,
  withLeadingBaseMonths,
  type FundReturns,
} from './rebase'

function monthsAxis(start: string, end: string): string[] {
  const [sy, sm] = start.split('-').map(Number)
  const [ey, em] = end.split('-').map(Number)
  const out: string[] = []
  let y = sy, m = sm
  while (y < ey || (y === ey && m <= em)) {
    out.push(`${y}-${String(m).padStart(2, '0')}`)
    m++
    if (m > 12) { m = 1; y++ }
  }
  return out
}
function fund(fid: string, start: string, end: string, ret: number | number[]): FundReturns {
  const months = monthsAxis(start, end)
  const returns = Array.isArray(ret) ? ret : months.map(() => ret)
  return { fund_id: fid, fund_name: fid, dates: months.map(m => `${m}-28`), returns }
}

describe('monthlyBench (修正B: 单一月化工具)', () => {
  it('rba 年化 / 12', () => {
    expect(monthlyBench(0.036)).toBeCloseTo(0.003)
  })
  it('恒定 rba=0.036 时 12 月基准累计 ≈ (1.003)^12 − 1 ≈ 3.66%', () => {
    const rba = 0.036
    let prod = 1.0
    for (let i = 0; i < 12; i++) prod *= 1 + monthlyBench(rba)
    expect(prod - 1).toBeCloseTo(Math.pow(1.003, 12) - 1, 5)
    expect(prod - 1).toBeCloseTo(0.0366, 2)
  })
})

describe('buildNav / monthBefore', () => {
  it('buildNav 累计复利', () => {
    expect(buildNav([0.01, 0.02, 0.03], 1.0)).toEqual([
      1.01, 1.01 * 1.02, 1.01 * 1.02 * 1.03,
    ])
  })
  it('monthBefore 跨年', () => {
    expect(monthBefore('2023-01')).toBe('2022-12')
    expect(monthBefore('2023-03')).toBe('2023-02')
  })
})

describe('rebasePlain (状态 A/B)', () => {
  it('起点前一月(monthBefore)若在轴上则画出恒等基点=1.0；再前面才是 null', () => {
    const f = fund('A', '2022-02', '2022-04', 0.01)
    const axis = monthsAxis('2022-01', '2022-04')
    const r = rebasePlain(f, axis)
    expect(r.nav[0]).toBeCloseTo(1.0) // 2022-01 = monthBefore(首月) 恒等基点
    expect(r.nav[1]).toBeCloseTo(1.01) // 2022-02 首月 base=1.0 复利
    expect(r.nav[2]).toBeCloseTo(1.01 * 1.01)
    expect(r.isAnchor).toBe(false)
  })

  it('轴上没有起点前一月时不画基点, 只是 null(向后兼容: 不强行扩轴)', () => {
    const f = fund('A', '2022-03', '2022-04', 0.01) // monthBefore = 2022-02, 不在轴上
    const axis = monthsAxis('2022-03', '2022-04')
    const r = rebasePlain(f, axis)
    expect(r.nav[0]).toBeCloseTo(1.01) // 首月直接复利, 没有额外基点
    expect(r.nav.length).toBe(2)
  })
})

describe('rebaseAnchored (状态 C 拼接)', () => {
  const axis = monthsAxis('2022-01', '2023-03')
  const A = fund('A', '2022-01', '2023-03', 0.01) // 锚定，最早
  const Y = fund('Y', '2021-06', '2023-03', 0.005) // 早于 A
  const X = fund('X', '2023-02', '2023-03', [0.02, 0.03]) // 晚于 A，拼接

  it('锚定 A 自身 base=1.0 @ t_A', () => {
    const rebased = rebaseAnchored([A, X, Y], axis, 'A')
    const a = rebased.find(r => r.fund_id === 'A')!
    expect(a.isAnchor).toBe(true)
    expect(a.nav[0]).toBeCloseTo(1.01) // 2022-01 首月
    expect(a.nav[axis.indexOf('2023-03')]).toBeCloseTo(Math.pow(1.01, 15))
  })

  it('早于 A 的 Y 裁剪到 t_A，base=1.0 @ t_A（与 A 同起跑线）', () => {
    const rebased = rebaseAnchored([A, Y], axis, 'A')
    const y = rebased.find(r => r.fund_id === 'Y')!
    expect(y.nav[0]).toBeCloseTo(1.005) // 2022-01 = t_A，Y 自身收益 0.005
    expect(y.nav[axis.indexOf('2022-02')]).toBeCloseTo(1.005 * 1.005)
  })

  it('晚于 A 的 X 拼接：base_X == V_A(t_X−1)（R2 断言），拼接点现在恰好落在恒等基点上', () => {
    const rebased = rebaseAnchored([A, X], axis, 'A')
    const a = rebased.find(r => r.fund_id === 'A')!
    const x = rebased.find(r => r.fund_id === 'X')!
    // base_X = V_A(t_X − 1) = V_A(2023-01)
    const baseX = a.nav[axis.indexOf('2023-01')]!
    // monthBefore(X 首月)=2023-01 在轴上 -> X 自己也在此画出恒等基点=base_X，
    // 拼接点跟着 nav 的首个非 null 值一起前移到这里（比 X 首月更精确地"承接锚定基金上月累计值"）
    expect(x.nav[axis.indexOf('2023-01')]).toBeCloseTo(baseX)
    expect(x.nav[axis.indexOf('2023-02')]).toBeCloseTo(baseX * 1.02) // X 首月复利
    expect(x.splicePoint?.month).toBe('2023-01')
    expect(x.splicePoint?.value).toBeCloseTo(baseX)
  })

  it('后发基金晚于锚定结束：降级独立 base=1.0，degraded=true', () => {
    const A2 = fund('A2', '2022-01', '2022-03', 0.01) // 锚定 2022-01..03
    const X2 = fund('X2', '2024-01', '2024-02', 0.02) // 晚于 A2 结束
    const ax2 = monthsAxis('2022-01', '2024-02')
    const rebased = rebaseAnchored([A2, X2], ax2, 'A2')
    const x2 = rebased.find(r => r.fund_id === 'X2')!
    expect(x2.degraded).toBe(true)
    expect(x2.splicePoint).toBeUndefined()
    expect(x2.nav[ax2.indexOf('2024-01')]).toBeCloseTo(1.02) // base=1.0 × (1+0.02)
  })

  it('同期发行基金：base=1.0 @ t_A，无拼接点', () => {
    const Z = fund('Z', '2022-01', '2023-03', 0.01)
    const rebased = rebaseAnchored([A, Z], axis, 'A')
    const z = rebased.find(r => r.fund_id === 'Z')!
    expect(z.splicePoint).toBeUndefined()
    expect(z.nav[0]).toBeCloseTo(1.01)
  })
})

describe('drawdownSeries', () => {
  it('基于自身 nav，0 在顶向下为负', () => {
    // nav: 1.0, 1.1, 0.99, 1.2 -> DD: 0, 0, -0.1, 0
    const dd = drawdownSeries([1.0, 1.1, 0.99, 1.2])
    expect(dd[0]).toBeCloseTo(0)
    expect(dd[1]).toBeCloseTo(0)
    expect(dd[2]).toBeCloseTo(0.99 / 1.1 - 1)
    expect(dd[3]).toBeCloseTo(0)
  })
  it('null 透传', () => {
    expect(drawdownSeries([null, 1.0, null])).toEqual([null, 0, null])
  })
})

describe('rollingExcess', () => {
  const months = monthsAxis('2022-01', '2023-12') // 24 个月

  it('RBA null 月 -> 含该月的 12 个窗口全 null（修正1：不跳过凑 11 月）', () => {
    const f = fund('A', '2022-01', '2023-12', 0.01)
    const rba = months.map(() => 0.0435)
    rba[12] = null as unknown as number // 2023-01 缺失
    const re = rollingExcess(f, months, rba)
    // i=11 窗口 [0..11] 不含 12 -> 非 null
    expect(re[11]).not.toBeNull()
    // i=12..23 窗口均含 12 -> 恰好 12 连续 null
    const contagion = re.slice(12, 24)
    expect(contagion.length).toBe(12)
    expect(contagion.every(v => v === null)).toBe(true)
  })

  it('历史 <12 月基金在末段才有值', () => {
    const f = fund('A', '2022-01', '2023-12', 0.01)
    const rba = months.map(() => 0.0435)
    const re = rollingExcess(f, months, rba)
    for (let i = 0; i < 11; i++) expect(re[i]).toBeNull() // 不足 12 月
    expect(re[11]).not.toBeNull()
  })

  it('方案a：full 序列算后裁剪近1年窗口 -> 曲线完整（12 点非孤点）', () => {
    const f = fund('A', '2022-01', '2023-12', 0.01)
    const rba = months.map(() => 0.0435)
    const re = rollingExcess(f, months, rba) // 在 full 24 月上算
    const window12 = re.slice(12, 24) // 近1年窗口（最后12月）
    expect(window12.every(v => v !== null)).toBe(true) // 12 点全部有值，非只剩孤点
    expect(window12.length).toBe(12)
  })
})

describe('monthlyExcess', () => {
  it('e_t = r_fund − monthlyBench(rba)，缺失月 excess=null', () => {
    const months = ['2022-01', '2022-02']
    const f: FundReturns = { fund_id: 'A', fund_name: 'A',
      dates: ['2022-01-28', '2022-02-28'], returns: [0.01, 0.02] }
    const rba = [0.036, null]
    const me = monthlyExcess(f, months, rba)
    expect(me[0].excess).toBeCloseTo(0.01 - monthlyBench(0.036))
    expect(me[1].excess).toBeNull() // RBA 缺失
    expect(me[0].fundReturn).toBeCloseTo(0.01)
    expect(me[0].rbaRate).toBeCloseTo(0.036)
  })
})

describe('computeAxisMonths (锚定态用于热力图/图表的月份范围, 修 ExcessHeatmap 灰行 bug)', () => {
  it('锚定基金起点晚于其他已选基金时, 轴从锚定基金自己起点开始 (不含更早的空白年份)', () => {
    const months = monthsAxis('2000-01', '2005-12')
    const longFund = fund('LONG', '2000-01', '2005-12', 0.01) // 起点早
    const shortAnchor = fund('SHORT', '2004-01', '2005-12', 0.01) // 锚定, 起点晚
    const axis = computeAxisMonths(months, [longFund, shortAnchor], 'full', 'SHORT')
    expect(axis[0]).toBe('2004-01') // 不是 2000-01
    expect(axis[axis.length - 1]).toBe('2005-12')
  })

  it('后发基金晚于锚定结束时, 轴延伸容纳到该后发基金的末月', () => {
    const months = monthsAxis('2020-01', '2023-12')
    const anchor = fund('A', '2020-01', '2021-12', 0.01)
    const laterFund = fund('B', '2021-06', '2023-12', 0.01)
    const axis = computeAxisMonths(months, [anchor, laterFund], 'full', 'A')
    expect(axis[0]).toBe('2020-01')
    expect(axis[axis.length - 1]).toBe('2023-12')
  })
})

describe('withLeadingBaseMonths (给每条线补"起点前一月"恒等基点位置, 权威机构 Growth of $X 惯例)', () => {
  it('非锚定态: 每支基金各自 monthBefore(own 首月), 缺的补到轴最前面', () => {
    const axis = monthsAxis('2022-02', '2022-12') // 不含 2022-01/2021-12
    const f1 = fund('F1', '2022-02', '2022-12', 0.01) // 需要 2022-01
    const f2 = fund('F2', '2022-03', '2022-12', 0.01) // 需要 2022-02, 轴上已有
    const out = withLeadingBaseMonths(axis, [f1, f2], null)
    expect(out[0]).toBe('2022-01') // 补齐 f1 的基点
    expect(out).toContain('2022-02') // f2 的基点本来就在轴上, 不重复添加
    expect(out.filter(m => m === '2022-02').length).toBe(1)
  })

  it('锚定态: tFund<=tA 的基金基点位置统一用 monthBefore(tA), 不是自己更早的真实起点', () => {
    const axis = monthsAxis('2022-01', '2022-12')
    const anchor = fund('A', '2022-01', '2022-12', 0.01)
    const early = fund('EARLY', '2000-01', '2022-12', 0.01) // 真实起点早得多, 但会被裁剪到 tA
    const out = withLeadingBaseMonths(axis, [anchor, early], 'A')
    expect(out).toContain('2021-12') // monthBefore(tA)
    expect(out).not.toContain('1999-12') // 不是 early 自己真实起点前一月
  })

  it('锚定态: tFund>tA 的后发基金用自己的 monthBefore(tFund)', () => {
    const axis = monthsAxis('2022-01', '2022-12')
    const anchor = fund('A', '2022-01', '2022-12', 0.01)
    const later = fund('LATER', '2022-06', '2022-12', 0.01)
    const out = withLeadingBaseMonths(axis, [anchor, later], 'A')
    expect(out).toContain('2021-12') // anchor 自己的基点
    expect(out).toContain('2022-05') // later 自己的基点 monthBefore(2022-06)
  })

  it('都不缺时原样返回, 不重复计算/不改变已有顺序', () => {
    const axis = monthsAxis('2021-12', '2022-12')
    const f = fund('F', '2022-01', '2022-12', 0.01)
    const out = withLeadingBaseMonths(axis, [f], null)
    expect(out).toEqual(axis)
  })
})

describe('percentile (热力图色标裁剪)', () => {
  it('空数组返回 0', () => {
    expect(percentile([], 0.9)).toBe(0)
  })

  it('单一元素直接返回该值', () => {
    expect(percentile([0.05], 0.9)).toBe(0.05)
  })

  it('90 分位裁剪掉危机月异常值, 明显小于最大值', () => {
    // 模拟: 大量正常月份 (~0.005) + 一个 2008 式危机月 (0.15)
    const normal = Array.from({ length: 20 }, () => 0.005)
    const vals = [...normal, 0.15].sort((a, b) => a - b)
    const p90 = percentile(vals, 0.9)
    expect(p90).toBeLessThan(0.15) // 没被危机月拉爆色标
    expect(p90).toBeCloseTo(0.005, 2) // 仍贴近正常月份的量级
  })

  it('线性插值: 已知升序数组按位置插值', () => {
    expect(percentile([1, 2, 3, 4, 5], 0.5)).toBeCloseTo(3)
    expect(percentile([1, 2, 3, 4, 5], 0)).toBeCloseTo(1)
    expect(percentile([1, 2, 3, 4, 5], 1)).toBeCloseTo(5)
  })
})
