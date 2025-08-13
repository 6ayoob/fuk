# strategy_advanced.py
import pandas as pd
import requests

# ===========================
# دالة لجلب بيانات OHLCV من CoinGecko
# ===========================
def fetch_ohlcv(symbol, limit=50):
    try:
        coin = symbol.split("-")[0].lower()
        url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days={limit}&interval=daily"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        # إرجاع DataFrame يحتوي على [timestamp, open, high, low, close, volume]
        df = pd.DataFrame(data['prices'], columns=['timestamp', 'close'])
        df['high'] = [x[1] for x in data['prices']]
        df['low'] = [x[1] for x in data['prices']]
        df['volume'] = [v[1] for v in data['total_volumes']]
        df['close'] = df['close']
        return df
    except Exception as e:
        print(f"خطأ في جلب OHLCV لـ {symbol}: {e}")
        return pd.DataFrame()

# ===========================
# دالة لحساب MA
# ===========================
def moving_average(series, period=20):
    return series.rolling(period).mean()

# ===========================
# دالة لحساب الدعم والمقاومة
# ===========================
def support_resistance(df):
    recent_high = df['high'][-50:].max()
    recent_low = df['low'][-50:].min()
    return recent_low, recent_high

# ===========================
# دالة لحساب مستويات فيبوناتشي
# ===========================
def fibonacci_levels(df):
    high = df['high'][-50:].max()
    low = df['low'][-50:].min()
    levels = {
        "23.6%": high - 0.236*(high - low),
        "38.2%": high - 0.382*(high - low),
        "50%": high - 0.5*(high - low),
        "61.8%": high - 0.618*(high - low),
        "78.6%": high - 0.786*(high - low),
    }
    return levels

# ===========================
# الدالة الرئيسية للتحقق من التوصية
# ===========================
def check_signal(symbol):
    df = fetch_ohlcv(symbol)
    if df.empty or len(df) < 20:
        return False

    close = df['close']
    ma20 = moving_average(close, 20).iloc[-1]
    ma50 = moving_average(close, 50).iloc[-1]
    current_price = close.iloc[-1]

    # اتجاه السوق صاعد
    if ma20 < ma50:
        return False

    # الدعم والمقاومة
    support, resistance = support_resistance(df)

    # مستويات فيبوناتشي
    fib_levels = fibonacci_levels(df)

    # شروط الدخول: السعر قريب من الدعم أو فيبوناتشي 50-61.8%
    entry_zone = min(support, fib_levels['50%'], fib_levels['61.8%'])
    if current_price > entry_zone and current_price < resistance:
        return True
    return False

# ===========================
# دالة لحساب أهداف الربح ووقف الخسارة
# ===========================
def trade_targets(entry_price):
    targets = {
        "take_profit_1": entry_price * 1.04,  # 4% ربح
        "take_profit_2": entry_price * 1.10,  # 10% ربح
        "stop_loss": entry_price * 0.95       # 5% خسارة
    }
    return targets
