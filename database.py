from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ===========================
# إعداد قاعدة البيانات
# ===========================
DATABASE_URL = "sqlite:///./market_signals_bot.db"

Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

# ===========================
# جدول المستخدمين
# ===========================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    subscriptions = relationship("Subscription", back_populates="user")
    trades = relationship("Trade", back_populates="user")

# ===========================
# جدول الاشتراكات
# ===========================
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

# ===========================
# جدول الصفقات (Trades)
# ===========================
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

    user = relationship("User", back_populates="trades")

# إنشاء جميع الجداول
Base.metadata.create_all(bind=engine)
