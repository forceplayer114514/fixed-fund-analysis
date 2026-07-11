"""MAD（中位数绝对偏差）异常检测。移植自 scripts/anomaly_detection.py。

使用稳健统计量（中位数 + MAD）代替均值+标准差，免疫极端值对统计量的污染。
"""
from __future__ import annotations

import statistics


def detect_anomalies(time_series: list[dict], threshold_sigma: float = 3.0) -> list[dict]:
    """检测月度收益率时序中的异常点。

    Args:
        time_series: 数据点列表，每个含 date, net_return, 可选 commentary_truth。
        threshold_sigma: 判定门禁（MAD 分数绝对值 >= 此值视为异常），默认 3.0。

    Returns:
        异常点 dict 列表，每个含 date, value, z_score, threshold_sigma, mean, stdev, commentary_truth。
    """
    returns = [dp["net_return"] for dp in time_series
               if "net_return" in dp and dp["net_return"] != 0.0]
    if len(returns) < 12:
        return []  # 至少需要一年数据才有统计意义

    median = statistics.median(returns)
    abs_deviations = [abs(x - median) for x in returns]
    mad = statistics.median(abs_deviations)

    # 由 MAD 估计标准差：std = MAD * 1.4826。MAD 为 0 时用极小值避免除零
    robust_stdev = mad * 1.4826 if mad != 0 else 1e-6

    anomalies = []
    for dp in time_series:
        ret = dp.get("net_return")
        if ret is None or ret == 0.0:
            continue
        mad_score = (ret - median) / robust_stdev
        if abs(mad_score) >= threshold_sigma:
            anomalies.append({
                "date": dp["date"],
                "value": ret,
                "z_score": mad_score,
                "threshold_sigma": threshold_sigma,
                "mean": median,  # 稳健均值用中位数
                "stdev": robust_stdev,
                "commentary_truth": dp.get("commentary_truth", None),
            })
    return anomalies
