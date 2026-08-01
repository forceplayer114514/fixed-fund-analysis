"""Grok agentic search 客户端 (Spec G).

Grok 与 Tavily 是两种东西, 接入时切勿按"再加一个检索 API"来理解:
  - Tavily 是检索: 给 query 返回一批 URL + snippet, 无 LLM 参与, 结果确定性高
  - Grok 是 agentic search: 自己在后台跑几十次搜索、读页面, 再由 LLM 给出**答案**。
    返回体里 search_sources 是**探索轨迹**(含噪声, 实测一次查询 24 条源里混进过
    完全无关的 SEC filing), content prose 才是**答案**。

硬约束 -- 只问"东西在哪一页", 绝不问它要 PDF 文件链接:
  实测 (Spec G 2.5) 问它 GCI 归档页上的月报 PDF, 3 轮都返回 5 个
  gci-inv-update-{jun,may,apr,mar,feb}-2026.pdf 并断言"retrieved directly from
  the Document Library on that page", 而该页真实只挂 1 份 -- 另外 4 份是把文件名
  里的月份 token 替换推出来的。**这 4 个编造 URL 全部 HTTP 200 且确实是 PDF**
  (该站把旧文件留在可预测路径), 所以"能下载成功"完全挡不住这类捏造。
  不给它这个题目, 它就没机会编。PDF 枚举一律走 discover2.probe_urls 抓页 + 正则。

503 upstream_unavailable 是中转站账号额度耗尽, 不是 Grok 能力问题 --
实测重试换账号即成功, 故 429/502/503 重试有效。
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .client import load_env

DEFAULT_BASE_URL = "https://grok2api.supernip.site"
DEFAULT_MODEL = "grok-chat-fast"
DEFAULT_TIMEOUT = 180
RETRY_STATUS = (429, 502, 503, 504)
# 退避逐次翻倍 (5/10/20 秒, 总窗口 35 秒)。原来固定 5 秒 x3 只有 15 秒 --
# 2026-08-01 实测中转站轮换账号比这慢, 4 次调用会全部撞在同一批坏账号上,
# 连着几轮摄取都挂在搜索这一步。
RETRY_SLEEP = 5
RETRIES = 3
PROMPT_DIR = Path(__file__).parent / "prompts"

_URL_RE = re.compile(r"https?://[^\s\)\]<>\"'，、）]+")


class GrokError(RuntimeError):
    pass


@dataclass(frozen=True)
class GrokAnswer:
    content: str                 # prose 正文 = 答案
    sources: List[str]           # search_sources = 探索轨迹 (含噪声)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchiveAnswer:
    """Grok 对"月报归档页在哪"的回答.

    刻意不含 pdf_urls -- 见模块 docstring 的硬约束。
    """
    issuer_domain: Optional[str]
    archive_url: Optional[str]
    sources: List[str] = field(default_factory=list)
    evidence: str = ""


def _config() -> Tuple[str, str, str]:
    """返 (base_url, api_key, model). key 缺失抛 GrokError."""
    load_env()
    base = os.environ.get("GROK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    key = os.environ.get("GROK_API_KEY", "").strip()
    model = os.environ.get("GROK_MODEL", DEFAULT_MODEL)
    if not key:
        raise GrokError("GROK_API_KEY 未设置 (检查 .env)")
    return base, key, model


def grok_ask(
    prompt: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = RETRIES,
) -> GrokAnswer:
    """单次 Grok 调用. 429/502/503/504 重试 (退避逐次翻倍), 耗尽抛 GrokError.

    注意: Grok 单次调用内部就会 fan-out 多条 query (实测一次给 24-37 条源),
    因此**不要**照抄 multi_query_search 的三次 query 模式, 一次调用即可,
    否则延迟三倍且无收益 (Spec G 2.7)。
    """
    base, key, model = _config()
    url = f"{base}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}

    last = ""
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            last = f"网络错误: {e}"
            if attempt < retries:
                time.sleep(RETRY_SLEEP * (2 ** attempt))
                continue
            raise GrokError(last) from e
        if r.status_code == 200:
            data = r.json()
            try:
                content = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError) as e:
                raise GrokError(f"返回体结构异常: {str(data)[:200]}") from e
            sources = [
                s.get("url", "") for s in (data.get("search_sources") or [])
                if s.get("url")
            ]
            return GrokAnswer(content=content, sources=sources, raw=data)
        last = f"HTTP {r.status_code}: {r.text[:200]}"
        if r.status_code in RETRY_STATUS and attempt < retries:
            time.sleep(RETRY_SLEEP * (2 ** attempt))
            continue
        raise GrokError(last)
    raise GrokError(last or "retries_exhausted")


def _load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text()


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """从回文剥出第一个 JSON 对象, 失败返 None."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def answer_archive(
    fund_name: str,
    issuer: str,
    asx_code: Optional[str] = None,
) -> ArchiveAnswer:
    """问 Grok: 这支基金的月报归档页/下载中心在哪, 官网域名是什么.

    只问页面, 不问文件 (见模块 docstring)。
    """
    tmpl = _load_prompt("grok_archive.md")
    prompt = (
        tmpl.replace("{fund_name}", fund_name)
            .replace("{issuer}", issuer)
            .replace("{asx_hint}", f", ASX code: {asx_code}" if asx_code else "")
    )
    ans = grok_ask(prompt)
    parsed = _parse_json(ans.content)
    obj = parsed or {}
    archive_url = obj.get("archive_url") or None
    issuer_domain = obj.get("issuer_domain") or None
    # 兜底: 只在 JSON 解析彻底失败 (Grok 不听话直接说人话) 时才生效。
    # JSON 解析成功且明确给 archive_url: null 是 Grok 遵循 prompt "找不到
    # 用 null" 指示的诚实回答, 绝不能被正则从 evidence/issuer_domain 等
    # 其它字段里抓一个不相关的 URL 顶替 (Task 8 审查发现)。
    if parsed is None:
        urls = [u.rstrip(".,;") for u in _URL_RE.findall(ans.content)]
        archive_url = urls[0] if urls else None
    return ArchiveAnswer(
        issuer_domain=issuer_domain,
        archive_url=archive_url,
        sources=ans.sources,
        evidence=str(obj.get("evidence") or ""),
    )


def answer_fundmonitors_id(fund_name: str) -> Optional[Tuple[int, str]]:
    """问 Grok: 这支基金在 fundmonitors 的 FundID + AccCode.

    上游失败返 None (不抛), 让 fundmonitors.probe 走既有的 no_fundid 分支。

    注意 (Spec G 2.4): 多份额类别基金上 Grok 会给错编号 (实测 Bentham 3 轮
    给了 3315/622/3315, 而 DB 真值是 3312; Tavily 同题也拿不到 3312 --
    这是数据源本身的歧义)。下游的 name-fuzzy 闸必须保留兜错源。
    """
    tmpl = _load_prompt("grok_fundmonitors.md")
    prompt = tmpl.replace("{fund_name}", fund_name)
    try:
        ans = grok_ask(prompt)
    except GrokError:
        return None
    obj = _parse_json(ans.content) or {}
    fid = obj.get("fund_id")
    if not isinstance(fid, int):
        return None
    acc = obj.get("acc_code") or ""
    return (fid, str(acc))
