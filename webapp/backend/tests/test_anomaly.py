"""MAD 异常检测测试。移植自 scripts/anomaly_detection.py 逻辑。"""
import pytest

from app.anomaly import detect_anomalies


@pytest.mark.unit
def test_detect_anomalies_finds_outlier():
    # 12个正常值(~0.005) + 1个极端值(0.5)
    ts = [{"date": f"2025-{m:02d}-28", "net_return": 0.005} for m in range(1, 13)]
    ts.append({"date": "2026-01-31", "net_return": 0.5})
    anomalies = detect_anomalies(ts, threshold_sigma=3.0)
    assert len(anomalies) == 1
    assert anomalies[0]["date"] == "2026-01-31"
    assert anomalies[0]["value"] == pytest.approx(0.5)
    assert anomalies[0]["z_score"] > 3.0


@pytest.mark.unit
def test_detect_anomalies_needs_min_12_months():
    # 不足12个月 -> 返回空
    ts = [{"date": f"2025-0{m}-28", "net_return": 0.005} for m in range(1, 7)]
    assert detect_anomalies(ts) == []


@pytest.mark.unit
def test_detect_anomalies_zero_returns_ignored():
    # net_return == 0 的点不参与统计也不报告
    ts = [{"date": f"2025-{m:02d}-28", "net_return": 0.005} for m in range(1, 13)]
    ts.append({"date": "2026-01-31", "net_return": 0.0})
    anomalies = detect_anomalies(ts)
    assert len(anomalies) == 0


@pytest.mark.unit
def test_detect_anomalies_preserves_commentary_truth():
    ts = [{"date": f"2025-{m:02d}-28", "net_return": 0.005} for m in range(1, 13)]
    ts.append({"date": "2026-01-31", "net_return": 0.5, "commentary_truth": 0.005})
    anomalies = detect_anomalies(ts)
    assert anomalies[0]["commentary_truth"] == pytest.approx(0.005)
