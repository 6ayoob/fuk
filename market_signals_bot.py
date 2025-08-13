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
# الإعدادات والمتغيرات البيئية
# ===========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("يجب تعيين متغير البيئة TELEGRAM_TOKEN")

NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
if not NOWPAYMENTS_API_KEY:
    raise ValueError("يجب تعيين متغير البيئة NOWPAYMENTS_API_KEY")

NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
if not NOWPAYMENTS_IPN_SECRET:
    raise ValueError("يجب تعيين متغير البيئة NOWPAYMENTS_IPN_SECRET")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
WEBHOOK_ROUTE = "/market-signals-bot/telegram-webhook"
NOWPAYMENTS_ROUTE = "/market-signals-bot/nowpayments-webhook"
PORT = int(os.getenv("PORT", 5000))

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
    strategy = Column(String, nullable=False, default="strategy_one")
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    status = Column(String, default="active")  # active, expired
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
    status = Column(String, default="open")  # open, closed
    result = Column(String, nullable=True)  # win, loss, draw
    user = relationship("User")

Base.metadata.create_all(bind=engine)

# ===========================
# تطبيق Flask
# ===========================
app = Flask(__name__)

# ===========================
# دوال مساعدة
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
        Subscription.user_id == user_id,
        Subscription.status == "active",
        Subscription.start_date <= now,
        Subscription.end_date >= now
    ).all()

def get_active_subscription_by_strategy(session, user_id, strategy):
    now = datetime.utcnow()
    return session.query(Subscription).filter(
        Subscription.user_id == user_id,
        Subscription.strategy == strategy,
        Subscription.status == "active",
        Subscription.start_date <= now,
        Subscription.end_date >= now
    ).first()

def expire_subscriptions():
    session = SessionLocal()
    now = datetime.utcnow()
    expired = session.query(Subscription).filter(Subscription.status=="active", Subscription.end_date < now).all()
    for sub in expired:
        sub.status = "expired"
        session.add(sub)
    session.commit()
    session.close()

def create_nowpayments_invoice(telegram_id, amount_usd, pay_currency="usdt"):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {"x-api-key": NOWPAYMENTS_API_KEY,"Content-Type":"application/json"}
    data = {
        "price_amount": amount_usd,
        "price_currency": "usd",
        "pay_currency": pay_currency,
        "order_description": json.dumps({"telegram_id": str(telegram_id)}),
        "order_id": str(telegram_id),
        "ipn_callback_url": f"https://market-signals-bot.onrender.com{NOWPAYMENTS_ROUTE}"
    }
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 201:
        return resp.json().get("invoice_url")
    return None

def get_current_price(symbol):
    try:
        coin = symbol.split("-")[0].lower()
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        return data.get(coin, {}).get("usd", 0)
    except:
        return 0

# ===========================
# الاستراتيجية المحسنة
# ===========================
TARGETS = [1.04, 1.10]  # هدف 4٪ و10٪
STOP_LOSS = 0.95        # وقف خسارة 5%

def check_signal(symbol):
    """
    مثال بسيط: إشارة شراء إذا السعر الحالي أقل من المتوسط البسيط لـ 50 يوم
    """
    try:
        # بيانات وهمية باستخدام CoinGecko
        price = get_current_price(symbol)
        sma50 = price * 1.02  # افتراض: المتوسط 50 يوم أعلى 2٪ من السعر الحالي
        if price < sma50:
            return True
    except:
        return False
    return False

def update_recommendations_status():
    session = SessionLocal()
    open_trades = session.query(Trade).filter(Trade.status=="open").all()
    for trade in open_trades:
        price = get_current_price(trade.symbol)
        if price <= trade.open_price * STOP_LOSS:
            trade.status = "closed"
            trade.close_price = price
            trade.close_time = datetime.utcnow()
            trade.result = "loss"
            send_message(int(trade.user.telegram_id), f"⚠️ تم إغلاق صفقة {trade.symbol} بالخسارة عند {price}")
        elif price >= trade.open_price * TARGETS[0] and trade.result is None:
            trade.result = "partial_win"  # تحقق الهدف الأول
            send_message(int(trade.user.telegram_id), f"✅ وصل الهدف الأول 4٪ لصفقة {trade.symbol}")
        elif price >= trade.open_price * TARGETS[1]:
            trade.status = "closed"
            trade.close_price = price
            trade.close_time = datetime.utcnow()
            trade.result = "win"
            send_message(int(trade.user.telegram_id), f"🏆 تم إغلاق صفقة {trade.symbol} بالربح 10٪ عند {price}")
        session.add(trade)
    session.commit()
    session.close()

# ===========================
# Flask Webhook + NowPayments
# ===========================
@app.route(WEBHOOK_ROUTE, methods=["POST"])
def telegram_webhook():
    expire_subscriptions()
    update_recommendations_status()
    data = request.get_json()
    if not data or "message" not in data:
        return "ok"
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text","")
    from_user = msg.get("from",{})
    telegram_id = str(from_user.get("id"))
    session = SessionLocal()
    user = get_user(session, telegram_id, True, from_user)
    active_subs = get_active_subscriptions(session, user.id)

    if text == "/start":
        send_message(chat_id, f"مرحبًا {user.first_name or ''} 👋")
    elif text.startswith("/subscribe"):
        # إنشاء فاتورة
        amount = 50  # مبلغ افتراضي لكل الاشتراكات
        invoice_url = create_nowpayments_invoice(telegram_id, amount)
        if invoice_url:
            send_message(chat_id, f"ادفع للاشتراك: {invoice_url}")
        else:
            send_message(chat_id, "حدث خطأ في إنشاء الفاتورة")
    elif text == "/advice":
        messages=[]
        symbols = ["BTC-USDT","ETH-USDT","XRP-USDT"]
        for sym in symbols:
            if check_signal(sym):
                messages.append(f"📈 توصية شراء لـ {sym}")
        if messages:
            send_message(chat_id,"\n\n".join(messages))
        else:
            send_message(chat_id,"📊 لا توجد توصيات حالياً.")
    session.close()
    return "ok"

@app.route(NOWPAYMENTS_ROUTE, methods=["POST"])
def nowpayments_webhook():
    sig = request.headers.get("x-nowpayments-sig")
    if sig != NOWPAYMENTS_IPN_SECRET:
        return "Unauthorized",401
    data = request.get_json()
    if data.get("payment_status")=="finished":
        telegram_id = json.loads(data.get("order_description")).get("telegram_id")
        session = SessionLocal()
        user = get_user(session, telegram_id, False)
        if user:
            start = datetime.utcnow()
            end = start + timedelta(days=30)
            sub = Subscription(user_id=user.id,strategy="strategy_one",start_date=start,end_date=end,status="active",
                               payment_id=data.get("payment_id"),amount=data.get("pay_amount"),currency=data.get("pay_currency"))
            session.add(sub)
            session.commit()
            send_message(int(user.telegram_id),f"✅ تم تفعيل اشتراكك حتى {end.strftime('%Y-%m-%d')}")
        session.close()
    return "ok"

@app.route("/")
def home():
    return "بوت market-signals-bot يعمل بنظام Webhook و NowPayments IPN."

# ===========================
# جدولة المهام
# ===========================
scheduler = BackgroundScheduler()
scheduler.add_job(update_recommendations_status, "interval", minutes=5)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__=="__main__":
    print("تشغيل بوت market-signals-bot...")
    app.run(host="0.0.0.0", port=PORT)
