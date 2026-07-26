#!/usr/bin/env python3
"""开发/测试阶段专用: 换机场订阅当前出口节点, 缓解压测时自建搜索后端底层引擎
(bing/duckduckgo等) 对固定出口 IP 的限流 (`tavily替代方案-最终报告.md` 已
实测确认限流是 IP 级, 换 IP 后全部引擎瞬间恢复)。该自建搜索后端 (曾用
SearXNG) 已于 Spec G 下线并从代码中整体删除, 本脚本与 llm_ingest/search.py
的生产请求路径 (现纯 Tavily) 无关, 仅作通用 Clash 节点切换工具保留。

这个脚本只做一件事: 调用本地 Clash 系客户端 (ClashX / Clash Verge / mihomo /
sing-box 的 Clash-兼容控制面板均可) 的 controller API, 把某个策略组当前选中
的节点换成组内另一个。

项目开发/压测阶段结束后不需要这个脚本 -- 默认就是本地直连, 符合"日常使用量
小不会像压测那样被限流"的判断, 不用特地"切回去"。

用法:
  python3 tools/rotate_proxy.py --list-groups          # 第一次跑, 先看有哪些策略组
  python3 tools/rotate_proxy.py --list-nodes PROXY      # 某组下的节点列表
  python3 tools/rotate_proxy.py --current PROXY         # 当前选中的节点
  python3 tools/rotate_proxy.py --rotate PROXY          # 换成组内随机一个不同节点

环境变量 (按实际机场客户端控制面板设置改, 默认值是 Clash 系常见配置):
  CLASH_API_URL     默认 http://127.0.0.1:9090
  CLASH_API_SECRET  默认空 (面板开了密钥才需要)
  组名没有默认值 -- 不同订阅的策略组名不一样 ("PROXY"/"节点选择"/自定义),
  先跑 --list-groups 确认真实组名, 后续命令都传这个组名。
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from typing import Dict, List

import requests

DEFAULT_API_URL = "http://127.0.0.1:9090"
TIMEOUT = 5


def _api_url() -> str:
    return os.environ.get("CLASH_API_URL", DEFAULT_API_URL).rstrip("/")


def _headers() -> Dict[str, str]:
    secret = os.environ.get("CLASH_API_SECRET", "").strip()
    return {"Authorization": f"Bearer {secret}"} if secret else {}


# 能切换节点的组类型; 单个节点条目 (type=Shadowsocks/Vmess等) 不在此列, 排除掉
_SWITCHABLE_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance"}


def list_groups() -> Dict[str, List[str]]:
    r = requests.get(f"{_api_url()}/proxies", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()["proxies"]
    return {
        name: info["all"]
        for name, info in data.items()
        if info.get("all") and info.get("type") in _SWITCHABLE_TYPES
    }


def current_node(group: str) -> str:
    r = requests.get(f"{_api_url()}/proxies/{group}", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["now"]


def rotate(group: str) -> str:
    """随机切到组内当前节点以外的另一个, 返回新节点名。"""
    r = requests.get(f"{_api_url()}/proxies/{group}", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    info = r.json()
    candidates = [n for n in info.get("all", []) if n != info.get("now")]
    if not candidates:
        raise RuntimeError(f"组 {group} 没有除当前节点外的其他可选节点")
    pick = random.choice(candidates)
    put = requests.put(
        f"{_api_url()}/proxies/{group}", headers=_headers(),
        json={"name": pick}, timeout=TIMEOUT,
    )
    put.raise_for_status()
    return pick


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--list-groups", action="store_true")
    ap.add_argument("--list-nodes", metavar="GROUP")
    ap.add_argument("--current", metavar="GROUP")
    ap.add_argument("--rotate", metavar="GROUP")
    args = ap.parse_args(argv)

    try:
        if args.list_groups:
            for name, nodes in list_groups().items():
                print(f"{name}  ({len(nodes)} 节点)")
        elif args.list_nodes:
            for n in list_groups().get(args.list_nodes, []):
                print(n)
        elif args.current:
            print(current_node(args.current))
        elif args.rotate:
            print(f"切到: {rotate(args.rotate)}")
        else:
            ap.print_help()
            return 1
    except requests.RequestException as e:
        print(f"连不上控制面板 ({_api_url()}): {e}", file=sys.stderr)
        print(
            "检查: 1) 机场客户端有没有开着 2) CLASH_API_URL 端口对不对 "
            "3) 面板要不要密钥 (CLASH_API_SECRET)",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
