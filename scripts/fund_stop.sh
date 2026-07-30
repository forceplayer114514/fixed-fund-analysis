#!/bin/zsh
# 停止固定收益基金分析前后端。
# 被 Desktop/停止基金分析系统.app 调用；也可在终端直接运行。
# 输出一行人类可读结果（.app 拿去做通知内容）。

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

stopped=()

kill_port() {
  local port=$1 label=$2
  local pids
  pids=$(lsof -ti :$port 2>/dev/null)
  [[ -z "$pids" ]] && return 1

  echo "$pids" | xargs kill 2>/dev/null

  # 最多等 5 秒优雅退出，仍在则强杀
  for i in {1..10}; do
    pids=$(lsof -ti :$port 2>/dev/null)
    [[ -z "$pids" ]] && break
    sleep 0.5
  done
  pids=$(lsof -ti :$port 2>/dev/null)
  [[ -n "$pids" ]] && echo "$pids" | xargs kill -9 2>/dev/null

  stopped+=("$label")
  return 0
}

kill_port 8000 "后端"
kill_port 5173 "前端"

if (( ${#stopped[@]} == 0 )); then
  echo "服务本来就没在运行"
else
  echo "已停止：${(j:、:)stopped}"
fi
