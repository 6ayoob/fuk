# market_signals_bot.py
import os
import json
from datetime import datetime, timedelta
from flask import Flask, request
import requests
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import pandas as pd

# ===========================
# إعداد المتغيرات البيئية
# ===========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
PORT = int(os.getenv("PORT", 5000))

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
WEBHOOK_ROUTE = "/market-signals-bot/telegram-webhook"
NOWPAYMENTS_ROUTE = "/market-signals-bot/nowpayments-webhook"

# ===========================
# قاعدة البيانات
# ===========================
DATABASE_URL = "sqlite:///./market_signals_bot.db"
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    subscriptions = relationship("Subscription", back_populates="user")

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    strategy = Column(String, nullable=False, default="strategy_advanced")
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    status = Column(String, default="active")
    payment_id = Column(String, nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    user = relationship("User", back_populates="subscriptions")

class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    strategy = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    open_time = Column(DateTime)
    close_time = Column(DateTime, nullable=True)
    open_price = Column(Float)
    close_price = Column(Float, nullable=True)
    status = Column(String, default="open")
    result = Column(String, nullable=True)
    user = relationship("User")

Base.metadata.create_all(bind=engine)

# ===========================
# Flask App
# ===========================
app = Flask(__name__)

# ===========================
# استراتيجة متقدمة
# ===========================
def fetch_ohlcv(symbol, limit=50):
    try:
        coin = symbol.split("-")[0].lower()
        url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days={limit}&interval=daily"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data['prices'], columns=['timestamp', 'close'])
        df['high'] = [x[1] for x in data['prices']]
        df['low'] = [x[1] for x in data['prices']]
        df['volume'] = [v[1] for v in data['total_volumes']]
        df['close'] = df['close']
        return df
    except Exception as e:
        print(f"خطأ في جلب OHLCV لـ {symbol}: {e}")
        return pd.DataFrame()

def moving_average(series, period=20):
    return series.rolling(period).mean()

def support_resistance(df):
    recent_high = df['high'][-50:].max()
    recent_low = df['low'][-50:].min()
    return recent_low, recent_high

def fibonacci_levels(df):
    high = df['high'][-50:].max()
    low = df['low'][-50:].min()
    return {
        "50%": high - 0.5*(high-low),
        "61.8%": high - 0.618*(high-low)
    }

def check_signal(symbol):
    df = fetch_ohlcv(symbol)
    if df.empty or len(df) < 20:
        return False
    close = df['close']
    ma20 = moving_average(close, 20).iloc[-1]
    ma50 = moving_average(close, 50).iloc[-1]
    current_price = close.iloc[-1]
    if ma20 < ma50:
        return False
    support, resistance = support_resistance(df)
    fib_levels = fibonacci_levels(df)
    entry_zone = min(support, fib_levels['50%'], fib_levels['61.8%'])
    return entry_zone < current_price < resistance

def trade_targets(entry_price):
    return {
        "take_profit_1": entry_price*1.04,
        "take_profit_2": entry_price*1.10,
        "stop_loss": entry_price*0.95
    }

# ===========================
# وظائف مساعدة
# ===========================
def send_message(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=5)
    except Exception as e:
        print(f"خطأ في إرسال رسالة: {e}")

def get_user(session, telegram_id, create_if_not_exist=True, user_info=None):
    user = session.query(User).filter_by(telegram_id=str(telegram_id)).first()
    if not user and create_if_not_exist:
        user = User(
            telegram_id=str(telegram_id),
            username=user_info.get("username") if user_info else None,
            first_name=user_info.get("first_name") if user_info else None,
            last_name=user_info.get("last_name") if user_info else None
        )
        session.add(user)
        session.commit()
    return user

def get_active_subscriptions(session, user_id):
    now = datetime.utcnow()
    return session.query(Subscription).filter(
        Subscription.user_id==user_id,
        Subscription.status=="active",
        Subscription.start_date<=now,
        Subscription.end_date>=now
    ).all()

def get_active_subscription_by_strategy(session, user_id):
    now = datetime.utcnow()
    return session.query(Subscription).filter(
        Subscription.user_id==user_id,
        Subscription.strategy=="strategy_advanced",
        Subscription.status=="active",
        Subscription.start_date<=now,
        Subscription.end_date>=now
    ).first()

def expire_subscriptions():
    session = SessionLocal()
    now = datetime.utcnow()
    expired = session.query(Subscription).filter(
        Subscription.status=="active",
        Subscription.end_date<now
    ).all()
    for sub in expired:
        sub.status = "expired"
        session.add(sub)
    session.commit()
    session.close()

def get_current_price(symbol):
    try:
        coin = symbol.split("-")[0].lower()
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.json().get(coin, {}).get("usd", 0)
    except Exception as e:
        print(f"خطأ في السعر لـ {symbol}: {e}")
        return 0

def update_recommendations_status():
    session = SessionLocal()
    try:
        open_trades = session.query(Trade).filter(Trade.status=="open").all()
        for trade in open_trades:
            current_price = get_current_price(trade.symbol)
            targets = trade_targets(trade.open_price)
            if current_price <= targets["stop_loss"]:
                trade.status="closed"
                trade.close_time=datetime.utcnow()
                trade.close_price=current_price
                trade.result="loss"
                session.add(trade)
                send_message(int(trade.user.telegram_id), f"⚠️ تم إغلاق صفقة {trade.symbol} بالخسارة عند {current_price}")
            elif current_price >= targets["take_profit_2"]:
                trade.status="closed"
                trade.close_time=datetime.utcnow()
                trade.close_price=current_price
                trade.result="win"
                session.add(trade)
                send_message(int(trade.user.telegram_id), f"✅ تم إغلاق صفقة {trade.symbol} بالربح عند {current_price}")
        session.commit()
    finally:
        session.close()

# ===========================
# Flask Webhook
# ===========================
@app.route(WEBHOOK_ROUTE, methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    if not update or "message" not in update:
        return "ok"
    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text","")
    from_user = message.get("from", {})
    telegram_id = str(from_user.get("id"))
    session = SessionLocal()
    try:
        user = get_user(session, telegram_id, True, from_user)
        active_sub = get_active_subscription_by_strategy(session, user.id)
        if text=="/start":
            send_message(chat_id,f"مرحبًا {user.first_name or ''} 👋\nالبوت يعمل بنجاح.\nاستخدم /help لمعرفة الأوامر.")
        elif text=="/help":
            send_message(chat_id,"/subscribe - الاشتراك\n/status - حالة الاشتراك\n/advice - التوصيات")
        elif text=="/subscribe":
            if active_sub:
                send_message(chat_id,"🚫 لديك اشتراك فعال بالفعل.")
            else:
                invoice_url = create_nowpayments_invoice(telegram_id, 50)
                if invoice_url:
                    send_message(chat_id,f"يرجى الدفع عبر الرابط:\n{invoice_url}")
                else:
                    send_message(chat_id,"خطأ في إنشاء رابط الدفع.")
        elif text=="/status":
            if not active_sub:
                send_message(chat_id,"🚫 لا يوجد اشتراك نشط.")
            else:
                send_message(chat_id,f"اشتراكك فعال حتى {active_sub.end_date.strftime('%Y-%m-%d')}")
        elif text=="/advice":
            if not active_sub:
                send_message(chat_id,"🚫 يرجى الاشتراك أولاً.")
            else:
                symbols = ["BTC-USDT","ETH-USDT","XRP-USDT"]
                messages=[]
                for sym in symbols:
                    if check_signal(sym):
                        messages.append(f"📈 توصية شراء لـ {sym}")
                send_message(chat_id,"\n".join(messages) if messages else "📊 لا توجد توصيات حالياً.")
        else:
            send_message(chat_id,"❓ أمر غير معروف، استخدم /help للمساعدة.")
    finally:
        session.close()
    return "ok"

# ===========================
# NowPayments IPN
# ===========================
def create_nowpayments_invoice(telegram_id, amount_usd):
    url="https://api.nowpayments.io/v1/invoice"
    headers={"x-api-key":NOWPAYMENTS_API_KEY,"Content-Type":"application/json"}
    data={
        "price_amount":amount_usd,
        "price_currency":"usd",
        "pay_currency":"usdt",
        "order_description": json.dumps({"telegram_id": str(telegram_id)}),
        "order_id": str(telegram_id),
        "ipn_callback_url": f"https://market-signals-bot.onrender.com{NOWPAYMENTS_ROUTE}"
    }
    resp=requests.post(url, headers=headers, json=data)
    if resp.status_code==201:
        return resp.json().get("invoice_url")
    return None

@app.route(NOWPAYMENTS_ROUTE, methods=["POST"])
def nowpayments_webhook():
    signature = request.headers.get("x-nowpayments-sig")
    if signature != NOWPAYMENTS_IPN_SECRET:
        return "Unauthorized",401
    data=request.get_json()
    payment_status = data.get("payment_status")
    payment_id = data.get("payment_id")
    amount = data.get("pay_amount")
    currency = data.get("pay_currency")
    custom_data = data.get("order_description")
    if payment_status=="finished":
        session=SessionLocal()
        try:
            telegram_id = str(json.loads(custom_data)["telegram_id"])
            user=get_user(session,telegram_id,False)
            if user:
                start_date=datetime.utcnow()
                end_date=start_date+timedelta(days=30)
                sub=Subscription(user_id=user.id,strategy="strategy_advanced",
                                 start_date=start_date,end_date=end_date,
                                 status="active",payment_id=payment_id,
                                 amount=amount,currency=currency)
                session.add(sub)
                session.commit()
                send_message(int(user.telegram_id),f"✅ تم تفعيل اشتراكك حتى {end_date.strftime('%Y-%m-%d')}")
        finally:
            session.close()
    return "ok"

# ===========================
# جدولة المهام
# ===========================
scheduler = BackgroundScheduler()
scheduler.add_job(update_recommendations_status,"interval",minutes=5)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ===========================
# تشغيل Flask
# ===========================
@app.route("/")
def home():
    return "بوت market-signals-bot يعمل."

if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT)
