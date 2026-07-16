"""FastAPI 应用工厂：CORS、lifespan（建表+调度器）、路由聚合。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db


def create_app(enable_scheduler: bool = True) -> FastAPI:
    """创建 FastAPI 应用。测试传 enable_scheduler=False 跳过调度器。

    enable_scheduler 通过 lifespan 闭包捕获，不修改全局 settings--避免
    create_app 返回后 settings 被恢复、lifespan 延迟执行时读到旧值。
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """启动：建表 + 可选启动 RBA 调度器；关闭：停止调度器。"""
        init_db()
        scheduler = None
        if enable_scheduler and settings.SCHEDULER_ENABLED:
            from app.scheduler import start_scheduler
            scheduler = start_scheduler()
        try:
            yield
        finally:
            if scheduler is not None:
                from app.scheduler import shutdown_scheduler
                shutdown_scheduler(scheduler)

    app = FastAPI(title="固定收益基金分析 API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.routers import funds, metrics, anomalies, rba, ingest
    app.include_router(funds.router)
    app.include_router(metrics.router)
    app.include_router(anomalies.router)
    app.include_router(rba.router)
    app.include_router(ingest.router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


# uvicorn 入口
app = create_app()
