import math
import statistics

def detect_anomalies(time_series, threshold_sigma=3.0):
    """
    Detect anomalies in a monthly return time series using standard deviation threshold.
    Returns a list of dicts describing the anomalies found.
    """
    returns = [dp["net_return"] for dp in time_series if "net_return" in dp and dp["net_return"] != 0.0]
    if len(returns) < 12:
        return [] # Need at least a year of data for meaningful stats
        
    mean = statistics.mean(returns)
    stdev = statistics.stdev(returns)
    
    anomalies = []
    
    for dp in time_series:
        ret = dp.get("net_return")
        if ret is None or ret == 0.0:
            continue
            
        z_score = (ret - mean) / stdev
        if abs(z_score) >= threshold_sigma:
            anomalies.append({
                "date": dp["date"],
                "value": ret,
                "z_score": z_score,
                "threshold_sigma": threshold_sigma,
                "mean": mean,
                "stdev": stdev
            })
            
    return anomalies
