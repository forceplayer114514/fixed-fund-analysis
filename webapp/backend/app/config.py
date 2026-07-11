"""后端配置：通过环境变量覆盖默认值。"""
import os
from pathlib import Path

# 数据库默认放在仓库根目录 data/fund_analysis.db
_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent  # webapp/backend -> 仓库根
_DEFAULT_DB_PATH = _BASE_DIR / "data" / "fund_analysis.db"


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")
    # RBA 抓取相关（阶段2的 LLM 配置在此预留但不启用）
    RBA_BASE_URL: str = "https://www.rba.gov.au/"
    RBA_HISTORY_API: str = "https://api.db.nomics.world/v22/series/RBA/F1/FIRMMCRTD?observations=1"
    # Web
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    # 调度
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    RBA_CRON_HOUR: int = int(os.getenv("RBA_CRON_HOUR", "9"))  # 每天几点抓 RBA


settings = Settings()
