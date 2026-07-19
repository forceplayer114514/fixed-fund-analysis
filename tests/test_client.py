"""llm_ingest/client.py 的 grounding 文本解析测试。"""
import pytest

from llm_ingest.client import _parse_grounding


class TestParseGroundingRedirectLabelBug:
    """2026-07-19 sub2api web_search 兜底路径量化测试发现: Gemini 有时把
    grounding markdown link 的 label 写成跳板域名本身
    (vertexaisearch.cloud.google.com), 而不是目标网站域名。旧正则把这种
    跳板域名当成"搜到的结果"塞进 sources, 15次调用里4次(27%)是这种伪触发
    (sources 里只有这一条无效数据, 本该判"没搜到任何东西")。
    """

    def test_md_link_grounding_redirect_label_dropped(self):
        text = (
            "[vertexaisearch.cloud.google.com]"
            "(https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123)"
        )
        sources, _ = _parse_grounding(text)
        assert sources == []

    def test_numbered_src_line_grounding_redirect_label_dropped(self):
        text = (
            "[1] [vertexaisearch.cloud.google.com]"
            "(https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123)"
        )
        sources, _ = _parse_grounding(text)
        assert sources == []

    def test_real_domain_label_still_captured(self):
        """确保修复没有误伤真实域名 -- 回归防线。"""
        text = "[hellostake.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/xyz)"
        sources, _ = _parse_grounding(text)
        assert sources == ["https://hellostake.com"]

    def test_mixed_real_and_redirect_label_only_real_kept(self):
        text = (
            "[1] [hellostake.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/xyz)\n"
            "[2] [vertexaisearch.cloud.google.com](https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc)"
        )
        sources, _ = _parse_grounding(text)
        assert sources == ["https://hellostake.com"]
