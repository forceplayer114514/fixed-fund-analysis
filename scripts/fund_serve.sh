#!/bin/zsh
# 启动固定收益基金分析前后端（后台常驻），等待就绪后打开浏览器。
# 被 Desktop/启动基金分析系统.app 调用；也可在终端直接运行。
# 退出码：0 = 就绪并已打开浏览器；1 = 40 秒内未就绪。

REPO=/Users/chong/Desktop/fixed_fund_analysis
export PATH="/Users/chong/.local/bin:/usr/local/bin:/usr/bin:/bin:/Users/chong/Library/Python/3.9/bin:$PATH"

BACKEND_LOG=/tmp/fund_uvicorn.log
FRONTEND_LOG=/tmp/fund_vite.log

# 后端
if lsof -i :8000 -t > /dev/null 2>&1; then
  echo "✓ 后端已在运行 (端口 8000)"
else
  echo "→ 启动后端..."
  (cd "$REPO/webapp/backend" && nohup python3 -m uvicorn app.main:app --port 8000 --reload \
     > "$BACKEND_LOG" 2>&1 < /dev/null &)
fi

# 前端
if lsof -i :5173 -t > /dev/null 2>&1; then
  echo "✓ 前端已在运行 (端口 5173)"
else
  echo "→ 启动前端..."
  (cd "$REPO/webapp/frontend" && nohup npm run dev \
     > "$FRONTEND_LOG" 2>&1 < /dev/null &)
fi

# 等待就绪
echo "→ 等待服务就绪..."
for i in {1..40}; do
  if curl -s http://localhost:8000/health > /dev/null 2>&1 \
     && curl -s http://localhost:5173 > /dev/null 2>&1; then
    echo "✓ 服务就绪"
    open http://localhost:5173
    exit 0
  fi
  sleep 1
done

echo "⚠ 服务未在 40 秒内就绪，日志：$BACKEND_LOG / $FRONTEND_LOG"
exit 1
