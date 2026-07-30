import { describe, expect, it } from 'vitest'

/**
 * 用 Vite 的 import.meta.glob(?raw) 直接读文件内容，不走 node:fs/node:path/__dirname——
 * 项目未装 @types/node（任务约束禁止新增依赖），改 tsconfig 排除测试文件同样禁止
 * （Task 1 曾这么绕过、被 review 打回，见 palette.test.ts 改用 ?raw 导入的
 * commit 2eb33b9）。这里沿用同一套已获认可的规避方式。
 */
const modules = {
  ...import.meta.glob('../components/**/*.tsx', { eager: true, query: '?raw', import: 'default' }),
  ...import.meta.glob('../pages/**/*.tsx', { eager: true, query: '?raw', import: 'default' }),
} as Record<string, string>

/**
 * 尚未迁移到字典的文件。每完成一个文件的 i18n 改造就从这里删掉它。
 * Task 14 会断言此数组为空 —— 空数组之后，任何新页面写死中文都会让本测试报红。
 */
const PENDING_FILES: string[] = [
  'components/AnomalyTable.tsx',
  'pages/Anomalies.tsx',
  'pages/Dashboard.tsx',
  'pages/FundManagement.tsx',
]

/** 剥掉块注释与行注释；注释里的中文是允许的（说明代码用）。 */
function stripComments(code: string): string {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

describe('组件与页面不得出现中文字面量', () => {
  const relFiles = Object.keys(modules).map(p => p.replace(/^\.\.\//, '')).sort()

  it('扫到了文件（防止路径写错导致空跑通过）', () => {
    expect(relFiles.length).toBeGreaterThan(10)
  })

  it.each(relFiles)('%s 无中文字面量', rel => {
    if (PENDING_FILES.includes(rel)) return
    const content = modules[`../${rel}`]
    const stripped = stripComments(content)
    const offenders = stripped.split('\n')
      .map((line, i) => ({ line: i + 1, text: line }))
      .filter(x => /[一-龥]/.test(x.text))
      .map(x => `${rel}:${x.line}: ${x.text.trim()}`)
    expect(offenders).toEqual([])
  })

  it('白名单里的文件都真实存在（防止改名后闸门失效）', () => {
    const existing = new Set(relFiles)
    const stale = PENDING_FILES.filter(p => !existing.has(p))
    expect(stale).toEqual([])
  })
})
