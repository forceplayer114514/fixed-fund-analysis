import { describe, it, expect } from 'vitest'
import {
  monthlyBench, buildNav, monthBefore, rebasePlain, rebaseAnchored,
  drawdownSeries, rollingExcess, monthlyExcess,
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
  it('各基金自首月起 base=1.0，首月前为 null', () => {
    const f = fund('A', '2022-02', '2022-04', 0.01)
    const axis = monthsAxis('2022-01', '2022-04')
    const r = rebasePlain(f, axis)
    expect(r.nav[0]).toBeNull() // 2022-01 在基金发行前
    expect(r.nav[1]).toBeCloseTo(1.01) // 2022-02 首月 base=1.0
    expect(r.nav[2]).toBeCloseTo(1.01 * 1.01)
    expect(r.isAnchor).toBe(false)
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

  it('晚于 A 的 X 拼接：base_X == V_A(t_X−1)（R2 断言），拼接点空心圆于 X 首点', () => {
    const rebased = rebaseAnchored([A, X], axis, 'A')
    const a = rebased.find(r => r.fund_id === 'A')!
    const x = rebased.find(r => r.fund_id === 'X')!
    // base_X = V_A(t_X − 1) = V_A(2023-01)
    const baseX = a.nav[axis.indexOf('2023-01')]!
    // X 首点 = base_X × (1 + r_X 首月)
    expect(x.splicePoint?.month).toBe('2023-02')
    expect(x.splicePoint?.value).toBeCloseTo(baseX * 1.02)
    // 直接断言：X 首点值 / (1 + r_X 首月) == V_A(t_X − 1)
    expect(x.splicePoint!.value / 1.02).toBeCloseTo(baseX)
    expect(x.splicePoint!.value / 1.02).toBeCloseTo(Math.pow(1.01, 13))
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
