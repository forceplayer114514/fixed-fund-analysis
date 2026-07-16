#!/usr/bin/env python3
"""PreToolUse hook：拦截绕过 lib.ingest gate_check 的直接写库/写缓存路径。

三条规则（命中任一即 deny，解析失败静默放行）：
1. Bash 命令含 `sqlite3` 且引用 .db 文件，但未带 `-readonly` -> deny。
   （sqlite3 CLI 写库完全绕过 gate_check/solver 反捏造断言，db.py 自己
   声明 write token 只是"软隔离"，这里在 harness 层补一道硬拦截。）
2. Bash 命令含 `FUND_DB_WRITE_TOKEN` 但不是 `-m lib.ingest` 调用 -> deny。
   （write token 只准配官方 CLI，禁止内联脚本持 token 绕过 CLI 的 gate。）
3. Write/Edit/NotebookEdit 的 file_path 命中 *.db 或 data/pdf_cache/ -> deny。
   （缓存文件即 source 可追溯载体，禁手工改；.db 只能被 lib.ingest 写。）
"""
import json
import re
import sys

DB_FILE_RE = re.compile(r"\S*\.db\b|\bFUND_DB_PATH\b")
SQLITE3_RE = re.compile(r"\bsqlite3\b")
READONLY_RE = re.compile(r"-readonly\b")
WRITE_TOKEN_RE = re.compile(r"\bFUND_DB_WRITE_TOKEN\b")
LIB_INGEST_RE = re.compile(r"-m\s+lib\.ingest\b")
PDF_CACHE_RE = re.compile(r"data/pdf_cache/")
DB_PATH_RE = re.compile(r"\.db($|[\s'\"])")


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""

        if SQLITE3_RE.search(command) and DB_FILE_RE.search(command) \
                and not READONLY_RE.search(command):
            deny(
                "sqlite3 CLI 直接操作 fund_analysis.db 会绕过 lib.ingest 的 "
                "gate_check/反捏造校验，禁止。写库必须走 `python3 -m lib.ingest "
                "<command>`；只读查询请加 `sqlite3 -readonly` 或改用 lib.db 的查询函数。"
            )
            return

        if WRITE_TOKEN_RE.search(command) and not LIB_INGEST_RE.search(command):
            deny(
                "FUND_DB_WRITE_TOKEN 只准配 `python3 -m lib.ingest` 调用。"
                "检测到该 token 出现在非 lib.ingest 命令中（如内联 python3 -c 脚本），"
                "这会绕过 CLI 的 gate_check，禁止。"
            )
            return

    if tool_name in ("Write", "Edit", "NotebookEdit"):
        file_path = tool_input.get("file_path", "") or ""
        if DB_PATH_RE.search(file_path) or PDF_CACHE_RE.search(file_path):
            deny(
                "禁止用 Write/Edit 直接改动 .db 文件或 data/pdf_cache/ 下的缓存文件。"
                "数据库只能经 lib.ingest CLI 写入；PDF 缓存是 source 可追溯载体，"
                "手工修改会破坏追溯链。"
            )
            return


if __name__ == "__main__":
    main()
