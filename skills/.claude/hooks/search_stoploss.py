#!/usr/bin/env python3
"""PreToolUse hook：连续搜索类工具调用无穿插达到阈值即 deny。

命中 SEARCH_TOOLS 就计数 +1；命中任何其他工具就清零（说明穿插了非
搜索操作，不算死循环）。计数达到 THRESHOLD 时 deny 这次调用，强制
模型停下换策略或问用户，而不是继续用近义改写重复搜索同一个事实。
按 session_id 存临时计数文件，不同会话互不影响。
"""
import json
import os
import sys
import tempfile

THRESHOLD = 6

SEARCH_TOOLS = {
    "mcp__search__search",
    "mcp__search__fetch",
    "mcp__search__research",
    "mcp__ScraplingServer__stealthy_fetch",
    "mcp__ScraplingServer__fetch",
}


def state_path(session_id):
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    return os.path.join(tempfile.gettempdir(), f"claude-search-stoploss-{safe_id}.count")


def read_count(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def write_count(path, n):
    with open(path, "w") as f:
        f.write(str(n))


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    session_id = data.get("session_id", "unknown")
    tool_name = data.get("tool_name", "")
    path = state_path(session_id)

    if tool_name not in SEARCH_TOOLS:
        write_count(path, 0)
        return

    count = read_count(path) + 1
    write_count(path, count)

    if count >= THRESHOLD:
        print(json.dumps({
            "systemMessage": f"止损触发：连续 {count} 次搜索类工具调用（最近一次 {tool_name}）未穿插其他操作。",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"已连续调用搜索类工具 {count} 次未穿插其他操作，必须停止搜索"
                    "并切换策略：AJAX/动态归档页用 stealthy_fetch(network_idle=true) 渲染；"
                    "探测需迭代调脚本 -> 派子代理，脚本代码不进主上下文。"
                    "或直接询问用户，不允许继续重复措辞搜索。"
                ),
            },
        }))


if __name__ == "__main__":
    main()
