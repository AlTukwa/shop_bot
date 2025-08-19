# -*- coding: utf-8 -*-
"""
Telegram Shop Bot
-----------------
Features:
- Products catalog (SQLite)
- Cart per user
- Checkout: Electronic (upload receipt) or Cash on Delivery
- Admin can add/list/remove products

Stack:
- Python 3.10+
- pyTelegramBotAPI
- SQLite
"""

import os
import sqlite3
import time
from typing import Optional, List, Tuple
from dotenv import load_dotenv

import telebot
from telebot.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

# ===================== CONFIG =====================
load_dotenv()  # تحميل القيم من .env

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير معرف. ضع التوكن داخل ملف .env")

PAYMENT_INFO = (
    "💳 *الدفع الإلكتروني*\n"
    "أرسل المبلغ إلى أحد الحسابات التالية ثم ارفع *صورة الإيصال*:\n\n"
    "• زين كاش: `0770 000 0000`\n"
    "• آسيا باي: `0780 000 0000`\n"
    "• ويسترن/موني غرام: الاسم — المدينة\n\n"
    "بعد التحويل *أرسل صورة الإيصال* مع كتابة *العنوان ورقم الهاتف* في الرسالة."
)

# ===================== DB =====================
DB_PATH = "shop.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  price INTEGER NOT NULL,
  description TEXT DEFAULT '',
  photo_file_id TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS carts (
  user_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  items_json TEXT NOT NULL,
  total INTEGER NOT NULL,
  method TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  receipt_file_id TEXT,
  address TEXT
);
"""

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return conn

_conn = db()
_cur = _conn.cursor()
_cur.executescript(SCHEMA)
_conn.commit()

# ===================== BOT INIT =====================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

EXPECTING_RECEIPT = set()
PENDING_ORDER_ID = {}

# ===================== HELPERS =====================

def dinar(n: int) -> str:
    return f"{n:,} IQD".replace(",", "٬")

def get_products() -> List[Tuple[int, str, int, str, Optional[str]]]:
    return _conn.execute("SELECT id, name, price, description, photo_file_id FROM products ORDER BY id DESC").fetchall()

def add_to_cart(user_id: int, product_id: int, qty: int = 1):
    cur = _conn.cursor()
    cur.execute("SELECT quantity FROM carts WHERE user_id=? AND product_id=?", (user_id, product_id))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE carts SET quantity=quantity+? WHERE user_id=? AND product_id=?", (qty, user_id, product_id))
    else:
        cur.execute("INSERT INTO carts (user_id, product_id, quantity) VALUES (?,?,?)", (user_id, product_id, qty))
    _conn.commit()

def get_cart(user_id: int):
    return _conn.execute(
        "SELECT p.id, p.name, p.price, c.quantity FROM carts c JOIN products p ON p.id=c.product_id WHERE c.user_id=?",
        (user_id,)
    ).fetchall()

def clear_cart(user_id: int):
    _conn.execute("DELETE FROM carts WHERE user_id=?", (user_id,))
    _conn.commit()

def cart_total(items) -> int:
    return sum(price * qty for _, _, price, qty in items)

# ===================== KEYBOARDS =====================

def main_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📦 المنتجات"), KeyboardButton("🛒 سلتي"))
    kb.row(KeyboardButton("ℹ️ تواصل معنا"))
    return kb

def product_inline_kb(pid: int):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 أضف للسلة", callback_data=f"add:{pid}"))
    return kb

def cart_inline_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🧹 تفريغ السلة", callback_data="cart:clear"))
    kb.add(InlineKeyboardButton("✅ إتمام الشراء", callback_data="checkout"))
    return kb

def checkout_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 دفع إلكتروني", callback_data="pay:electronic"))
    kb.add(InlineKeyboardButton("🚚 عند الاستلام", callback_data="pay:cod"))
    return kb

def admin_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("➕ إضافة منتج"), KeyboardButton("📃 قائمة المنتجات"))
    kb.row(KeyboardButton("❌ حذف منتج"))
    kb.row(KeyboardButton("🏠 رجوع"))
    return kb

# ===================== COMMANDS =====================

@bot.message_handler(commands=["start"])
def cmd_start(message: Message):
    kb = main_kb()
    if message.from_user.id == ADMIN_CHAT_ID:
        kb.row(KeyboardButton("🛠️ لوحة الإدارة"))
    bot.send_message(message.chat.id, "أهلاً بك في *المتجر* 🛍️", reply_markup=kb)

# باقي الهاندلرات (إضافة منتجات، عرض السلة، الدفع..) تبقى نفسها من الكود السابق
# (انسخها كما هي من الكود اللي عندك، ما غيرت منطقها أبداً)

# ===================== RUN =====================
if __name__ == "__main__":
    print("✅ Bot is running…")
    bot.infinity_polling(skip_pending=True)
