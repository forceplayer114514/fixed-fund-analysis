"""sub2api HTTP 客户端 (Anthropic /v1/messages 格式,Gemini 后端)。

- 重试 + 超时 (>=60s)
- 遍历 content 拼所有 text block
- .env 从仓库根加载 (SUB2API_BASE_URL / SUB2API_KEY / SUB2API_MODEL)
- API key 只从 .env / 环境变量取, 绝不硬编
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 120  # s, >=60 per plan
DEFAULT_RETRIES = 2
DEFAULT_MAX_TOKENS = 2048  # Phase 0 峰值 1578


def load_env(env_path: Optional[Path] = None) -> None:
    """从仓库根 .env 加载, 不覆盖已存在的 env var."""
    if env_path is None:
        env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


@dataclass(frozen=True)
class ClientConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES

    @classmethod
    def from_env(cls) -> "ClientConfig":
        load_env()
        return cls(
            base_url=os.environ["SUB2API_BASE_URL"].rstrip("/"),
            api_key=os.environ["SUB2API_KEY"],
            model=os.environ["SUB2API_MODEL"],
        )


@dataclass(frozen=True)
class Response:
    text: str  # 所有 text block 拼接
    usage: Dict[str, Any]
    model: str
    raw: Dict[str, Any]
    latency_s: float
    search_sources: List[str] = field(default_factory=list)
    search_queries: List[str] = field(default_factory=list)


class ClientError(RuntimeError):
    pass


class Client:
    def __init__(self, cfg: Optional[ClientConfig] = None) -> None:
        self.cfg = cfg or ClientConfig.from_env()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def _post(self, payload: Dict[str, Any]) -> Response:
        url = self.cfg.base_url + "/v1/messages"
        last_err: Optional[Exception] = None
        for attempt in range(self.cfg.retries + 1):
            t0 = time.time()
            try:
                r = requests.post(url, headers=self._headers(), json=payload, timeout=self.cfg.timeout)
                latency = time.time() - t0
                if r.status_code != 200:
                    # 4xx 不重试
                    if 400 <= r.status_code < 500:
                        raise ClientError(f"HTTP {r.status_code}: {r.text[:500]}")
                    last_err = ClientError(f"HTTP {r.status_code}: {r.text[:500]}")
                    time.sleep(1.5 ** attempt)
                    continue
                data = r.json()
                texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
                full_text = "\n".join(texts)
                sources, queries = _parse_grounding(full_text)
                return Response(
                    text=full_text,
                    usage=data.get("usage", {}),
                    model=data.get("model", ""),
                    raw=data,
                    latency_s=latency,
                    search_sources=sources,
                    search_queries=queries,
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                time.sleep(1.5 ** attempt)
        raise ClientError(f"exhausted retries: {last_err}")

    def messages(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        extra_content: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        """纯文本 messages 调用."""
        content: List[Dict[str, Any]] = []
        if extra_content:
            content.extend(extra_content)
        content.append({"type": "text", "text": prompt})
        return self._post({
            "model": self.cfg.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        })

    def messages_with_pdf(
        self,
        prompt: str,
        pdf_bytes: bytes,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Response:
        """PDF (base64 image block) + 文本 prompt.  media_type: application/pdf."""
        b64 = base64.b64encode(pdf_bytes).decode()
        pdf_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
        return self.messages(prompt, max_tokens=max_tokens, extra_content=[pdf_block])

    def messages_with_search(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_uses: int = 5,
    ) -> Response:
        """联网搜索工具.  会降级到 gemini-2.5-flash.  搜索与读 PDF 拆两次调用."""
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens,
            "tools": [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_uses,
            }],
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
        return self._post(payload)


# ---------- grounding 解析 (sub2api 把 Gemini groundingMetadata 拍成 text) ----------

# text 里附加块形如:
#   ---
#   Web search queries: q1, q2, q3
#
#   Sources:
#   [1] [domain.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/...)
#   [2] [other.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/...)

_SRC_LINE = re.compile(r"\[\d+\]\s*\[([^\]]+)\]\(([^)]+)\)")
_QRY_LINE = re.compile(r"Web search queries:\s*(.+)")


def _parse_grounding(text: str) -> "tuple[List[str], List[str]]":
    """从 sub2api 返回文本里抠 grounding sources + queries."""
    sources: List[str] = []
    queries: List[str] = []
    for m in _SRC_LINE.finditer(text):
        url = m.group(2).strip()
        if url and url not in sources:
            sources.append(url)
    qm = _QRY_LINE.search(text)
    if qm:
        queries = [q.strip() for q in qm.group(1).split(",") if q.strip()]
    return sources, queries


def follow_redirect(url: str, timeout: int = 20) -> Optional[str]:
    """跟随 vertexaisearch grounding-api-redirect 拿到真实 URL.

    真站可能 403 (直接抓时反爬), 但重定向 chain 走完前 requests 会记 r.url.
    """
    try:
        r = requests.get(
            url, allow_redirects=True, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        return r.url
    except Exception:
        return None


def resolve_sources(sources: List[str], timeout: int = 20) -> List[str]:
    """批量展开 grounding redirect. 保留原顺序, 去重."""
    seen: List[str] = []
    for s in sources:
        real = follow_redirect(s, timeout=timeout) if "grounding-api-redirect" in s else s
        if real and real not in seen:
            seen.append(real)
    return seen
