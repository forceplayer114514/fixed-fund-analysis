import statistics

def detect_anomalies(time_series, threshold_sigma=3.0):
    """
    Detect anomalies in a monthly return time series using robust statistics (Median and MAD).
    Returns a list of dicts describing the anomalies found.
    """
    returns = [dp["net_return"] for dp in time_series if "net_return" in dp and dp["net_return"] != 0.0]
    if len(returns) < 12:
        return [] # Need at least a year of data for meaningful stats
        
    median = statistics.median(returns)
    # Calculate MAD (Median Absolute Deviation)
    abs_deviations = [abs(x - median) for x in returns]
    mad = statistics.median(abs_deviations)
    
    # Standard deviation approximation from MAD: std = MAD * 1.4826
    # If MAD is 0 (all returns are identical), fallback to a small number to avoid division by zero
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
                "mad_score": mad_score,
                "threshold_sigma": threshold_sigma,
                "median": median,
                "mad": mad,
                "commentary_truth": dp.get("commentary_truth", None) # Will be populated by parse_factsheet
            })
            
    return anomalies
