"""异常审计与人工纠错路由（Task 5 填充端点）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["anomalies"])
