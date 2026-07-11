"""基金 CRUD + 手动重算路由（Task 2 填充端点）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/funds", tags=["funds"])
