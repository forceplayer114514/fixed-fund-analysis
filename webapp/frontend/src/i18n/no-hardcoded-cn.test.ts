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
const PENDING_FILES: string[] = []

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

/**
 * Task 14 闸门收口：
 * 1) 断言 PENDING_FILES 恒为空 —— 此后任何新页面写死中文都会让上面那个闸门报红。
 * 2) 新增硬编码颜色 / 残留 dark: 类名扫描，覆盖与上面同一批文件（components/**、pages/**）。
 *    本项目未装 @types/node，沿用文件顶部注释里说明的 import.meta.glob(?raw) 方式读取源码，
 *    不用 node:fs/node:path（brief 里的 SCAN_DIRS/walk/SRC/join/relative/readFileSync 在
 *    本仓库里没有对应实现，这里改用已有的 modules/stripComments 达到同样的扫描范围）。
 */
describe('闸门收口', () => {
  it('待迁移白名单已清空（此后任何新页面写死中文都会报红）', () => {
    expect(PENDING_FILES).toEqual([])
  })

  const relFiles = Object.keys(modules).map(p => p.replace(/^\.\.\//, '')).sort()

  it.each(relFiles)('%s 不含硬编码颜色与 dark: 类名', rel => {
    const content = modules[`../${rel}`]
    const stripped = stripComments(content)
    const offenders = stripped.split('\n')
      .map((text, i) => ({ line: i + 1, text }))
      .filter(x =>
        /#[0-9a-fA-F]{3,8}\b/.test(x.text) ||
        /\bdark:/.test(x.text) ||
        /\b(?:bg|text|border)-(?:gray|slate|zinc|neutral|stone|cyan|red|amber|green|blue)-\d{2,3}\b/.test(x.text),
      )
      .map(x => `${rel}:${x.line}: ${x.text.trim()}`)
    expect(offenders).toEqual([])
  })
})
