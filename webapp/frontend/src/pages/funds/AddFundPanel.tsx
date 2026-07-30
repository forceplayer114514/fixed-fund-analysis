import type { Dispatch, SetStateAction } from 'react'

export interface AddFormState {
  fund_id: string
  fund_name: string
  apir_code: string
  confirmed_url: string
  issuer: string
  issuer_domain: string
  asx_code: string
  // Spec G: 搜索引擎选择, 每次摄取生效, 不记在基金上
  search_engine: 'tavily' | 'grok'
}

interface AddFundPanelProps {
  open: boolean
  form: AddFormState
  setForm: Dispatch<SetStateAction<AddFormState>>
  showAdvanced: boolean
  setShowAdvanced: Dispatch<SetStateAction<boolean>>
  error: string
  submitting: boolean
  onSubmit: () => void
}

export default function AddFundPanel({
  open,
  form,
  setForm,
  showAdvanced,
  setShowAdvanced,
  error,
  submitting,
  onSubmit,
}: AddFundPanelProps) {
  if (!open) return null

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs text-gray-500 block mb-1">
          基金名 <span className="text-red-500">*</span>
          <span className="text-gray-400 ml-1">(唯一必填)</span>
        </label>
        <input
          className="w-full text-sm border border-gray-200 rounded px-3 py-2"
          value={form.fund_name}
          onChange={e => setForm({ ...form, fund_name: e.target.value })}
          placeholder="如 Bentham Global Income Fund"
        />
        <div className="text-xs text-gray-400 mt-1">
          提交后 Gemini 会自动联网找归档页并抓月度数据。
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-sm font-medium mb-1">搜索引擎</label>
        <div className="flex gap-4">
          <label className="flex items-center gap-1 text-sm">
            <input
              type="radio"
              name="search_engine"
              value="tavily"
              checked={form.search_engine === 'tavily'}
              onChange={() => setForm({ ...form, search_engine: 'tavily' })}
            />
            Tavily（快，确定性高）
          </label>
          <label className="flex items-center gap-1 text-sm">
            <input
              type="radio"
              name="search_engine"
              value="grok"
              checked={form.search_engine === 'grok'}
              onChange={() => setForm({ ...form, search_engine: 'grok' })}
            />
            Grok（慢 15–20 秒，直接给答案）
          </label>
        </div>
      </div>

      <button
        type="button"
        className="text-xs text-gray-500 hover:text-gray-700 underline"
        onClick={() => setShowAdvanced(v => !v)}
      >
        {showAdvanced ? '▼' : '▶'} 高级选项 (全部选填, 用于加速/纠错定位)
      </button>

      {showAdvanced && (
        <div className="space-y-3 border-l-2 border-gray-100 pl-3">
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              fund_id <span className="text-gray-400">(选填 -- 留空由基金名自动生成 slug)</span>
            </label>
            <input
              className="w-full text-sm border border-gray-200 rounded px-3 py-2"
              value={form.fund_id}
              onChange={e => setForm({ ...form, fund_id: e.target.value })}
              placeholder="如 bentham_global_income_fund"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">APIR 代码</label>
            <input
              className="w-full text-sm border border-gray-200 rounded px-3 py-2"
              value={form.apir_code}
              onChange={e => setForm({ ...form, apir_code: e.target.value })}
              placeholder="如 ETL5010AU"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">归档页 URL (跳过搜索)</label>
            <input
              className="w-full text-sm border border-gray-200 rounded px-3 py-2"
              value={form.confirmed_url}
              onChange={e => setForm({ ...form, confirmed_url: e.target.value })}
              placeholder="https://.../monthly-reports"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">发行商 (加速搜索)</label>
            <input
              className="w-full text-sm border border-gray-200 rounded px-3 py-2"
              value={form.issuer}
              onChange={e => setForm({ ...form, issuer: e.target.value })}
              placeholder="如 Bentham Asset Management"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">发行商官网域名</label>
            <input
              className="w-full text-sm border border-gray-200 rounded px-3 py-2"
              value={form.issuer_domain}
              onChange={e => setForm({ ...form, issuer_domain: e.target.value })}
              placeholder="如 benthamam.com"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">ASX 代码</label>
            <input
              className="w-full text-sm border border-gray-200 rounded px-3 py-2"
              value={form.asx_code}
              onChange={e => setForm({ ...form, asx_code: e.target.value })}
              placeholder="如 MXT"
            />
          </div>
        </div>
      )}

      {error && <div className="text-xs text-red-500">{error}</div>}
      <button
        className="w-full text-sm bg-[#1a1a2e] text-white py-2 rounded-lg hover:bg-[#2a2a4e] disabled:opacity-50"
        onClick={onSubmit}
        disabled={submitting}
      >
        {submitting ? '起任务中…' : '开始 LLM 摄取'}
      </button>
    </div>
  )
}
