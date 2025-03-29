import os
import sys
import logging
import queue
import threading
import time
from functools import lru_cache
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass
import datetime
from dateutil.relativedelta import relativedelta
import pytesseract
from PIL import Image
import io
import telebot
from telebot import types
from replit import db
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
class Config:
    # Обязательные параметры
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_ID = os.getenv('ADMIN_ID', '1175871225')
    
    # Параметры кредита
    CREDIT_AMOUNT = int(os.getenv('DEFAULT_CREDIT_AMOUNT', '1500000'))
    INTEREST_RATE = float(os.getenv('DEFAULT_INTEREST_RATE', '10'))
    TERM_MONTHS = int(os.getenv('DEFAULT_TERM_MONTHS', '12'))
    PAYMENT_DAY = int(os.getenv('DEFAULT_PAYMENT_DAY', '8'))
    MIN_PAYMENT = int(os.getenv('MIN_PAYMENT', '30000'))
    
    # Настройки Tesseract OCR
    TESSERACT_CONFIG = r'--oem 3 --psm 6 -l rus+eng'
    TESSERACT_PATH = os.getenv('TESSERACT_PATH')
    
    # Настройки кэширования
    CACHE_TIMEOUT = int(os.getenv('CACHE_TIMEOUT', '300'))
    
    # Настройки логирования
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE')

# Проверка обязательных параметров
if not Config.BOT_TOKEN:
    raise ValueError("BOT_TOKEN не указан в переменных окружения!")

# Настройка Tesseract
if Config.TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = Config.TESSERACT_PATH

# Настройка логирования
logging.basicConfig(
    level=Config.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=Config.LOG_FILE,
    filemode='a' if Config.LOG_FILE else None
)
logger = logging.getLogger(__name__)

# Модели данных
@dataclass
class CreditParams:
    amount: int
    rate: float
    term: int
    payment_date: int

@dataclass
class PaymentInfo:
    amount: float
    date: Optional[str] = None
    status: Optional[str] = None
    sender: Optional[str] = None
    phone: Optional[str] = None
    raw_text: Optional[str] = None

# Инициализация бота
bot = telebot.TeleBot(Config.BOT_TOKEN)
message_queue = queue.Queue()
cache = defaultdict(dict)

# Русские праздники
HOLIDAYS = [
    (1, 1), (1, 2), (1, 7), (2, 23), (3, 8),
    (5, 1), (5, 9), (6, 12), (11, 4)
]

# Вспомогательные функции
def is_admin(user_id: str) -> bool:
    return str(user_id) == Config.ADMIN_ID

def get_month_name(month_num: int) -> str:
    months = [
        'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
        'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'
    ]
    return months[month_num - 1]

def format_money(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")

def is_holiday(date: datetime.date) -> bool:
    return date.weekday() >= 5 or (date.month, date.day) in HOLIDAYS

def get_previous_workday(date: datetime.date) -> datetime.date:
    while True:
        date -= datetime.timedelta(days=1)
        if not is_holiday(date):
            return date

# Работа с базой данных
def init_database():
    default_structure = {
        "payments": {},
        "users": {},
        "params": {
            "amount": Config.CREDIT_AMOUNT,
            "rate": Config.INTEREST_RATE,
            "term": Config.TERM_MONTHS,
            "payment_date": Config.PAYMENT_DAY
        },
        "payment_approvers": [],
        "pending_payments": {},
        "user_names": {},
        "notify_users": []
    }

    for key, value in default_structure.items():
        if key not in db:
            db[key] = value

def get_credit_params() -> CreditParams:
    params = db["params"]
    return CreditParams(
        amount=params["amount"],
        rate=params["rate"],
        term=params["term"],
        payment_date=params["payment_date"]
    )

def get_user_name(user_id: str) -> str:
    return db.get("user_names", {}).get(str(user_id), f"ID: {user_id}")

# Клавиатуры
def create_keyboard(is_admin_user: bool = False) -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = ["📊 График платежей"]
    
    if is_admin_user:
        buttons.extend([
            "💰 Внести платёж",
            "♻️ Сбросить платежи",
            "⚙️ Настройки кредита",
            "👥 Управление",
            "🔄 Перезапуск"
        ])
    
    keyboard.add(*buttons)
    return keyboard

def create_management_keyboard() -> types.ReplyKeyboardMarkup:
    buttons = [
        "➕ Добавить получателя",
        "➖ Удалить получателя",
        "📋 Список получателей",
        "➕ Добавить подтверждающего",
        "➖ Удалить подтверждающего",
        "📋 Список подтверждающих",
        "◀️ Вернуться в меню"
    ]
    return types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2).add(*buttons)

# Расчеты платежей
@lru_cache(maxsize=128)
def calculate_monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    if months <= 0:
        return 0
    
    monthly_rate = (annual_rate / 100) / 12
    payment = principal * (monthly_rate * (1 + monthly_rate)**months) / ((1 + monthly_rate)**months - 1)
    return round(payment, 2)

def calculate_payment_schedule(params: CreditParams, payments: Dict[str, float]) -> Tuple[List[Dict], float, float]:
    schedule = []
    balance = params.amount
    total_interest = 0
    total_paid = 0
    
    for month in range(1, params.term + 1):
        is_paid = str(month) in payments
        payment = payments[str(month)] if is_paid else calculate_monthly_payment(balance, params.rate, params.term - month + 1)
        interest = (balance * params.rate / 100) / 12
        principal = payment - interest
        
        if balance <= principal:
            payment = balance + interest
            principal = balance
            balance = 0
        else:
            balance -= principal
        
        total_interest += interest
        total_paid += payment
        
        schedule.append({
            "month": month,
            "payment": payment,
            "interest": interest,
            "principal": principal,
            "balance": balance,
            "is_paid": is_paid
        })
        
        if balance <= 0:
            break
    
    return schedule, total_paid, total_interest

# Обработчики команд
@bot.message_handler(commands=['start', 'help'])
def handle_start(message: types.Message):
    init_database()
    user_id = str(message.chat.id)
    
    if user_id not in db["users"]:
        db["users"][user_id] = {"notify": True}
    
    text = """
🏦 *КРЕДИТНЫЙ АССИСТЕНТ* 🏦

📱 *ДОСТУПНЫЕ ФУНКЦИИ:*
📊 Просмотр графика платежей
💸 Внесение платежей с подтверждением
⚙️ Настройка параметров кредита
👥 Управление пользователями
"""
    bot.send_message(
        message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=create_keyboard(is_admin(user_id))
    )

@bot.message_handler(commands=['id'])
def handle_id_command(message: types.Message):
    bot.reply_to(message, f"🆔 Ваш ID: `{message.chat.id}`", parse_mode="Markdown")

# Обработка платежей
@bot.message_handler(func=lambda m: m.text == "💰 Внести платёж")
def handle_payment_request(message: types.Message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "⛔️ У вас нет прав для внесения платежей.")
        return
    
    params = get_credit_params()
    next_month = 1
    while str(next_month) in db["payments"] and next_month <= params.term:
        next_month += 1
    
    if next_month > params.term:
        bot.reply_to(message, "✅ Все платежи уже внесены!")
        return
    
    payment_date = datetime.datetime.now() + relativedelta(months=next_month-1)
    month_name = get_month_name(payment_date.month)
    
    msg = bot.reply_to(
        message,
        f"💰 *Платёж за {month_name} {payment_date.year}*\n"
        f"Введите сумму (минимум {Config.MIN_PAYMENT:,} руб.):",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Отмена")
    )
    bot.register_next_step_handler(msg, process_payment_input, next_month)

def process_payment_input(message: types.Message, month: int):
    if message.text == "❌ Отмена":
        bot.send_message(
            message.chat.id,
            "Операция отменена",
            reply_markup=create_keyboard(is_admin(message.chat.id)))
        )
        return
    
    try:
        amount = float(message.text.replace(',', '.'))
        params = get_credit_params()
        
        # Расчет остатка
        balance = params.amount
        for m in range(1, month):
            if str(m) in db["payments"]:
                payment = db["payments"][str(m)]
                interest = (balance * params.rate / 100) / 12
                balance -= (payment - interest)
        
        monthly_interest = (balance * params.rate / 100) / 12
        max_payment = balance + monthly_interest
        
        if amount < Config.MIN_PAYMENT or amount > max_payment:
            msg = bot.reply_to(
                message,
                f"⚠️ Сумма должна быть от {Config.MIN_PAYMENT:,} до {max_payment:,.0f} руб.",
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_payment_input, month)
            return
        
        # Создание ожидающего платежа
        payment_id = f"{message.chat.id}_{month}"
        db.setdefault("pending_payments", {})[payment_id] = {
            "amount": amount,
            "month": month,
            "from_user": message.chat.id,
            "timestamp": time.time(),
            "status": "pending"
        }
        
        # Уведомление подтверждающих
        notify_payment_approvers(payment_id, amount, month)
        
        # Подтверждение отправителю
        payment_date = datetime.datetime.now() + relativedelta(months=month-1)
        bot.reply_to(
            message,
            f"✅ Платёж на {amount:,} руб. за {get_month_name(payment_date.month)} "
            f"отправлен на подтверждение!",
            reply_markup=create_keyboard(is_admin(message.chat.id)))
    except ValueError:
        msg = bot.reply_to(message, "❌ Введите корректную сумму")
        bot.register_next_step_handler(msg, process_payment_input, month)

def notify_payment_approvers(payment_id: str, amount: float, month: int):
    params = get_credit_params()
    payment_date = datetime.datetime.now() + relativedelta(months=month-1)
    month_name = get_month_name(payment_date.month)
    
    # Расчет деталей платежа
    balance = params.amount
    for m in range(1, month):
        if str(m) in db["payments"]:
            payment = db["payments"][str(m)]
            interest = (balance * params.rate / 100) / 12
            balance -= (payment - interest)
    
    interest = (balance * params.rate / 100) / 12
    new_balance = balance - (amount - interest)
    
    # Формирование сообщения
    text = (
        f"💰 *Запрос подтверждения платежа*\n\n"
        f"• Месяц: *{month_name} {payment_date.year}*\n"
        f"• Сумма: *{amount:,}* руб.\n"
        f"• Проценты: *{interest:,.2f}* руб.\n"
        f"• Остаток после: *{new_balance:,.2f}* руб.\n\n"
        f"От: {get_user_name(db['pending_payments'][payment_id]['from_user'])}"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{payment_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{payment_id}")
    )
    
    # Отправка подтверждающим
    for approver_id in db.get("payment_approvers", []):
        try:
            bot.send_message(
                int(approver_id),
                text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления подтверждающему {approver_id}: {e}")

# Обработка фото чеков
@bot.message_handler(content_types=['photo'])
def handle_receipt_photo(message: types.Message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        receipt_info = parse_receipt_text(downloaded_file)
        
        if receipt_info and receipt_info.amount:
            show_receipt_confirmation(message, receipt_info)
        else:
            bot.reply_to(message, "❌ Не удалось распознать сумму в чеке")
    except Exception as e:
        logger.error(f"Ошибка обработки чека: {e}")
        bot.reply_to(message, "❌ Ошибка обработки изображения")

def parse_receipt_text(image_bytes: bytes) -> Optional[PaymentInfo]:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image, config=Config.TESSERACT_CONFIG)
        
        # Упрощенный парсинг (можно улучшить)
        amount = None
        date = None
        
        for line in text.split('\n'):
            line = line.lower().strip()
            if 'сумма' in line and not amount:
                parts = line.replace(',', '.').split()
                for part in parts:
                    if part.replace('.', '').isdigit():
                        amount = float(part)
                        break
            
            if not date and (line.count('.') == 2 or line.count('/') == 2):
                date_part = line.split()[0] if ' ' in line else line
                if any(c.isdigit() for c in date_part):
                    date = date_part
        
        return PaymentInfo(
            amount=amount,
            date=date,
            raw_text=text
        ) if amount else None
    except Exception as e:
        logger.error(f"Ошибка OCR: {e}")
        return None

def show_receipt_confirmation(message: types.Message, receipt: PaymentInfo):
    text = [
        "🧾 *Информация из чека:*",
        f"💰 Сумма: *{format_money(receipt.amount)}* ₽"
    ]
    
    if receipt.date:
        text.append(f"📅 Дата: *{receipt.date}*")
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "✅ Использовать как платёж",
            callback_data=f"use_receipt_{receipt.amount}"
        )
    )
    
    bot.reply_to(
        message,
        "\n".join(text),
        parse_mode="Markdown",
        reply_markup=markup
    )

# Обработка callback-запросов
@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_', 'reject_')))
def handle_payment_decision(call: types.CallbackQuery):
    action, payment_id = call.data.split('_', 1)
    pending_payments = db.get("pending_payments", {})
    
    if payment_id not in pending_payments:
        bot.answer_callback_query(call.id, "❌ Платёж уже обработан!")
        return
    
    payment_data = pending_payments[payment_id]
    
    if action == 'approve':
        approve_payment(call, payment_id, payment_data)
    else:
        reject_payment(call, payment_id, payment_data)
    
    bot.answer_callback_query(call.id)

def approve_payment(call: types.CallbackQuery, payment_id: str, payment_data: dict):
    month = str(payment_data["month"])
    amount = payment_data["amount"]
    from_user = payment_data["from_user"]
    
    # Сохранение платежа
    db.setdefault("payments", {})[month] = amount
    del db["pending_payments"][payment_id]
    
    # Уведомление отправителя
    payment_date = datetime.datetime.now() + relativedelta(months=int(month)-1)
    bot.send_message(
        from_user,
        f"✅ Ваш платёж на {amount:,} руб. за {get_month_name(payment_date.month)} подтверждён!",
        reply_markup=create_keyboard(is_admin(from_user))
    )
    
    # Обновление сообщения подтверждающему
    try:
        bot.edit_message_text(
            f"✅ Платёж подтверждён: {amount:,} руб.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
    except Exception as e:
        logger.error(f"Ошибка обновления сообщения: {e}")

def reject_payment(call: types.CallbackQuery, payment_id: str, payment_data: dict):
    from_user = payment_data["from_user"]
    amount = payment_data["amount"]
    month = payment_data["month"]
    
    del db["pending_payments"][payment_id]
    
    # Уведомление отправителя
    payment_date = datetime.datetime.now() + relativedelta(months=month-1)
    bot.send_message(
        from_user,
        f"❌ Ваш платёж на {amount:,} руб. за {get_month_name(payment_date.month)} отклонён",
        reply_markup=create_keyboard(is_admin(from_user))
    )
    
    # Обновление сообщения подтверждающему
    try:
        bot.edit_message_text(
            f"❌ Платёж отклонён: {amount:,} руб.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
    except Exception as e:
        logger.error(f"Ошибка обновления сообщения: {e}")

# Запуск бота
if __name__ == "__main__":
    logger.info("Запуск кредитного бота...")
    init_database()
    
    # Запуск обработчика очереди сообщений
    threading.Thread(target=process_message_queue, daemon=True).start()
    
    try:
        bot.infinity_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
