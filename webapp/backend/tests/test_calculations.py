"""基础计算函数测试，断言复用 tests/test_metrics.py 以保证移植精度。"""
import pytest

from app.calculations import (
    calculate_annualized_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
    calculate_max_drawdown_detail,
    calculate_information_ratio,
    calculate_excess_win_rate,
    calculate_max_consecutive_underperform,
    calculate_autocorrelation,
    unsmooth_returns,
    should_apply_geltner,
    compute_all_metrics,
)


@pytest.mark.unit
def test_calculate_annualized_return():
    assert calculate_annualized_return(1.1025, 6, "TestFund") == pytest.approx(0.21550625)
    with pytest.raises(ValueError) as excinfo:
        calculate_annualized_return(1.05, 0, "TestFund")
    assert "[TestFund]" in str(excinfo.value) and "n_months=0" in str(excinfo.value)


@pytest.mark.unit
def test_calculate_annualized_volatility():
    assert calculate_annualized_volatility([0.01, 0.02, 0.03], "TestFund") == pytest.approx(0.03464101615)
    assert calculate_annualized_volatility([0.01], "TestFund") == 0.0
    assert calculate_annualized_volatility([], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_max_drawdown():
    assert calculate_max_drawdown([100.0, 105.0, 94.5, 91.35, 110.0], "TestFund") == pytest.approx(-0.13)
    with pytest.raises(ValueError) as excinfo:
        calculate_max_drawdown([0.0, 10.0], "TestFund")
    assert "peak=0.0" in str(excinfo.value)
    assert calculate_max_drawdown([], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_max_drawdown_detail():
    # nav=[1.0,1.10,0.95,0.90,1.05,1.12]: 前峰1.10@idx1, 谷底0.90@idx3,
    # 恢复: idx5=1.12>=1.10 -> recovery_idx=5, months=5-3=2, recovered=True
    d = calculate_max_drawdown_detail([1.0, 1.10, 0.95, 0.90, 1.05, 1.12], "TestFund")
    assert d["max_drawdown"] == pytest.approx((0.90 - 1.10) / 1.10)
    assert d["recovery_months"] == 2
    assert d["recovered"] is True
    # 未恢复：谷底后未回到前峰
    d2 = calculate_max_drawdown_detail([1.0, 1.10, 0.95, 0.90, 0.92, 0.95], "TestFund")
    assert d2["max_drawdown"] == pytest.approx((0.90 - 1.10) / 1.10)
    assert d2["recovered"] is False
    assert d2["recovery_months"] == 5 - 3  # 末月idx - trough idx = 2
    # 无回撤（单调递增）
    d3 = calculate_max_drawdown_detail([1.0, 1.05, 1.10, 1.15], "TestFund")
    assert d3["max_drawdown"] == 0.0
    assert d3["recovery_months"] is None
    assert d3["recovered"] is True
    # 空序列
    assert calculate_max_drawdown_detail([], "TestFund") == {
        "max_drawdown": 0.0, "recovery_months": None, "recovered": True}


@pytest.mark.unit
def test_calculate_information_ratio():
    # excess=[0.01,-0.005,0.008,-0.003]: 手工算 mean/std(ddof=1)*sqrt(12)
    excess = [0.01, -0.005, 0.008, -0.003]
    n = len(excess)
    mean_e = sum(excess) / n
    var = sum((e - mean_e) ** 2 for e in excess) / (n - 1)
    std_e = var ** 0.5
    expected = mean_e / std_e * (12.0 ** 0.5)
    assert calculate_information_ratio(excess) == pytest.approx(expected)
    # n<2 -> None
    assert calculate_information_ratio([0.01]) is None
    assert calculate_information_ratio([]) is None
    # std=0（全部相同）-> None
    assert calculate_information_ratio([0.01, 0.01, 0.01]) is None


@pytest.mark.unit
def test_excess_win_rate():
    # [0.02, -0.01, 0.03, 0.0] -> 正数2个 -> 2/4 = 0.5（0.0 不算跑赢）
    assert calculate_excess_win_rate([0.02, -0.01, 0.03, 0.0]) == pytest.approx(0.5)
    assert calculate_excess_win_rate([0.02, 0.03]) == pytest.approx(1.0)
    assert calculate_excess_win_rate([]) == 0.0


@pytest.mark.unit
def test_max_consecutive_underperform():
    # excess = [0.02, -0.01, -0.03, 0.04, -0.01, -0.02, -0.01]
    # 连续 <= 0 的块：[-0.01,-0.03]长2, [-0.01,-0.02,-0.01]长3
    assert calculate_max_consecutive_underperform([0.02, -0.01, -0.03, 0.04, -0.01, -0.02, -0.01]) == 3
    # 全部跑赢 -> 0
    assert calculate_max_consecutive_underperform([0.02, 0.03]) == 0
    # 空列表 -> 0
    assert calculate_max_consecutive_underperform([]) == 0


@pytest.mark.unit
def test_max_underperform_rba_missing_breaks_streak():
    """决策5：RBA 缺失月(None)中断跑输 streak，不静默拼接成连续。"""
    # 跑输、RBA缺失、跑输 -> 压缩会算成连续2，对齐序列正确算成1
    assert calculate_max_consecutive_underperform([-0.01, None, -0.02]) == 1
    # 连续两月跑输中间无缺失 -> 2
    assert calculate_max_consecutive_underperform([-0.01, -0.02, 0.01]) == 2
    # 全 None -> 0
    assert calculate_max_consecutive_underperform([None, None]) == 0
    # 持平(0)算跑输；None 在中间打断
    assert calculate_max_consecutive_underperform([0.0, None, 0.0]) == 1


@pytest.mark.unit
def test_calculate_autocorrelation():
    # 完全正自相关序列：phi 接近 1（50 点单调平滑序列，避免重复锯齿破坏自相关）
    phi, q_stat = calculate_autocorrelation([0.01 * i for i in range(1, 51)], "TestFund")
    assert phi > 0.9
    assert q_stat > 0
    # 短序列 -> (0.0, 0.0)
    assert calculate_autocorrelation([0.01], "TestFund") == (0.0, 0.0)


@pytest.mark.unit
def test_unsmooth_returns():
    assert unsmooth_returns([0.01, 0.015, 0.02], 0.5, "TestFund") == pytest.approx([0.01, 0.02, 0.025])
    with pytest.raises(ValueError) as excinfo:
        unsmooth_returns([0.01, 0.02], 0.999, "TestFund")
    assert "phi=0.999" in str(excinfo.value)
    assert unsmooth_returns([], 0.5, "TestFund") == []


@pytest.mark.unit
def test_should_apply_geltner_three_firewalls():
    # 全部通过：n>=36, Q>3.841, 0<=phi<=0.85
    assert should_apply_geltner(36, 0.5, 10.0) is True
    # 防火墙1：n < 36
    assert should_apply_geltner(35, 0.5, 10.0) is False
    # 防火墙2：Q <= 3.841
    assert should_apply_geltner(36, 0.5, 2.0) is False
    # 防火墙3：phi < 0
    assert should_apply_geltner(36, -0.1, 10.0) is False
    # 防火墙3：phi > 0.85
    assert should_apply_geltner(36, 0.9, 10.0) is False


@pytest.mark.unit
def test_compute_all_metrics_short_history():
    """不足36个月：跳过去平滑，un 指标 == orig 指标，is_geltner_applied=0。"""
    returns = [0.005, 0.006, 0.004, 0.007, 0.005, 0.006]
    rf_rates = [0.0435] * 6  # 6个月，恒定 RBA 4.35%
    m = compute_all_metrics(returns, rf_rates, "ShortFund")
    assert m["history_months"] == 6
    assert m["excess_sample_months"] == 6
    assert m["is_short_history_warning"] == 1
    assert m["is_geltner_applied"] == 0
    # un 指标应等于 orig 指标
    assert m["un_annualized_excess_return"] == pytest.approx(m["orig_annualized_excess_return"])
    assert m["un_information_ratio"] == pytest.approx(m["orig_information_ratio"])
    assert m["un_max_drawdown"] == pytest.approx(m["orig_max_drawdown"])
    assert m["un_annualized_return"] == pytest.approx(m["orig_annualized_return"])
    assert m["un_recovery_months"] == m["orig_recovery_months"]


@pytest.mark.unit
def test_compute_all_metrics_raw_return_extreme_loss_raises_not_complex():
    """单月收益率 <= -100% 时基金口径年化收益率必须报错，不能静默产出复数写库。

    (Python `(-x) ** frac` 返回 complex 而非抛异常，若无 comp<=0 守卫会静默污染 DB。)
    """
    returns = [-1.5, 0.01, 0.02, 0.01, 0.02, 0.01]
    rf_rates = [0.0435] * 6
    with pytest.raises(ValueError) as excinfo:
        compute_all_metrics(returns, rf_rates, "CrashFund")
    assert "CrashFund" in str(excinfo.value)
    assert "<= 0" in str(excinfo.value)


@pytest.mark.unit
def test_compute_all_metrics_excess_return_formula():
    """验证超额收益采用逐月扣减复利法（spec 4.1）。"""
    # 单月：r=0.01, RBA=0.0435 -> r_e = 0.01 - 0.0435/12 = 0.01 - 0.003625 = 0.006375
    # 年化(1月)：(1.006375)^12 - 1
    returns = [0.01]
    rf_rates = [0.0435]
    m = compute_all_metrics(returns, rf_rates, "TestFund")
    expected = (1.006375) ** 12 - 1
    assert m["orig_annualized_excess_return"] == pytest.approx(expected, rel=1e-6)


@pytest.mark.unit
def test_compute_all_metrics_win_rate_and_underperform():
    """验证胜率与最长连续跑输。"""
    # 4个月，全部跑赢RBA
    returns = [0.02, 0.03, 0.025, 0.015]
    rf_rates = [0.0435] * 4
    m = compute_all_metrics(returns, rf_rates, "TestFund")
    assert m["orig_excess_win_rate"] == pytest.approx(1.0)
    assert m["orig_max_underperform_months"] == 0
    # IR 可计算（4 个不同正值超额，std>0）
    assert m["orig_information_ratio"] is not None


@pytest.mark.unit
def test_ir_validation_anchor():
    """PDD 1.1 校验锚点：IR 分子*12 ≈ 年化超额收益（算术 vs 几何年化 <0.3pp）。"""
    returns = [0.003, 0.004, 0.005, 0.004, 0.003, 0.006] * 6  # 36 个月
    rf_rates = [0.0435] * 36
    m = compute_all_metrics(returns, rf_rates, "AnchorFund")
    excess = [r - 0.0435 / 12.0 for r in returns]
    mean_e = sum(excess) / len(excess)
    arithmetic_annualized_excess = mean_e * 12  # IR 分子*12
    assert abs(arithmetic_annualized_excess - m["orig_annualized_excess_return"]) < 0.003
    assert m["orig_information_ratio"] is not None


@pytest.mark.unit
def test_compute_all_metrics_rba_missing():
    """RBA 缺失月从超额序列剔除：excess_sample_months 减少；基金口径用全序列；
    年化超额用 n_excess；最长跑输在未压缩序列上 None 中断。"""
    returns = [0.005, 0.006, 0.004, 0.007, 0.005, 0.006]
    rf_rates = [0.0435, 0.0435, None, 0.0435, 0.0435, 0.0435]  # 第3月 RBA 缺失
    m = compute_all_metrics(returns, rf_rates, "RbaMissingFund")
    assert m["history_months"] == 6
    assert m["excess_sample_months"] == 5  # 剔除1月
    # 基金口径年化收益率用全6月
    comp_raw = 1.0
    for r in returns:
        comp_raw *= (1.0 + r)
    assert m["orig_annualized_return"] == pytest.approx(comp_raw ** (12.0 / 6) - 1)
    # 年化超额用压缩序列 + n_excess=5
    excess_comp = [r - 0.0435 / 12.0 for r, rf in zip(returns, rf_rates) if rf is not None]
    expected_excess_ann = 1.0
    for e in excess_comp:
        expected_excess_ann *= (1.0 + e)
    expected_excess_ann = expected_excess_ann ** (12.0 / 5) - 1
    assert m["orig_annualized_excess_return"] == pytest.approx(expected_excess_ann)
    # IR 可计算（5 个有效月，std>0）
    assert m["orig_information_ratio"] is not None
    # 最长跑输：第3月 RBA 缺失，未压缩序列在该位 None 中断（此处全跑赢，仅验证不报错且为0）
    assert m["orig_max_underperform_months"] == 0


@pytest.mark.unit
def test_compute_all_metrics_with_geltner():
    """60个月、phi 落在合理区间且 Q 显著的序列应真正触发 Geltner 去平滑。

    构造确定性 AR(1) 序列 r_t = c * r_{t-1} + eps_t，其中 eps 采用低自相关的
    确定性伪噪声（mod13 散列），使样本 phi 由 AR 系数主导而非被平滑的 eps 抬高。
    实测 phi≈0.43（落在防火墙3的 [0, 0.85] 区间）、Q≈11.6（> 3.841 临界值），
    故 should_apply_geltner 返回 True，去平滑路径被真正执行。
    """
    # AR(1): r_t = 0.75 * r_{t-1} + eps_t，eps 为确定性伪噪声（无随机性、可复现）
    returns = []
    r = 0.0
    for i in range(60):
        eps = 0.004 * ((i * 7) % 13) / 13.0 - 0.002
        r = 0.75 * r + eps
        returns.append(r)
    rf_rates = [0.0435] * 60
    m = compute_all_metrics(returns, rf_rates, "SmoothFund")
    assert m["history_months"] == 60
    assert m["is_short_history_warning"] == 0
    # Q 统计显著且 phi 在合理区间 -> 真正触发了去平滑
    assert m["is_q_significant"] == 1
    assert m["is_geltner_applied"] == 1
    # 去平滑放大真实波动：un 波动率必须严格大于 orig 波动率
    assert m["un_annualized_volatility"] > m["orig_annualized_volatility"]
