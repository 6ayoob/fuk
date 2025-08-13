import os
import requests
import time
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from database import SessionLocal, User, Subscription, Trade
import strategy_one
import strategy_two

# ===========================
# إعداد التوكن و Telegram API
# ===========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("يجب تعيين متغير البيئة TELEGRAM_TOKEN")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ===========================
# وظائف مساعدة
# ===========================
def send_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"خطأ في إرسال رسالة: {e}")

def get_user(session, telegram_id, create_if_not_exist=True, user_info=None):
    user = session.query(User).filter_by(telegram_id=str(telegram_id)).first()
    if not user and create_if_not_exist:
        user = User(
            telegram_id=str(telegram_id),
            username=user_info.get("username") if user_info else None,
            first_name=user_info.get("first_name") if user_info else None,
            last_name=user_info.get("last_name") if user_info else None,
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

def update_recommendations_status():
    session = SessionLocal()
    try:
        open_trades = session.query(Trade).filter(Trade.status=="open").all()
        for trade in open_trades:
            current_price = get_current_price(trade.symbol)
            loss_threshold = trade.open_price * 0.9
            profit_threshold = trade.open_price * 1.1
            if current_price <= loss_threshold:
                trade.status = "closed"
                trade.close_time = datetime.utcnow()
                trade.close_price = current_price
                trade.result = "loss"
                session.add(trade)
                send_message(int(trade.user.telegram_id), f"⚠️ تم إغلاق صفقة {trade.symbol} بالخسارة عند {current_price}")
            elif current_price >= profit_threshold:
                trade.status = "closed"
                trade.close_time = datetime.utcnow()
                trade.close_price = current_price
                trade.result = "win"
                session.add(trade)
                send_message(int(trade.user.telegram_id), f"✅ تم إغلاق صفقة {trade.symbol} بالربح عند {current_price}")
        session.commit()
    except Exception as e:
        print(f"خطأ في تحديث التوصيات: {e}")
    finally:
        session.close()

def get_current_price(symbol):
    try:
        coin = symbol.split("-")[0].lower()
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        price = data.get(coin, {}).get("usd", 0)
        return price
    except Exception as e:
        print(f"خطأ في جلب السعر لـ {symbol}: {e}")
        return 0

# ===========================
# جدولة المهام الدورية
# ===========================
scheduler = BackgroundScheduler()
scheduler.add_job(update_recommendations_status, "interval", minutes=5)
scheduler.start()

# ===========================
# تشغيل البوت
# ===========================
def run_bot():
    print("تشغيل البوت...")
    # هنا نستخدم طريقة long polling بسيطة
    OFFSET = None
    while True:
        try:
            url = f"{TELEGRAM_API_URL}/getUpdates?timeout=20"
            if OFFSET:
                url += f"&offset={OFFSET}"
            resp = requests.get(url, timeout=30)
            data = resp.json()
            if not data.get("ok"):
                continue

            updates = data.get("result", [])
            for update in updates:
                OFFSET = update["update_id"] + 1
                if "message" in update:
                    message = update["message"]
                    chat_id = message["chat"]["id"]
                    text = message.get("text", "")
                    from_user = message.get("from", {})
                    telegram_id = str(from_user.get("id"))

                    session = SessionLocal()
                    user = get_user(session, telegram_id, True, from_user)
                    active_subs = get_active_subscriptions(session, user.id)
                    session.close()

                    # أوامر بسيطة
                    if text == "/start":
                        send_message(chat_id, f"مرحبًا {user.first_name or ''}! 👋\nالبوت يعمل بنجاح.\nاستخدم /help للمساعدة.")
                    elif text == "/help":
                        send_message(chat_id, "/subscribe 1 - استراتيجية 1 (40$)\n/subscribe 2 - استراتيجية 2 (70$)\n/status - حالة الاشتراكات\n/advice - تلقي التوصيات")
                    elif text == "/advice":
                        if not active_subs:
                            send_message(chat_id, "🚫 يرجى الاشتراك أولاً.")
                        else:
                            messages = []
                            symbols = ["BTC-USDT", "ETH-USDT", "XRP-USDT"]
                            for sub in active_subs:
                                if sub.strategy == "strategy_one":
                                    for sym in symbols:
                                        if strategy_one.check_signal(sym):
                                            messages.append(f"📈 توصية شراء لـ {sym} (استراتيجية 1)")
                                elif sub.strategy == "strategy_two":
                                    for sym in symbols:
                                        if strategy_two.check_signal(sym):
                                            messages.append(f"🚀 توصية شراء لـ {sym} (استراتيجية 2)")
                            send_message(chat_id, "\n\n".join(messages) if messages else "📊 لا توجد توصيات حالياً.")
        except Exception as e:
            print(f"خطأ في البوت: {e}")
        time.sleep(1)
