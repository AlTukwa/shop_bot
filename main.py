# -*- coding: utf-8 -*-
"""
Telegram Shop Bot (Sellable Template)
-------------------------------------
Features:
- Products catalog (SQLite)
- Cart per user (SQLite)
- Checkout with two methods: Electronic payment (upload receipt) or Cash on Delivery
- Notifies admin for every order; forwards receipt photo
- Simple admin commands to add/list/remove products

Stack:
- Python 3.10+
- pyTelegramBotAPI (telebot)
- SQLite (built-in)

Setup:
1) pip install pyTelegramBotAPI==4.20.0
2) Fill BOT_TOKEN and ADMIN_CHAT_ID below
3) Run: python bot.py
"""

import os
import sqlite3
import time
from typing import Optional, List, Tuple

import telebot
from telebot.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InputMediaPhoto,
)

# ===================== CONFIG =====================
# ضع توكن البوت الصحيح هنا أو كمتغير بيئة
BOT_TOKEN = os.getenv("BOT_TOKEN", "8443593567:AAE0-CZK0nje8auJTJmYauyQLl6IG2boqoM")
# ضع آي دي حسابك (الأدمن)
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "774345717"))

# Payment info to show users (adjust as needed)
PAYMENT_INFO = (
    "💳 *الدفع الإلكتروني*\n"
    "أرسل المبلغ إلى أحد الحسابات التالية ثم ارفع *صورة الإيصال* هنا:\n\n"
    "• زين كاش: `0770 000 0000`\n"
    "• آسيا باي: `0780 000 0000`\n"
    "• ويسترن/موني غرام: الاسم — المدينة\n\n"
    "بعد التحويل *أرسل صورة الإيصال* مع كتابة *العنوان ورقم الهاتف* في الرسالة."
)

# ===================== DB =====================
DB_PATH = "shop.db"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  price INTEGER NOT NULL,          -- store smallest unit (e.g., IQD)
  description TEXT DEFAULT '',
  photo_file_id TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS carts (
  user_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, product_id),
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  items_json TEXT NOT NULL,        -- serialized items snapshot
  total INTEGER NOT NULL,
  method TEXT NOT NULL,            -- 'electronic' | 'cod'
  status TEXT NOT NULL,            -- 'pending_receipt' | 'placed' | 'paid' | 'cancelled'
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

# A simple per-user state flag for expecting receipt after electronic checkout
EXPECTING_RECEIPT = set()  # user_id set
PENDING_ORDER_ID = {}      # user_id -> order_id awaiting receipt

# ===================== HELPERS =====================

def dinar(n: int) -> str:
    # format integer as IQD with separators
    return f"{n:,} IQD".replace(",", "٬")

def md_escape(text: str) -> str:
    """Escape Markdown V2 special characters to avoid breaking captions/keyboards."""
    if text is None:
        return ""
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, f"\\{ch}")
    return text

def get_products() -> List[Tuple[int, str, int, str, Optional[str]]]:
    rows = _conn.execute("SELECT id, name, price, description, photo_file_id FROM products ORDER BY id DESC").fetchall()
    return rows

def get_product(pid: int):
    return _conn.execute("SELECT id, name, price, description, photo_file_id FROM products WHERE id=?", (pid,)).fetchone()

def add_to_cart(user_id: int, product_id: int, qty: int = 1):
    cur = _conn.cursor()
    cur.execute("SELECT quantity FROM carts WHERE user_id=? AND product_id=?", (user_id, product_id))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE carts SET quantity = quantity + ? WHERE user_id=? AND product_id=?", (qty, user_id, product_id))
    else:
        cur.execute("INSERT INTO carts (user_id, product_id, quantity) VALUES (?, ?, ?)", (user_id, product_id, qty))
    _conn.commit()

def get_cart(user_id: int):
    rows = _conn.execute(
        """
        SELECT p.id, p.name, p.price, c.quantity
        FROM carts c JOIN products p ON p.id = c.product_id
        WHERE c.user_id=?
        ORDER BY p.id DESC
        """,
        (user_id,),
    ).fetchall()
    return rows  # list of tuples (pid, name, price, qty)

def clear_cart(user_id: int):
    _conn.execute("DELETE FROM carts WHERE user_id=?", (user_id,))
    _conn.commit()

def cart_total(items) -> int:
    return sum((price * qty) for _, _, price, qty in items)

# ===================== KEYBOARDS =====================

def main_kb() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📦 المنتجات"), KeyboardButton("🛒 سلتي"))
    kb.row(KeyboardButton("ℹ️ تواصل معنا"))
    return kb

def product_inline_kb(pid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 أضف للسلة", callback_data=f"add:{pid}"))
    kb.add(InlineKeyboardButton("⬅️ رجوع للقائمة", callback_data="back:list"))
    return kb

def cart_inline_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🧹 تفريغ السلة", callback_data="cart:clear"))
    kb.add(InlineKeyboardButton("✅ إتمام الشراء", callback_data="checkout"))
    return kb

def checkout_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 دفع إلكتروني", callback_data="pay:electronic"))
    kb.add(InlineKeyboardButton("🚚 عند الاستلام", callback_data="pay:cod"))
    return kb

def admin_kb() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("➕ إضافة منتج"), KeyboardButton("📃 قائمة المنتجات"))
    kb.row(KeyboardButton("❌ حذف منتج"))
    kb.row(KeyboardButton("🏠 رجوع"))
    return kb

# ===================== COMMANDS =====================

@bot.message_handler(commands=["start"])
def cmd_start(message: Message):
    uid = message.from_user.id
    is_admin = uid == ADMIN_CHAT_ID
    text = (
        "أهلاً بك في *المتجر* 🛍️\n"
        "تصفح المنتجات وأضف للسلة ثم اختر طريقة الدفع المناسبة لك.\n\n"
        "إذا عندك أي سؤال اضغط *تواصل معنا*."
    )
    kb = main_kb()
    if is_admin:
        kb.row(KeyboardButton("🛠️ لوحة الإدارة"))
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "🏠 رجوع")
def back_home(message: Message):
    cmd_start(message)

@bot.message_handler(func=lambda m: m.text == "ℹ️ تواصل معنا")
def contact_info(message: Message):
    bot.send_message(message.chat.id, "للتواصل: @your_username أو واتساب 07xx xxx xxxx")

@bot.message_handler(func=lambda m: m.text == "📦 المنتجات")
def list_products(message: Message):
    products = get_products()
    if not products:
        bot.send_message(message.chat.id, "لا توجد منتجات حالياً.")
        return
    for pid, name, price, desc, photo_id in products:
        # نهرب النص لتفادي كسر الماركداون (قد يؤدي لاختفاء الأزرار)
        name_s = md_escape(str(name or ""))
        desc_s = md_escape(str(desc or ""))
        caption = f"*{name_s}*\nالسعر: {dinar(price)}\n\n{desc_s}".strip()
        if photo_id:
            bot.send_photo(
                message.chat.id,
                photo_id,
                caption=caption,
                reply_markup=product_inline_kb(pid),
                parse_mode="Markdown",
            )
        else:
            bot.send_message(
                message.chat.id,
                caption,
                reply_markup=product_inline_kb(pid),
                parse_mode="Markdown",
            )

@bot.callback_query_handler(func=lambda c: bool(c.data) and c.data.startswith("add:"))
def cb_add_to_cart(call):
    try:
        pid_str = call.data.split(":", 1)[1].strip()
        pid = int(pid_str)
        row = get_product(pid)
        if not row:
            bot.answer_callback_query(call.id, "⚠️ المنتج غير موجود.")
            return

        # للتشخيص: تأكد أن PID يتغير عند الضغط على منتجات مختلفة
        print(f"[DEBUG] add_to_cart: user={call.from_user.id} pid={pid}")

        add_to_cart(call.from_user.id, pid, 1)
        bot.answer_callback_query(call.id, "تمت الإضافة للسلة 🧺")
    except Exception as e:
        print(f"[ERROR] add_to_cart: {e}")
        bot.answer_callback_query(call.id, "حدث خطأ غير متوقع.")

@bot.message_handler(func=lambda m: m.text == "🛒 سلتي")
def view_cart(message: Message):
    items = get_cart(message.from_user.id)
    if not items:
        bot.send_message(message.chat.id, "سلتك فارغة.")
        return
    lines = ["🛒 *سلتك*: \n"]
    for pid, name, price, qty in items:
        lines.append(f"• {name} × {qty} — {dinar(price*qty)}")
    lines.append(f"\nالمجموع: *{dinar(cart_total(items))}*")
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=cart_inline_kb())

@bot.callback_query_handler(func=lambda c: c.data == "cart:clear")
def cb_cart_clear(call):
    clear_cart(call.from_user.id)
    bot.answer_callback_query(call.id, "تم تفريغ السلة")
    bot.edit_message_text("سلتك الآن فارغة.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == "checkout")
def cb_checkout(call):
    items = get_cart(call.from_user.id)
    if not items:
        bot.answer_callback_query(call.id, "السلة فارغة.")
        return
    total = cart_total(items)
    text = (
        "✅ *إتمام الشراء*\n"
        f"الإجمالي: *{dinar(total)}*\n\n"
        "اختر طريقة الدفع:"
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=checkout_kb(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("pay:"))
def cb_pay_method(call):
    method = call.data.split(":")[1]  # 'electronic' or 'cod'
    uid = call.from_user.id
    items = get_cart(uid)
    if not items:
        bot.answer_callback_query(call.id, "السلة فارغة.")
        return
    total = cart_total(items)

    # snapshot items as text (simple JSON-like)
    snapshot_lines = []
    for pid, name, price, qty in items:
        snapshot_lines.append(f"{name}×{qty}@{price}")
    items_snapshot = ";".join(snapshot_lines)

    created_at = int(time.time())

    if method == "electronic":
        _conn.execute(
            "INSERT INTO orders (user_id, items_json, total, method, status, created_at) VALUES (?,?,?,?,?,?)",
            (uid, items_snapshot, total, method, 'pending_receipt', created_at),
        )
        _conn.commit()
        order_id = _conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        PENDING_ORDER_ID[uid] = order_id
        EXPECTING_RECEIPT.add(uid)

        bot.edit_message_text(
            f"رقم الطلب: *#{order_id}*\n\n{PAYMENT_INFO}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
        )
        bot.answer_callback_query(call.id)
        # notify admin
        bot.send_message(
            ADMIN_CHAT_ID,
            f"📦 طلب جديد بانتظار الإيصال\nرقم الطلب: #{order_id}\nالمجموع: {dinar(total)}\nالطريقة: دفع إلكتروني\nالمستخدم: @{call.from_user.username or call.from_user.id}",
        )

    elif method == "cod":
        _conn.execute(
            "INSERT INTO orders (user_id, items_json, total, method, status, created_at) VALUES (?,?,?,?,?,?)",
            (uid, items_snapshot, total, method, 'placed', created_at),
        )
        _conn.commit()
        order_id = _conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        clear_cart(uid)

        bot.edit_message_text(
            (
                f"تم إنشاء طلبك *#{order_id}* بنجاح.\n"
                f"الإجمالي: *{dinar(total)}*\n\n"
                "🚚 طريقة الدفع: عند الاستلام.\n"
                "أرسل *العنوان ورقم الهاتف* هنا ليتم التواصل والتوصيل."
            ),
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
        )
        bot.answer_callback_query(call.id)
        # notify admin
        bot.send_message(
            ADMIN_CHAT_ID,
            f"📦 طلب جديد\nرقم الطلب: #{order_id}\nالمجموع: {dinar(total)}\nالطريقة: دفع عند الاستلام\nالمستخدم: @{call.from_user.username or call.from_user.id}",
        )

# ===================== PHOTO HANDLER (merged) =====================

# تحويل الأرقام العربية ↦ إنجليزية
AR_TO_EN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def normalize_digits(s: str) -> str:
    return (s or "").translate(AR_TO_EN)

@bot.message_handler(content_types=["photo"])
def on_photo(message: Message):
    uid = message.from_user.id
    caption = message.caption or ""

    # 1) إضافة منتج من الأدمن: "الاسم | السعر | الوصف"
    if uid == ADMIN_CHAT_ID and "|" in caption:
        try:
            parts = [p.strip() for p in caption.split("|")]
            name = parts[0]
            # دعم الأرقام العربية والفواصل والمسافات
            price_str = normalize_digits(parts[1]).replace(" ", "").replace(",", "").replace("٬", "")
            price = int(price_str)
            desc = parts[2] if len(parts) > 2 else ""
            photo_id = message.photo[-1].file_id

            _conn.execute(
                "INSERT INTO products (name, price, description, photo_file_id) VALUES (?,?,?,?)",
                (name, price, desc, photo_id),
            )
            _conn.commit()
            bot.reply_to(message, f"تمت إضافة المنتج: *{md_escape(name)}* بسعر {dinar(price)}", parse_mode="Markdown")
            return
        except Exception as e:
            bot.reply_to(message, f"تعذّر إضافة المنتج. تأكد من الصيغة.\nخطأ: {e}")
            return

    # 2) إيصال دفع إلكتروني متوقَّع
    if uid in EXPECTING_RECEIPT:
        order_id = PENDING_ORDER_ID.get(uid)
        if not order_id:
            bot.reply_to(message, "لا يوجد طلب بانتظار إيصال.")
            return

        file_id = message.photo[-1].file_id
        address = caption  # لو أرسل العنوان في الكابشن

        _conn.execute(
            "UPDATE orders SET status='paid', receipt_file_id=?, address=? WHERE id=?",
            (file_id, address, order_id),
        )
        _conn.commit()

        # بعد استلام الإيصال نُفرّغ السلة وننهي الحالة
        clear_cart(uid)
        EXPECTING_RECEIPT.discard(uid)
        PENDING_ORDER_ID.pop(uid, None)

        bot.reply_to(message, f"تم استلام إيصال الدفع لطلبك *#{order_id}*. سيتم التواصل قريبًا، شكرًا لك.", parse_mode="Markdown")

        # إخطار الأدمن مع الصورة
        caption_admin = f"✅ تم استلام إيصال الدفع\nرقم الطلب: #{order_id}\nالمستخدم: @{message.from_user.username or message.from_user.id}"
        try:
            bot.send_photo(ADMIN_CHAT_ID, file_id, caption=caption_admin)
        except Exception:
            bot.send_message(ADMIN_CHAT_ID, caption_admin + "\n(تعذّر إرسال الصورة)")
        return

    # 3) صور عادية
    bot.reply_to(message, "تم استلام الصورة.")

# ===================== ADMIN TEXT ACTIONS =====================

@bot.message_handler(func=lambda m: m.text == "🛠️ لوحة الإدارة")
def admin_panel(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    bot.send_message(message.chat.id, "لوحة الإدارة:", reply_markup=admin_kb())

@bot.message_handler(func=lambda m: m.text == "➕ إضافة منتج")
def admin_add_product_hint(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    bot.send_message(
        message.chat.id,
        (
            "أرسل *صورة المنتج* مع *الكابشن* بالصيغة:\n"
            "الاسم | السعر | الوصف\n\n"
            "مثال:\n"
            "تيشيرت قطن أبيض | 15000 | قماش عالي الجودة مقاسات متوفرة"
        ),
        parse_mode="Markdown",
    )

@bot.message_handler(func=lambda m: m.text == "📃 قائمة المنتجات")
def admin_list_products(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    products = get_products()
    if not products:
        bot.send_message(message.chat.id, "لا توجد منتجات.")
        return
    lines = ["*المنتجات:*\n"]
    for pid, name, price, desc, _ in products:
        lines.append(f"#{pid} — {name} — {dinar(price)}")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "❌ حذف منتج")
def admin_delete_hint(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    bot.send_message(message.chat.id, "أرسل رقم المنتج للحذف: مثال: 12")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_CHAT_ID and m.text.isdigit())
def admin_delete_product(message: Message):
    pid = int(message.text)
    row = get_product(pid)
    if not row:
        bot.reply_to(message, "لم يتم العثور على المنتج")
        return
    _conn.execute("DELETE FROM products WHERE id=?", (pid,))
    _conn.commit()
    bot.reply_to(message, f"تم حذف المنتج #{pid}")

# ===================== TEXT FALLBACK =====================

@bot.message_handler(content_types=["text"])
def text_fallback(message: Message):
    uid = message.from_user.id
    if uid in EXPECTING_RECEIPT and PENDING_ORDER_ID.get(uid):
        # treat text as address details for the pending order
        order_id = PENDING_ORDER_ID.get(uid)
        _conn.execute("UPDATE orders SET address=? WHERE id=?", (message.text, order_id))
        _conn.commit()
        bot.reply_to(message, "تم حفظ العنوان/البيانات. الآن أرسل صورة الإيصال لإكمال الدفع.")
        return

    # default help
    bot.send_message(
        message.chat.id,
        "اختر من القائمة بالأسفل أو اكتب /start",
        reply_markup=main_kb(),
    )

# ===================== RUN =====================
if __name__ == "__main__":
    print("Bot is running…")
    bot.infinity_polling(skip_pending=True)
