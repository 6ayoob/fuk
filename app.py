import threading
from flask import Flask
from bot import run_bot  # نستورد وظيفة تشغيل البوت من bot.py

# إنشاء تطبيق Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Bot is running on Render free service!"

def start_flask():
    # تشغيل Flask على المنفذ اللي يحدده Render
    app.run(host="0.0.0.0", port=10000)

if __name__ == '__main__':
    # تشغيل البوت في Thread منفصل
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()

    # تشغيل Flask في Thread الرئيسي
    start_flask()
