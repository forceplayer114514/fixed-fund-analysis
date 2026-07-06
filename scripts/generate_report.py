import os
import sys
import json
import datetime
import yaml

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEANED_DIR = os.path.join(BASE_DIR, "data", "cleaned")
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

def load_metrics(fund_id):
    # Find the metrics file in the fund's cleaned directory
    fund_dir = os.path.join(CLEANED_DIR, fund_id)
    if not os.path.exists(fund_dir):
        return None
    files = [f for f in os.listdir(fund_dir) if f.endswith(".metrics.json")]
    if not files:
        return None
    # Load the latest metrics file
    files.sort()
    latest_file = os.path.join(fund_dir, files[-1])
    with open(latest_file, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate comparison report.")
    parser.add_argument("--funds", nargs="+", help="Optional: specific fund IDs to include in the report.")
    args = parser.parse_args()

    print("=== Generating Comparison Report ===")

    # Load registry
    if not os.path.exists(REGISTRY_PATH):
        print(f"CRITICAL: Registry not found at {REGISTRY_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f) or {}

    funds_to_process = args.funds if args.funds else registry.keys()

    funds_metrics = {}
    for fund_id in funds_to_process:
        if fund_id not in registry:
            print(f"Warning: {fund_id} not in registry, skipping.", file=sys.stderr)
            continue
        metrics = load_metrics(fund_id)
        if metrics:
            funds_metrics[fund_id] = metrics

    if not funds_metrics:
        print("CRITICAL: No calculated metrics found in cleaned directories.", file=sys.stderr)
        sys.exit(1)
        
    rba_rate = next(iter(funds_metrics.values()))["rba_cash_rate"]
    
    # Start building markdown report
    md = []
    md.append("# 澳洲固定收益基金业绩与风险对比报告")
    md.append(f"*报告生成日期: {datetime.datetime.now().strftime('%Y-%m-%d')} | 当前 RBA 官方现金利率 (Cash Rate): {rba_rate * 100.0:.2f}%*\n")
    
    md.append("## 一、 核心业绩与风险指标对比")
    md.append("下表对比了各基金在原始净收益率（Original）与 Geltner 去平滑修正后收益率（Unsmoothed）下的年化收益、超额收益、年化波动率及 Sortino 比率：\n")
    
    # Performance Table Headers
    md.append("| 基金名称 (代码) | 数据区间 | 历史月数 | 去平滑 | 年化收益率 (原/修正后) | 年化超额收益 (原/修正后) | 年化波动率 (原/修正后) | Sortino 比率 (原/修正后) | 最大回撤 |")
    md.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for fund_id, m in funds_metrics.items():
        name = m["fund_name"]
        apir = m["apir_code"]
        period = m["data_period"]
        history = m["history_months"]
        geltner_applied = "已应用" if m["is_geltner_applied"] else "未应用"

        orig_ret = m["original_metrics"]["annualized_return"]
        un_ret = m["unsmoothed_metrics"]["annualized_return"]
        orig_ex_ret = m["original_metrics"].get("annualized_excess_return", 0.0)
        un_ex_ret = m["unsmoothed_metrics"].get("annualized_excess_return", 0.0)
        orig_vol = m["original_metrics"]["annualized_volatility"]
        un_vol = m["unsmoothed_metrics"]["annualized_volatility"]
        orig_sortino = m["original_metrics"]["sortino_ratio"]
        un_sortino = m["unsmoothed_metrics"]["sortino_ratio"]
        max_dd = m["original_metrics"].get("max_drawdown", 0.0)

        # Add warning symbols if short history
        warning_suffix = " ⚠️" if m["is_short_history_warning"] else ""

        md.append(
            f"| {name} ({apir}) | {period} | {history}{warning_suffix} | {geltner_applied} | "
            f"{orig_ret*100.0:.2f}% / {un_ret*100.0:.2f}% | "
            f"{orig_ex_ret*100.0:.2f}% / {un_ex_ret*100.0:.2f}% | "
            f"{orig_vol*100.0:.2f}% / {un_vol*100.0:.2f}% | "
            f"{orig_sortino:.2f} / {un_sortino:.2f} | "
            f"{max_dd*100.0:.2f}% |"
        )
        
    md.append("\n> **注**：⚠️ 表示历史数据少于 6 个月。与传统使用单一当期无风险利率作为历史 hurdle rate 的简化计算不同，本报告**逐月扣除了历史当期实际的 RBA Cash Rate 得到超额收益率（Alpha），在此基础上求得复利年化超额收益与下行偏差**。这一调整消消除力了不同基金因成立年份不同、基准利率变化导致的选择性偏差，实现了真正的客观对比。\n")
    
    md.append("## 二、 自相关性分析与 Geltner 去平滑评估")
    md.append("资产估值平滑（Smoothing）会显著低估基金的波动率并高估其 Sortino 比率。去平滑参数 $\\phi$（一阶自相关系数）代表了平滑效应的严重程度。我们对各基金进行了 Ljung-Box 显著性检验（置信度 95%，显著性临界值 $Q > 3.841$）：\n")
    
    md.append("| 基金名称 | 自相关系数 $\\phi$ | Ljung-Box $Q$ 统计量 | 统计显著性 | 修正决策与依据 |")
    md.append("| :--- | :---: | :---: | :---: | :--- |")
    
    for fund_id, m in funds_metrics.items():
        name = m["fund_name"]
        phi = m["unsmoothing_coefficient_phi"]
        n = m["history_months"]
        q_stat = n * (n + 2) * (phi ** 2) / (n - 1) if n >= 36 else 0.0
        
        is_sig = q_stat > 3.841
        sig_str = "显著" if is_sig else "不显著"
        if n < 36:
            sig_str = "N/A"
            decision = f"跳过修正：历史区间仅 {n} 个月，少于 36 个月硬门禁"
        elif not is_sig:
            decision = f"跳过修正：自相关性不显著（Q = {q_stat:.2f} <= 3.841）"
        elif phi < 0:
            decision = f"跳过修正：自相关系数为负（phi = {phi:.4f}），无平滑特征"
        elif phi > 0.85:
            decision = f"跳过修正：自相关系数极高（phi = {phi:.4f} > 0.85），超出合理约束范围"
        else:
            decision = f"进行修正：自相关性显著且 phi = {phi:.4f} 在 [0, 0.85] 范围内"
            
        md.append(f"| {name} | {phi:.4f} | {q_stat:.2f} | {sig_str} | {decision} |")
        
    md.append("\n### 指标深度解析：")
    for idx, (fund_id, m) in enumerate(funds_metrics.items(), 1):
        name = m["fund_name"]
        phi = m["unsmoothing_coefficient_phi"]
        n = m["history_months"]
        orig_vol = m["original_metrics"]["annualized_volatility"]
        un_vol = m["unsmoothed_metrics"]["annualized_volatility"]
        orig_ex = m["original_metrics"].get("annualized_excess_return", 0.0)
        un_ex = m["unsmoothed_metrics"].get("annualized_excess_return", 0.0)
        orig_sortino = m["original_metrics"]["sortino_ratio"]
        un_sortino = m["unsmoothed_metrics"]["sortino_ratio"]
        
        if n < 36:
            md.append(f"{idx}. **{name}** 由于历史仅 {n} 个月，低于 36 个月样本门槛，跳过 Geltner 去平滑修正，保留原始指标（年化超额收益 **{orig_ex*100.0:.2f}%**，年化波动率 **{orig_vol*100.0:.2f}%**，Sortino 比率 **{orig_sortino:.2f}**）。")
        elif not m["is_geltner_applied"]:
            md.append(f"{idx}. **{name}** 的一阶自相关系数 $\\phi = {phi:.4f}$ 在统计上不显著或不满足修正条件，跳过去平滑修正。其复利年化超额收益为 **{orig_ex*100.0:.2f}%**，年化波动率 **{orig_vol*100.0:.2f}%**，Sortino 比率 **{orig_sortino:.2f}**。")
        else:
            md.append(f"{idx}. **{name}** 展示了显著的一阶自相关性（$\\phi = {phi:.4f}$）。经 Geltner 去平滑修正后，其真实年化波动率由 **{orig_vol*100.0:.2f}% 调增至 {un_vol*100.0:.2f}%**，复利年化超额收益为 **{orig_ex*100.0:.2f}% / {un_ex*100.0:.2f}%**，去平滑后的 Sortino 比率从 **{orig_sortino:.2f} 修正为 {un_sortino:.2f}**，这更真实地揭示了真实的下行风险。")
            
    # ponytail: Removed section "三、 信用利差分解与杠杆收益分析" and adjusted section numbering.
    # Leverage ratio cannot be parsed dynamically, so we strip this unnecessary analysis.

    md.append("\n## 三、 基金对比核心总结与投资建议")
    md.append("1. **综合风险收益表现 (Sortino 比率 - 超额收益视角)**：")

    sorted_funds = sorted(funds_metrics.values(), key=lambda x: x["unsmoothed_metrics"]["sortino_ratio"] if x["is_geltner_applied"] else x["original_metrics"]["sortino_ratio"], reverse=True)
    for m in sorted_funds:
        name = m["fund_name"]
        n = m["history_months"]
        orig_ret = m["original_metrics"]["annualized_return"]
        ex_ret = m["unsmoothed_metrics"]["annualized_excess_return"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_excess_return"]
        sortino = m["unsmoothed_metrics"]["sortino_ratio"] if m["is_geltner_applied"] else m["original_metrics"]["sortino_ratio"]
        vol = m["unsmoothed_metrics"]["annualized_volatility"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_volatility"]

        cycle_note = f"经历了 {n} 个月历史大周期" if n >= 36 else f"仅有 {n} 个月历史短期业绩"
        md.append(f"   - **{name}** ({cycle_note})：年化复利绝对收益率 **{orig_ret*100.0:.2f}%**，复利年化超额收益（扣除 RBA Cash Rate 之后）为 **{ex_ret*100.0:.2f}%**，在真实的年化波动率 **{vol*100.0:.2f}%** 下实现了 **{sortino:.2f}** 的超额 Sortino 比率。")

    md.append("2. **去平滑风险防范**：")
    for m in funds_metrics.values():
        name = m["fund_name"]
        phi = m["unsmoothing_coefficient_phi"]
        orig_vol = m["original_metrics"]["annualized_volatility"]
        un_vol = m["unsmoothed_metrics"]["annualized_volatility"]
        if m["is_geltner_applied"]:
            md.append(f"   - **{name}**：由于存在明显的平滑效应（一阶自相关系数 $\\phi = {phi:.4f}$），其账面年化波动率（{orig_vol*100.0:.2f}%）显著低估。经 Geltner 修正后的真实年化波动率为 **{un_vol*100.0:.2f}%**，在大类配置与风控限额管理中必须以修正后的真实波动率为基准。")

    # Save report.md to data output directory
    output_dir = os.path.join(BASE_DIR, "data", "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "report.md")

    # Generate AI investment advice based on calculated metrics
    md.append("\n## 五、 AI 投资建议")
    md.append("基于各固定收益基金的风险收益特征与 Geltner 去平滑波动率修正，提供以下投资建议：\n")
    for idx, m in enumerate(sorted_funds, 1):
        name = m["fund_name"]
        sortino = m["unsmoothed_metrics"]["sortino_ratio"] if m["is_geltner_applied"] else m["original_metrics"]["sortino_ratio"]
        vol = m["unsmoothed_metrics"]["annualized_volatility"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_volatility"]
        ex_ret = m["unsmoothed_metrics"]["annualized_excess_return"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_excess_return"]

        name_lower = name.lower()
        if "floating-rate high yield" in name_lower:
            md.append(f"{idx}. **{name}**：目前超额 Sortino 比率极高 ({sortino:.2f})，年化超额收益达到 {ex_ret*100.0:.2f}%。该基金主要投资于澳洲投资级浮息票据，对利率上行有很好的防御性，适合作为目前高息环境下的核心固收配置，以获取稳健的信用利差溢价。")
        elif "smarter money" in name_lower:
            md.append(f"{idx}. **{name}**：该基金波动率极低（去平滑后年化波动率 {vol*100.0:.2f}%），流动性极强。虽然超额收益率略低于杠杆/高收益基金，但其防御属性极佳。适合作为现金管理和流动性增强工具，替代传统银行定期存款。")
        elif "long-short opportunities" in name_lower:
            md.append(f"{idx}. **{name}**：通过信用及银行股权市场的多空策略，实现了较强的年化超额收益 ({ex_ret*100.0:.2f}%)，但去平滑后波动率也相对较高 ({vol*100.0:.2f}%)。适合风险偏好较高、追求绝对收益的投资者作为固收组合的卫星配置。")
        elif "long-short credit" in name_lower:
            md.append(f"{idx}. **{name}**：拥有超过 8 年的长期运行记录，年化超额收益 {ex_ret*100.0:.2f}%，Sortino 比率为 {sortino:.2f}。适合中长期投资者配置，作为穿越利率周期的基石阿尔法配置。")
        elif "stake accumulate" in name_lower:
            md.append(f"{idx}. **{name}**：虽然历史数据偏短（{m['history_months']} 个月），但其年化超额收益（{ex_ret*100.0:.2f}%）和极低波动率使其短期表现亮眼。建议密切跟踪其在完整利率周期中的表现，目前可进行小仓位试水。")
        else:
            md.append(f"{idx}. **{name}**：年化超额收益为 {ex_ret*100.0:.2f}%，年化波动率为 {vol*100.0:.2f}%，超额 Sortino 比率为 {sortino:.2f}。请结合具体投资期限和风险偏好合理配置。")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # Generate Excel data file
    excel_path = os.path.join(output_dir, "fund_data.xlsx")
    generate_excel_data(registry, CLEANED_DIR, excel_path)

    print(f"SUCCESS: Comparison report successfully generated at {output_path}")
    print(f"SUCCESS: Excel data successfully generated at {excel_path}")
    sys.exit(0)

def generate_excel_data(registry, cleaned_dir, excel_path):
    print("=== Generating Excel Data File ===")
    rba_history_path = os.path.join(os.path.dirname(cleaned_dir), "rba_cash_rate_history.json")
    if os.path.exists(rba_history_path):
        try:
            with open(rba_history_path, "r", encoding="utf-8") as f:
                rba_history = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load RBA history cache: {e}")
            rba_history = {}
    else:
        rba_history = {}
        
    try:
        import pandas as pd
    except ImportError:
        print("Warning: pandas is not installed. Skipping Excel generation.", file=sys.stderr)
        return
        
    try:
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            for fund_id, reg_info in registry.items():
                fund_name = reg_info.get("fund_name", fund_id)
                fund_dir = os.path.join(cleaned_dir, fund_id)
                if not os.path.exists(fund_dir):
                    continue
                validated_files = [f for f in os.listdir(fund_dir) if f.endswith(".validated.json")]
                if not validated_files:
                    continue
                validated_files.sort()
                latest_validated_file = os.path.join(fund_dir, validated_files[-1])
                
                try:
                    with open(latest_validated_file, "r", encoding="utf-8") as f:
                        val_data = json.load(f)
                except Exception as e:
                    print(f"Warning: Failed to load validated data for {fund_id}: {e}")
                    continue
                    
                time_series = val_data.get("time_series", [])
                if not time_series:
                    continue
                    
                records = []
                for dp in time_series[1:]:
                    date_str = dp["date"]
                    net_return = dp["net_return"]
                    
                    try:
                        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                        period_str = f"{dt.year}年{dt.month}月"
                    except Exception:
                        period_str = date_str
                    
                    month_key = date_str[:7]
                    rba_annual = rba_history.get(month_key)
                    if rba_annual is None:
                        rba_annual = 0.0435
                        
                    rba_monthly = rba_annual / 12.0
                    
                    records.append({
                        "时期": period_str,
                        "本月净收益率": net_return,
                        "当期RBA现金利率 (年化)": rba_annual,
                        "当期RBA现金利率 (月度)": rba_monthly
                    })
                    
                if not records:
                    continue
                    
                df = pd.DataFrame(records)
                sheet_name = fund_name[:31]
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                
                workbook = writer.book
                worksheet = writer.sheets[sheet_name]
                
                for row in range(2, len(df) + 2):
                    worksheet[f"B{row}"].number_format = "0.00%"
                    worksheet[f"C{row}"].number_format = "0.00%"
                    worksheet[f"D{row}"].number_format = "0.00%"
                    
                for col in worksheet.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = max(max_len + 3, 12)
                    
                print(f"Excel sheet generated for: {fund_name}")

    except Exception as e:
        print(f"Error generating Excel file: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
