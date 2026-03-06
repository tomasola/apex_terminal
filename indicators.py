import pandas as pd
import numpy as np

def rma(series, period):
    alpha = 1 / period
    return series.ewm(alpha=alpha, adjust=False).mean()

def calculate_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calculate_sma(series, length):
    return series.rolling(window=length).mean()

def calculate_rsi(series, period):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0))
    loss = (-delta.where(delta < 0, 0))
    
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_rsi_divergence(df, len_fast=5, len_slow=14):
    """
    Replica: rsi(close, 5) - rsi(close, 14)
    """
    rsi_fast = calculate_rsi(df['close'], int(len_fast))
    rsi_slow = calculate_rsi(df['close'], int(len_slow))
    return rsi_fast - rsi_slow

def calculate_stochastic_supertrend(df, length_rsi=14, period_k=14, smooth_k=3, factor=10):
    """
    Replica EXACTA de Stochastic SuperTrend [BigBeluga] v6
    """
    # 1. Stoch RSI Calculation
    rsi = calculate_rsi(df['close'], int(length_rsi))
    stoch_min = rsi.rolling(window=int(period_k)).min()
    stoch_max = rsi.rolling(window=int(period_k)).max()
    stoch_range = stoch_max - stoch_min
    # Avoid division by zero
    stoch_raw = 100 * (rsi - stoch_min) / stoch_range.replace(0, 1)
    # k = ta.sma(stoch, smoothK)
    k = stoch_raw.rolling(window=int(smooth_k)).mean()
    
    # 2. Bands Calculation
    upper_band_raw = k + float(factor)
    lower_band_raw = k - float(factor)
    
    trend = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int) # 1: Down, -1: Up
    signals = pd.Series(index=df.index, dtype=str).fillna("")
    
    # Initialize values
    start_idx = k.first_valid_index()
    if start_idx is None:
        return k, k, k.fillna(0).astype(int), signals
        
    idx0 = df.index.get_loc(start_idx)
    
    # nz() initialization logic
    curr_upper = upper_band_raw.iloc[idx0]
    curr_lower = lower_band_raw.iloc[idx0]
    
    trend.iloc[idx0] = curr_upper
    direction.iloc[idx0] = 1 # Start Neutral/Down
    
    for i in range(idx0 + 1, len(df)):
        k_val = k.iloc[i]
        prev_k = k.iloc[i-1]
        
        # Pine: upperBand := upperBand < prevUpper or k[1] > prevUpper ? upperBand : prevUpper
        new_upper = upper_band_raw.iloc[i]
        curr_upper = new_upper if (new_upper < curr_upper or prev_k > curr_upper) else curr_upper
        
        # Pine: lowerBand := lowerBand > prevLower or k[1] < prevLower ? lowerBand : prevLower
        new_lower = lower_band_raw.iloc[i]
        curr_lower = new_lower if (new_lower > curr_lower or prev_k < curr_lower) else curr_lower
        
        # Pine: dir logic
        # if nz(trend[1]) == prevUpper: dir := k > upperBand ? -1 : 1 else dir := k < lowerBand ? 1 : -1
        prev_trend = trend.iloc[i-1]
        prev_dir = direction.iloc[i-1]
        
        if prev_dir == 1: # Was using Upper (Down Trend)
            curr_dir = -1 if k_val > curr_upper else 1
        else: # Was using Lower (Up Trend)
            curr_dir = 1 if k_val < curr_lower else -1
            
        direction.iloc[i] = curr_dir
        trend.iloc[i] = curr_lower if curr_dir == -1 else curr_upper
        
        # Signals based on dir change + 50 level filter
        # plotshape(dir != dir[1] and dir == -1 and k < 50) -> BUY
        if curr_dir == -1 and prev_dir == 1 and k_val < 50:
            signals.iloc[i] = "BUY"
        # plotshape(dir != dir[1] and dir == 1 and k > 50) -> SELL
        elif curr_dir == 1 and prev_dir == -1 and k_val > 50:
            signals.iloc[i] = "SELL"

    return k, trend, direction, signals

def calculate_macd(series, fast=12, slow=26, signal=9):
    exp1 = calculate_ema(series, fast)
    exp2 = calculate_ema(series, slow)
    macd = exp1 - exp2
    signal_line = calculate_ema(macd, signal)
    histogram = macd - signal_line
    return macd, signal_line, histogram

def calculate_adx(df, length=14):
    """Calcula el ADX (Average Directional Index)"""
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    
    atr = rma(tr, length)
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    plus_di = 100 * rma(pd.Series(plus_dm, index=df.index), length) / atr
    minus_di = 100 * rma(pd.Series(minus_dm, index=df.index), length) / atr
    
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = rma(dx, length)
    
    return adx

def calculate_bollinger_bands(series, period=20, std_dev=2):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper_band = sma + (std * std_dev)
    lower_band = sma - (std * std_dev)
    return upper_band, sma, lower_band
