import telebot
from telebot import types
from replit import db
import datetime
from dateutil.relativedelta import relativedelta
import os
import sys
import logging
import queue
import threading
from functools import lru_cache
from typing import Dict, List, Union, Optional
from collections import defaultdict
import time
import pytesseract # Added for Tesseract OCR
from PIL import Image # Added for Tesseract OCR
import io # Added for Tesseract OCR


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Settings
TOKEN = os.environ['BOT_TOKEN']

# Очередь сообщений
message_queue = queue.Queue()
# Кэш для данных
cache = defaultdict(dict)
CACHE_TIMEOUT = 300  # 5 минут

def clear_expired_cache():
    """Очистка устаревших данных из кэша"""
    current_time = time.time()
    expired_keys = [k for k, v in cache.items() 
                   if current_time - v.get('timestamp', 0) > CACHE_TIMEOUT]
    for k in expired_keys:
        del cache[k]

def process_message_queue():
    """Обработка сообщений из очереди"""
    while True:
        try:
            message = message_queue.get()
            if message is None:
                break
            # Обработка сообщения
            func, args = message
            func(*args)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)
        finally:
            message_queue.task_done()

# Запуск обработчика очереди
message_thread = threading.Thread(target=process_message_queue, daemon=True)
message_thread.start()
CREDIT_AMOUNT = 1_500_000
INTEREST_RATE = 10
TERM_MONTHS = 12  # Максимальный срок в месяцах

# Russian holidays (month, day)
HOLIDAYS = [
    (1, 1), (1, 2), (1, 7), (2, 23), (3, 8), 
    (5, 1), (5, 9), (6, 12), (11, 4)
]

bot = telebot.TeleBot(TOKEN)

def is_holiday(date):
    """Check if date is a holiday or weekend"""
    if date.weekday() >= 5:  # Saturday or Sunday
        return True
    return (date.month, date.day) in HOLIDAYS

def get_previous_workday(date):
    """Returns last working day before given date"""
    while True:
        date -= datetime.timedelta(days=1)
        if not is_holiday(date):
            return date

def create_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        types.KeyboardButton("📊 График платежей"),
        types.KeyboardButton("💸 Внести платёж"),
        types.KeyboardButton("♻️ Сбросить платежи"),
        types.KeyboardButton("⚙️ Настройки кредита"),
        types.KeyboardButton("👥 Управление"),
        types.KeyboardButton("🔄 Перезапуск")
    ]
    keyboard.add(*buttons)
    return keyboard

@bot.message_handler(func=lambda m: m.text == "🔄 Перезапуск")
def restart_bot(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для перезапуска бота.")
        return

    bot.reply_to(message, "🔄 Перезапуск бота...")
    start(message)  # Вызываем функцию start
    os.execv(sys.executable, ['python'] + sys.argv)

@bot.message_handler(commands=['setname'])
def set_user_name(message):
    if str(message.chat.id) != "1175871225":  # Проверка на администратора
        bot.reply_to(message, "⛔️ У вас нет прав для установки имён пользователей.")
        return

    args = message.text.split(maxsplit=2)
    if len(args) != 3:
        bot.reply_to(message, "❌ Использование: /setname ID Имя\nПример: /setname 123456789 Иван")
        return

    try:
        user_id = args[1]
        name = args[2]
        if "user_names" not in db:
            db["user_names"] = {}
        db["user_names"][user_id] = name
        bot.reply_to(message, f"✅ Установлено имя *{name}* для ID: `{user_id}`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")

@bot.message_handler(commands=['names'])
def show_user_names(message):
    if str(message.chat.id) != "1175871225":  # Проверка на администратора
        bot.reply_to(message, "⛔️ У вас нет прав для просмотра имён пользователей.")
        return

    if "user_names" not in db or not db["user_names"]:
        bot.reply_to(message, "📋 Список имён пользователей пуст.")
        return

    names_list = "\n".join([f"• `{user_id}`: *{name}*" for user_id, name in db["user_names"].items()])
    bot.reply_to(message, f"📋 *Список пользователей:*\n\n{names_list}", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👥 Управление")
def notification_settings(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для управления.")
        return

    bot.send_message(
        message.chat.id,
        "*Управление пользователями:*",
        parse_mode="Markdown",
        reply_markup=create_management_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "➕ Добавить получателя")
def add_notification_recipient(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для управления уведомлениями.")
        return

    msg = bot.reply_to(message, "Введите ID пользователя для добавления в получатели уведомлений:")
    bot.register_next_step_handler(msg, process_add_recipient)

@bot.message_handler(func=lambda m: m.text == "➕ Добавить подтверждающего")
def add_payment_approver(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для управления подтверждающими.")
        return

    msg = bot.reply_to(message, "Введите ID пользователя для добавления в подтверждающие:")
    bot.register_next_step_handler(msg, process_add_approver)

def process_add_approver(message):
    try:
        user_id = str(int(message.text))
        approvers = db.get("payment_approvers", [])

        if user_id in approvers:
            bot.reply_to(message, "❌ Этот пользователь уже является подтверждающим!")
            return

        approvers.append(user_id)
        db["payment_approvers"] = approvers
        bot.reply_to(message, f"✅ Пользователь с ID {user_id} добавлен в список подтверждающих!")
    except ValueError:
        bot.reply_to(message, "❌ Некорректный ID пользователя. Введите числовое значение.")

@bot.message_handler(func=lambda m: m.text == "➖ Удалить подтверждающего")
def remove_payment_approver(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для управления подтверждающими.")
        return

    msg = bot.reply_to(message, "Введите ID пользователя для удаления из подтверждающих:")
    bot.register_next_step_handler(msg, process_remove_approver)

def process_remove_approver(message):
    try:
        user_id = str(int(message.text))
        approvers = db.get("payment_approvers", [])

        if user_id not in approvers:
            bot.reply_to(message, "❌ Этот пользователь не является подтверждающим!")
            return

        approvers.remove(user_id)
        db["payment_approvers"] = approvers
        bot.reply_to(message, f"✅ Пользователь с ID {user_id} удален из списка подтверждающих!")
    except ValueError:
        bot.reply_to(message, "❌ Некорректный ID пользователя. Введите числовое значение.")

@bot.message_handler(func=lambda m: m.text == "📋 Список подтверждающих")
def list_payment_approvers(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для просмотра подтверждающих.")
        return

    approvers = db.get("payment_approvers", [])
    if not approvers:
        bot.reply_to(message, "📋 Список подтверждающих пуст!")
        return

    approvers_list = "\n".join([f"• ID: `{user_id}` - *{get_user_name(user_id)}*" for user_id in approvers])
    bot.reply_to(message, f"📋 *Список подтверждающих:*\n\n{approvers_list}", parse_mode="Markdown")

def process_add_recipient(message):
    try:
        user_id = str(int(message.text))  # Проверяем, что ID числовой
        notify_users = db.get("notify_users", [])

        if user_id in notify_users:
            bot.reply_to(message, "❌ Этот пользователь уже получает уведомления!")
            return

        notify_users.append(user_id)
        db["notify_users"] = notify_users
        bot.reply_to(message, f"✅ Пользователь с ID {user_id} добавлен в список получателей уведомлений!")
    except ValueError:
        bot.reply_to(message, "❌ Некорректный ID пользователя. Введите числовое значение.")

@bot.message_handler(func=lambda m: m.text == "➖ Удалить получателя")
def remove_notification_recipient(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для управления уведомлениями.")
        return

    msg = bot.reply_to(message, "Введите ID пользователя для удаления:")
    bot.register_next_step_handler(msg, process_remove_recipient)

def process_remove_recipient(message):
    try:
        user_id = str(int(message.text))
        notify_users = db.get("notify_users", [])

        if user_id not in notify_users:
            bot.reply_to(message, "❌ Этот пользователь не получает уведомления!")
            return

        notify_users.remove(user_id)
        db["notify_users"] = notify_users
        bot.reply_to(message, f"✅ Пользователь с ID {user_id} удален из списка получателей уведомлений!")
    except ValueError:
        bot.reply_to(message, "❌ Некорректный ID пользователя. Введите числовое значение.")

@bot.message_handler(func=lambda m: m.text == "◀️ Вернуться в меню")
def back_to_menu(message):
    bot.send_message(message.chat.id, "Возвращаемся в главное меню", reply_markup=create_keyboard())

@bot.message_handler(func=lambda m: m.text == "📋 Список получателей")
def list_notification_recipients(message):
    if str(message.chat.id) != "1175871225":  # Проверяем ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для просмотра получателей уведомлений.")
        return

    notify_users = db.get("notify_users", [])
    if not notify_users:
        bot.reply_to(message, "📋 Список получателей уведомлений пуст!")
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for user_id in notify_users:
        user_name = get_user_name(user_id)
        name_button = types.InlineKeyboardButton(
            f"✏️ Изменить имя: {user_name} (ID: {user_id})",
            callback_data=f"setname_{user_id}"
        )
        markup.add(name_button)

    recipients_list = "\n".join([
        f"• ID: `{user_id}` - *{get_user_name(user_id)}*" 
        for user_id in notify_users
    ])
    bot.reply_to(
        message, 
        f"📋 *Список получателей уведомлений:*\n\n{recipients_list}\n\n_Нажмите на кнопку для изменения имени_",
        parse_mode="Markdown",
        reply_markup=markup
    )

def check_notifications():
    """Check if notifications need to be sent"""
    today = datetime.datetime.now().date()
    next_month = today + relativedelta(months=1)
    notification_date = datetime.date(next_month.year, next_month.month, 8)

    if is_holiday(notification_date):
        notification_date = get_previous_workday(notification_date)

    if today == notification_date:
        for user_id in db.get("users", {}):
            if db["users"][user_id].get("notify", False):
                try:
                    bot.send_message(
                        int(user_id),  # Convert string ID to integer
                        "⏰ Напоминание: следующий платёж через 7 дней!\n"
                        "Используйте /show для просмотра графика",
                        reply_markup=create_keyboard()
                    )
                except Exception as e:
                    print(f"Ошибка уведомления: {e}")

@bot.message_handler(commands=['id'])
def show_id(message):
    bot.reply_to(message, f"🆔 Ваш ID: `{message.chat.id}`", parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def start(message):
    # Initialize database
    if "payments" not in db:
        db["payments"] = {}
    if "users" not in db:
        db["users"] = {}
    if "params" not in db:
        db["params"] = {
            "amount": CREDIT_AMOUNT,
            "rate": INTEREST_RATE,
            "term": TERM_MONTHS,
            "payment_date": 8
        }
    if "payment_approvers" not in db:
        db["payment_approvers"] = []


    # Register user
    user_id = str(message.chat.id)
    if user_id not in db["users"]:
        db["users"][user_id] = {"notify": True}

    text = """
🏦 *КРЕДИТНЫЙ АССИСТЕНТ* 🏦

📱 *ДОСТУПНЫЕ ФУНКЦИИ:*

📊 *График платежей*
└ Просмотр текущих и будущих платежей
└ Автоматический расчет остатка
└ История внесенных платежей

💸 *Внесение платежей*
└ Минимальный платеж: 30 000 ₽
└ Двойная верификация платежей
└ Автоматический расчет процентов

⚙️ *Управление кредитом*
└ Настройка суммы и ставки
└ Изменение срока кредита
└ Установка даты платежа

👥 *Система уведомлений*
└ Напоминания за 7 дней
└ Уведомления о новых платежах
└ Гибкая настройка получателей

*Для начала работы используйте меню ниже* ⤵️
"""
    # Определяем тип клавиатуры в зависимости от прав пользователя
    keyboard = create_admin_keyboard() if str(message.chat.id) == "1175871225" else create_user_keyboard()

    bot.send_message(message.chat.id, text, 
                   parse_mode="Markdown",
                   reply_markup=keyboard)



@bot.message_handler(func=lambda m: m.text == "💰 Внести платёж")
def handle_payment(message):
    if str(message.chat.id) != "1175871225":  # Проверка на администратора
        bot.reply_to(message, "⛔️ У вас нет прав для внесения платежей.")
        return
    params = db["params"]
    # Находим первый неоплаченный месяц
    next_month = 1
    while str(next_month) in db["payments"] and next_month <= params["term"]:
        next_month += 1

    if next_month > params["term"]:
        bot.reply_to(message, "✅ Все платежи уже внесены!", parse_mode="Markdown")
        return

    # Создаем клавиатуру с кнопкой отмены
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    cancel_button = types.KeyboardButton("❌ Отмена")
    keyboard.add(cancel_button)

    # Определяем месяц и год платежа
    current_date = datetime.datetime.now() + relativedelta(months=next_month-1)
    months = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
              'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
    month_name = months[current_date.month - 1]

    msg = bot.reply_to(
        message, 
        f"💰 *Платёж за {month_name} {current_date.year}*\nВведите сумму платежа (минимум 30 000 руб.):\n\n_Для отмены нажмите кнопку ❌ Отмена_",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    bot.register_next_step_handler(msg, process_payment_amount, next_month)

@lru_cache(maxsize=128)
def get_cached_data(key: str, timeout: int = CACHE_TIMEOUT) -> Optional[dict]:
    """Получение данных из кэша"""
    if key in cache and time.time() - cache[key].get('timestamp', 0) < timeout:
        return cache[key].get('data')
    return None

def set_cached_data(key: str, data: dict) -> None:
    """Сохранение данных в кэш"""
    cache[key] = {
        'data': data,
        'timestamp': time.time()
    }

def calculate_monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    """
    Рассчитывает фиксированный ежемесячный платеж используя формулу амортизации
    с кэшированием результатов
    """
    cache_key = f"payment_{principal}_{annual_rate}_{months}"
    cached_result = get_cached_data(cache_key)
    if cached_result:
        return cached_result['payment']

    try:
        monthly_rate = (annual_rate / 100) / 12
        if months <= 0:
            return 0
        payment = principal * (monthly_rate * (1 + monthly_rate)**months) / ((1 + monthly_rate)**months - 1)
        result = round(payment, 2)
        set_cached_data(cache_key, {'payment': result})
        return result
    except Exception as e:
        logger.error(f"Ошибка расчета платежа: {e}")
        return 0

def process_payment_amount(message, month):
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Операция отменена", reply_markup=create_keyboard())
        return

    params = db["params"]
    try:
        amount = int(message.text)
        # Сохраняем временный платеж для подтверждения
        if "pending_payments" not in db:
            db["pending_payments"] = {}

        payment_id = f"{message.chat.id}_{month}"
        db["pending_payments"][payment_id] = {
            "amount": amount,
            "month": month,
            "from_user": message.chat.id,
            "status": "pending"
        }
        # Расчет оставшегося долга
        balance = params["amount"]
        total_interest = 0
        for m in range(1, month):
            if str(m) in db["payments"]:
                payment = db["payments"][str(m)]
                interest = (balance * params["rate"] / 100) / 12
                total_interest += interest
                balance = balance - (payment - interest)

        # Расчет необходимого платежа
        remaining_months = params["term"] - month + 1
        monthly_payment = calculate_monthly_payment(balance, params["rate"], remaining_months)
        monthly_interest = (balance * params["rate"] / 100) / 12

        min_payment = 30_000  # Фиксированный минимальный платеж
        max_payment = balance + monthly_interest

        if not min_payment <= amount <= max_payment:
            msg = bot.reply_to(
                message, 
                f"⚠️ Сумма должна быть:\n• Минимум: *{min_payment:,.0f}* руб.\n• Максимум: *{max_payment:,.0f}* руб.\n\nВведите сумму снова:", 
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_payment_amount, month)
            return

        # Отправляем запрос на подтверждение конкретному пользователю
        payment_approvers = db.get("payment_approvers", [])
        if not payment_approvers:
            bot.reply_to(message, "❌ Нет пользователей для подтверждения платежа!")
            return

        payment_id = f"{message.chat.id}_{month}"

        # Определяем месяц и год платежа
        current_date = datetime.datetime.now() + relativedelta(months=month-1)
        months = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
                  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
        month_name = months[current_date.month - 1]

        # Информация о платеже для отправителя
        sender_text = (
            "╔═══════════════════════╗\n"
            "║   💫 ПЛАТЁЖ ОТПРАВЛЕН   ║\n"
            "╚═══════════════════════╝\n\n"
            "📊 *ДЕТАЛИ ПЛАТЕЖА:*\n"
            f"├─💰 Сумма: *{amount:,}* ₽\n"
            f"├─📅 Период: *{month_name} {current_date.year}*\n"
            f"└─📈 Платёж №*{month}* из {params['term']}\n\n"
            "⏳ *Ожидайте подтверждения...*"
        )
        bot.reply_to(message, sender_text, parse_mode="Markdown")

        for approver_id in payment_approvers:
            markup = types.InlineKeyboardMarkup()
            approve_button = types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{payment_id}")
            reject_button = types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{payment_id}")
            markup.add(approve_button, reject_button)

            # Расчет остатка и процентов для подтверждающего
            cur_balance = params["amount"]
            total_interest = 0
            for m in range(1, month):
                if str(m) in db["payments"]:
                    payment = db["payments"][str(m)]
                    interest = (cur_balance * params["rate"] / 100) / 12
                    total_interest += interest
                    cur_balance = cur_balance - (payment - interest)

            current_interest = (cur_balance * params["rate"] / 100) / 12
            new_balance = cur_balance - (amount - current_interest)

            # Расширенная информация для подтверждающего
            confirmation_text = (
                f"💰 *Запрос на подтверждение платежа*\n\n"
                f"*Детали платежа:*\n"
                f"• Месяц: *{month_name} {current_date.year}*\n"
                f"• Номер платежа: *{month}* из {params['term']}\n"
                f"• Сумма платежа: *{amount:,}* руб.\n"
                f"• Проценты: *{current_interest:,.2f}* руб.\n"
                f"• Основной долг: *{(amount - current_interest):,.2f}* руб.\n\n"
                f"• От пользователя: `{message.chat.id}`" + (f" (*{db.get('user_names', {}).get(str(message.chat.id), 'Без имени')}*)" if db.get('user_names', {}).get(str(message.chat.id)) else "") + "\n\n"
                f"*Итого после платежа:*\n"
                f"• Остаток долга: *{new_balance:,.2f}* руб.\n"
                f"• Внесено платежей: *{sum(db['payments'].values()) + amount:,}* руб.\n\n"
                f"Подтвердите или отклоните платёж:"
            )

            try:
                bot.send_message(int(approver_id), confirmation_text, reply_markup=markup, parse_mode="Markdown")
            except Exception as e:
                print(f"Ошибка отправки запроса подтверждения: {e}")
        return

        # Расчет оставшегося долга
        params = db["params"]
        balance = params["amount"]
        paid_months = 0
        for m in range(1, params["term"] + 1):
            if str(m) in db["payments"]:
                paid_months += 1
                payment = db["payments"][str(m)]
                interest = (balance * params["rate"] / 100) / 12
                balance = balance - (payment - interest)

        # Определяем месяц и год платежа
        current_date = datetime.datetime.now() + relativedelta(months=month-1)
        months = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
                  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
        month_name = months[current_date.month - 1]

        notification = (
            f"💰 *Новый платёж внесен!*\n\n"
            f"• Месяц: *{month_name} {current_date.year}*\n"
            f"• Номер платежа: *{paid_months}* из {params['term']}\n"
            f"• Сумма платежа: *{amount:,}* руб.\n"
            f"• Остаток долга: *{balance:,.2f}* руб."
        )

        # Отправляем уведомления выбранным пользователям
        notify_users = db.get("notify_users", [])
        for user_id in notify_users:
            try:
                bot.send_message(int(user_id), notification, parse_mode="Markdown")
            except Exception as e:
                print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

        # Отправляем короткое подтверждение
        bot.reply_to(message, notification, parse_mode="Markdown")

    except ValueError:
        msg = bot.reply_to(message, "❌ Введите числовое значение\nПопробуйте снова:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_payment_amount, month)

@bot.message_handler(commands=['show'])
@bot.message_handler(func=lambda m: m.text == "📊 График платежей")
def show_schedule_button(message):
    show(message)

@lru_cache(maxsize=128)
def calculate_schedule(params: tuple, payments: tuple) -> str:
    """
    Рассчитывает график платежей с кэшированием
    params: кортеж пар (ключ, значение) параметров кредита
    payments: кортеж пар (ключ, значение) уже внесенных платежей
    """
    params_dict = dict(params)
    payments_dict = dict(payments)

    months = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
              'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

    response = (
        "╔══════════════════════╗\n"
        "║   🏦 *КРЕДИТ ИНФО*   ║\n"
        "╚══════════════════════╝\n\n"
        "📊 *ОСНОВНЫЕ ПАРАМЕТРЫ:*\n"
        f"├─💰 Сумма: *{params_dict['amount']:,}* ₽\n"
        f"├─⏳ Срок: *{params_dict['term']}* мес.\n"
        f"└─📈 Ставка: *{params_dict['rate']}%*\n\n"
        "╔═══════════════════════╗\n"
        "║  📅 *ГРАФИК ПЛАТЕЖЕЙ* ║\n"
        "╚═══════════════════════╝\n\n"
    )

    start_date = datetime.datetime.now()
    month_count = 1
    balance = params_dict["amount"]  # Инициализируем balance здесь

    while balance > 0 and month_count <= params_dict["term"]:
        payment_date = params_dict.get("payment_date", 8)
        current_date = start_date.replace(day=payment_date) + relativedelta(months=month_count-1)
        is_paid = str(month_count) in payments_dict

        if is_paid:
            payment = payments_dict[str(month_count)]
            status = "✅ Внесённый платёж"
        else:
            remaining_months = params_dict["term"] - month_count + 1
            payment = calculate_monthly_payment(balance, params_dict["rate"], remaining_months)
            status = "💭 Приблизительный платёж"

        interest = (balance * params_dict["rate"] / 100) / 12
        principal_payment = payment - interest

        if balance <= principal_payment:
            payment = balance + interest
            balance = 0
        else:
            balance = balance - principal_payment
        month_name = months[current_date.month - 1]
        response += (
            f"┌─ *{month_name.upper()} {current_date.year}* ─────────┐\n"
            f"│ {status}\n"
            f"├─💸 Платёж: *{payment:,.2f}* ₽\n"
            f"├─📊 Проценты: *{interest:,.2f}* ₽\n"
            f"├─💱 Основной долг: *{(payment - interest):,.2f}* ₽\n"
            f"└─💰 Остаток: *{balance:,.2f}* ₽\n\n"
        )

        if balance <= 0:
            break

        month_count += 1

    # Подсчет общей суммы платежей и остатка
    total_payments = sum(payment for payment in payments_dict.values())
    total_interest = 0
    initial_balance = params_dict["amount"]
    for m in range(1, params_dict["term"] + 1):
        interest = (initial_balance * params_dict["rate"] / 100) / 12
        total_interest += interest
        if str(m) in payments_dict:
            payment = payments_dict[str(m)]
            initial_balance = initial_balance - (payment - interest)

    # Расчет остатка и будущих процентов
    remaining_to_pay = params_dict["amount"]
    actual_interest = 0
    remaining_months = params_dict["term"]

    # Расчет для уже внесенных платежей
    paid_months = sorted([int(m) for m in payments_dict.keys()])
    for m in paid_months:
        payment = payments_dict[str(m)]
        month_interest = (remaining_to_pay * params_dict["rate"] / 100) / 12
        actual_interest += month_interest
        remaining_to_pay -= (payment - month_interest)
        remaining_months -= 1

    # Добавляем будущие проценты для оставшегося долга
    if remaining_months > 0:
        monthly_rate = (params_dict["rate"] / 100) / 12
        future_interest = remaining_to_pay * monthly_rate * remaining_months

    total_remaining = remaining_to_pay + future_interest
    total_amount = params_dict["amount"] + total_interest

    response += (
        "\n╔═══════════════════════╗\n"
        "║      💫 *ИТОГО*       ║\n"
        "╚═══════════════════════╝\n\n"
        f"├─✅ Внесено: *{total_payments:,.2f}* ₽\n"
        f"├─⏳ Осталось: *{total_remaining:,.2f}* ₽\n"
        f"├─💰 Общая сумма: *{total_amount:,.2f}* ₽\n"
        f"└─📊 Проценты: *{total_interest:,.2f}* ₽\n"
    )

    return response

def show(message):
    params = db["params"]
    if "payments" not in db:
        db["payments"] = {}

    # Преобразуем словари в кортежи для кэширования
    params_tuple = tuple(sorted(params.items()))
    payments_tuple = tuple(sorted(db["payments"].items()))

    # Получаем кэшированный результат
    response = calculate_schedule(params_tuple, payments_tuple)
    bot.send_message(message.chat.id, response, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "♻️ Сбросить платежи")
def reset_payments(message):
    # Проверяем, является ли пользователь владельцем
    if str(message.chat.id) != "1175871225":  # ID администратора
        bot.reply_to(message, "⛔️ У вас нет прав для сброса платежей.", reply_markup=create_keyboard())
        return

    # Создаем inline клавиатуру для подтверждения
    markup = types.InlineKeyboardMarkup()
    confirm_button = types.InlineKeyboardButton("✅ Подтвердить сброс", callback_data="confirm_reset")
    cancel_button = types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")
    markup.add(confirm_button, cancel_button)

    warning_text = (
        "⚠️ *ВНИМАНИЕ!*\n\n"
        "Вы собираетесь сбросить *ВСЕ* внесённые платежи!\n"
        "Это действие *нельзя* отменить.\n\n"
        "Вы уверены, что хотите продолжить?"
    )

    bot.reply_to(message, warning_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["confirm_reset", "cancel_reset"])
def handle_reset_confirmation(call):
    if str(call.message.chat.id) != "1175871225":  # Проверка на администратора
        bot.answer_callback_query(call.id, "⛔️ У вас нет прав для этого действия")
        return

    if call.data == "confirm_reset":
        if "payments" in db:
            db["payments"] = {}
            bot.edit_message_text(
                "✅ Все внесённые платежи успешно сброшены!",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None
            )
            show(call.message)  # Показываем пустой график платежей
    else:  # cancel_reset
        bot.edit_message_text(
            "❌ Операция сброса платежей отменена",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )

    bot.answer_callback_query(call.id)

def create_user_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        "📊 График платежей",
    ]
    keyboard.add(*buttons)
    return keyboard

def create_admin_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        types.KeyboardButton("📊 График платежей"),
        types.KeyboardButton("💰 Внести платёж"),
        types.KeyboardButton("🔄 Сбросить платежи"),
        types.KeyboardButton("⚙️ Настройки кредита"),
        types.KeyboardButton("👥 Управление"),
        types.KeyboardButton("🔄 Перезапуск")
    ]
    keyboard.add(*buttons)
    return keyboard

def create_management_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        "➕ Добавить получателя",
        "➖ Удалить получателя",
        "📋 Список получателей",
        "➕ Добавить подтверждающего",
        "➖ Удалить подтверждающего",
        "📋 Список подтверждающих",
        "◀️ Вернуться в меню"
    ]
    keyboard.add(*[types.KeyboardButton(button) for button in buttons])
    return keyboard

@bot.message_handler(func=lambda m: m.text == "⚙️ Настройки кредита")
def configure_credit(message):
    if str(message.chat.id) != "1175871225":  # Проверка на администратора
        bot.reply_to(message, "⛔️ У вас нет прав для настройки кредита.")
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    msg = bot.send_message(message.chat.id, "Введите сумму кредита:", reply_markup=keyboard)
    bot.register_next_step_handler(msg, process_amount)

def process_amount(message):
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Операция отменена", reply_markup=create_keyboard())
        return
    try:
        amount = int(message.text)
        db["params"]["amount"] = amount
        msg = bot.send_message(message.chat.id, "Введите процентную ставку (%):", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Отмена"))
        bot.register_next_step_handler(msg, process_rate)
    except ValueError:
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("❌ Отмена")
        msg = bot.send_message(message.chat.id, "Некорректный ввод. Пожалуйста, введите число.", reply_markup=keyboard)
        bot.register_next_step_handler(msg, process_amount)

def process_rate(message):
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Операция отменена", reply_markup=create_keyboard())
        return
    try:
        rate = float(message.text)
        db["params"]["rate"] = rate
        msg = bot.send_message(message.chat.id, "Введите срок кредита (в месяцах):", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Отмена"))
        bot.register_next_step_handler(msg, process_term)
    except ValueError:
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("❌ Отмена")
        msg = bot.send_message(message.chat.id, "Некорректный ввод. Пожалуйста, введите число.", reply_markup=keyboard)
        bot.register_next_step_handler(msg, process_rate)

@bot.callback_query_handler(func=lambda call: call.data.startswith('setname_'))
def handle_setname_button(call):
    if str(call.message.chat.id) != "1175871225":
        bot.answer_callback_query(call.id, "⛔️ У вас нет прав для изменения имён")
        return

    user_id = call.data.split('_')[1]
    msg = bot.send_message(
        call.message.chat.id,
        f"Введите новое имя для пользователя с ID: `{user_id}`\n\n_Для отмены напишите_ 'отмена'",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, process_new_name, user_id)

def get_user_name(user_id: str) -> str:
    """Получить имя пользователя по ID"""
    return db.get("user_names", {}).get(str(user_id), f"ID: {user_id}")

def process_new_name(message, user_id):
    if message.text.lower() == 'отмена':
        bot.reply_to(message, "❌ Операция отменена")
        return

    if "user_names" not in db:
        db["user_names"] = {}

    # Сохраняем имя пользователя
    new_name = message.text
    db["user_names"][str(user_id)] = new_name

    # Обновляем имя во всех списках
    update_text = (
        f"✅ Установлено имя *{new_name}* для ID: `{user_id}`\n\n"
        "Имя обновлено в:\n"
        "• Списке получателей уведомлений\n"
        "• Списке подтверждающих платежи\n"
        "• Общем списке пользователей"
    )

    bot.reply_to(message, update_text, parse_mode="Markdown")
    # Обновляем список
    list_notification_recipients(message)

@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_', 'reject_')))
def handle_payment_confirmation(call):
    """Обработка подтверждения платежа через очередь сообщений"""
    try:
        # Добавляем обработку в очередь
        message_queue.put((process_payment_confirmation, (call,)))
    except Exception as e:
        logger.error(f"Ошибка добавления в очередь: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки запроса")

def process_payment_confirmation(call):
    try:
        action, payment_id = call.data.split('_', 1)  # Используем maxsplit=1
        if "pending_payments" not in db:
            db["pending_payments"] = {}

        pending_payment = db["pending_payments"].get(payment_id)

        if not pending_payment:
            bot.answer_callback_query(call.id, "❌ Платёж не найден или уже обработан!")
            return

        if action == 'approve':
            # Подтверждаем платёж
            month = pending_payment["month"]
            amount = pending_payment["amount"]
            from_user = pending_payment["from_user"]

            if "payments" not in db:
                db["payments"] = {}

            db["payments"][str(month)] = amount
            del db["pending_payments"][payment_id]

            # Уведомляем отправителя о подтверждении
            try:
                # Получаем информацию о платеже
                params = db["params"]
                current_date = datetime.datetime.now() + relativedelta(months=month-1)
                months = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
                         'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
                month_name = months[current_date.month - 1]

                # Расчет остатка и процентов
                balance = params["amount"]
                total_interest = 0
                for m in range(1, month):
                    if str(m) in db["payments"]:
                        payment = db["payments"][str(m)]
                        interest = (balance * params["rate"] / 100) / 12
                        total_interest += interest
                        balance = balance - (payment - interest)

                current_interest = (balance * params["rate"] / 100) / 12
                new_balance = balance - (amount - current_interest)

                payment_info = (
                    "╔═══════════════════════╗\n"
                    "║    ✅ ПЛАТЁЖ ПРИНЯТ    ║\n"
                    "╚═══════════════════════╝\n\n"
                    f"📅 *Период:* {month_name} {current_date.year}\n"
                    f"📊 *Платёж №{month}* из {params['term']}\n\n"
                    "💰 *ДЕТАЛИ ПЛАТЕЖА:*\n"
                    f"├─💵 Сумма: *{amount:,}* ₽\n"
                    f"├─📈 Проценты: *{current_interest:,.2f}* ₽\n"
                    f"└─💱 Основной долг: *{(amount - current_interest):,.2f}* ₽\n\n"
                    "📊 *ОБЩАЯ ИНФОРМАЦИЯ:*\n"
                    f"├─💰 Остаток: *{new_balance:,.2f}* ₽\n"
                    f"└─✅ Всего внесено: *{sum(db['payments'].values()):,}* ₽"
                )

                # Отправляем сообщение о подтверждении
                bot.send_message(from_user, payment_info, parse_mode="Markdown")
                # Отдельно отправляем клавиатуру
                keyboard = create_admin_keyboard() if str(from_user) == "1175871225" else create_user_keyboard()
                bot.send_message(from_user, "Выберите действие:", reply_markup=keyboard)
            except Exception as e:
                print(f"Ошибка отправки уведомления: {e}")

            # Обновляем сообщение для подтверждающего
            try:
                # Удаляем кнопки подтверждения
                bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
                # Отправляем новое сообщение с результатом
                bot.send_message(
                    call.message.chat.id,
                    payment_info,
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Ошибка обновления сообщения: {e}")

        else:  # reject
            # Отклоняем платёж
            from_user = pending_payment["from_user"]
            del db["pending_payments"][payment_id]

            # Уведомляем отправителя об отклонении
            try:
                reject_info = (
                    "╔═══════════════════════╗\n"
                    "║    ❌ ПЛАТЁЖ ОТКЛОНЁН   ║\n"
                    "╚═══════════════════════╝\n\n"
                    "📊 *ДЕТАЛИ ПЛАТЕЖА:*\n"
                    f"├─💰 Сумма: *{pending_payment['amount']:,}* ₽\n"
                    f"└─📈 Платёж №*{pending_payment['month']}* из {db['params']['term']}"
                )
                bot.send_message(from_user, reject_info, parse_mode="Markdown")
            except Exception as e:
                print(f"Ошибка отправки уведомления: {e}")

            # Обновляем сообщение с кнопками
            try:
                # Обновляем сообщение об отклонении
                bot.edit_message_text(
                    "❌ Платёж отклонён!",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
                # Возвращаем клавиатуру администратора
                keyboard = create_admin_keyboard() if str(call.message.chat.id) == "1175871225" else create_user_keyboard()
                bot.send_message(call.message.chat.id, "Выберите действие:", reply_markup=keyboard)
            except Exception as e:
                print(f"Ошибка обновления сообщения: {e}")

        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Ошибка обработки подтверждения платежа: {e}", exc_info=True)
        try:
            bot.answer_callback_query(call.id, "❌ Произошла ошибка при обработке платежа")
        except:
            logger.error("Не удалось отправить уведомление об ошибке")

def process_term(message):
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Операция отменена", reply_markup=create_keyboard())
        return
    try:
        term = int(message.text)
        if term <= 0 or term > TERM_MONTHS:
            msg = bot.send_message(message.chat.id, f"Срок должен быть от 1 до {TERM_MONTHS} месяцев. Попробуйте снова:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Отмена"))
            bot.register_next_step_handler(msg, process_term)
            return
        db["params"]["term"] = term
        msg = bot.send_message(message.chat.id, "Введите дату ежемесячного платежа (число от 1 до 28):", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Отмена"))
        bot.register_next_step_handler(msg, process_payment_date)
    except ValueError:
        bot.send_message(message.chat.id, "Некорректный ввод. Пожалуйста, введите число.")
        process_rate(message)

def process_payment_date(message):
    try:
        payment_date = int(message.text)
        if 1 <= payment_date <= 28:
            db["params"]["payment_date"] = payment_date
            bot.send_message(message.chat.id, "Параметры кредита успешно изменены!", reply_markup=create_keyboard())
            show(message)  # Показываем график платежей после настройки
        else:
            msg = bot.send_message(message.chat.id, "Дата должна быть от 1 до 28. Попробуйте снова:")
            bot.register_next_step_handler(msg, process_payment_date)
    except ValueError:
        msg = bot.send_message(message.chat.id, "Некорректный ввод. Введите число от 1 до 28:")
        bot.register_next_step_handler(msg, process_payment_date)


def main():
    try:
        logger.info("Бот запускается...")
        if not TOKEN:
            raise ValueError("BOT_TOKEN не установлен в переменных окружения!")

        # Устанавливаем имя администратора при запуске
        if "user_names" not in db:
            db["user_names"] = {}
        db["user_names"]["1175871225"] = "Евгений"

        logger.info("Настройки бота:")
        logger.info(f"- Сумма кредита: {CREDIT_AMOUNT:,} руб.")
        logger.info(f"- Ставка: {INTEREST_RATE}%")
        logger.info(f"- Срок: {TERM_MONTHS} месяцев")

        # Инициализация базы данных
        init_database()

        # Запуск бота
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)

def init_database():
    """Инициализация базы данных"""
    default_structure = {
        "payments": {},
        "users": {},
        "params": {
            "amount": CREDIT_AMOUNT,
            "rate": INTEREST_RATE,
            "term": TERM_MONTHS,
            "payment_date": 8
        },
        "payment_approvers": [],
        "pending_payments": {},
        "user_names": {}  # Словарь для хранения имён пользователей
    }

    for key, default_value in default_structure.items():
        if key not in db:
            db[key] = default_value

    logger.info("База данных инициализирована")

def parse_receipt(text):
    """Извлекает информацию из чека T-Bank"""
    try:
        lines = text.split('\n')
        receipt_data = {
            'amount': None,
            'date': None,
            'status': None,
            'sender': None,
            'phone': None,
            'full_text': text
        }
        
        for i, line in enumerate(lines):
            line = line.strip()
            
            # Поиск суммы (после слова "Сумма")
            if 'сумма' in line.lower():
                # Ищем число в следующей строке или после двоеточия
                amount_str = ''
                if ':' in line:
                    amount_str = line.split(':')[1]
                elif i + 1 < len(lines):
                    amount_str = lines[i + 1]
                    
                # Извлекаем числа
                amount_str = ''.join(c for c in amount_str if c.isdigit() or c in '., ')
                if amount_str:
                    receipt_data['amount'] = float(amount_str.replace(',', '.').replace(' ', ''))
            
            # Поиск даты в формате dd.mm.yyyy
            if line.count('.') == 2 and len(line) >= 8:
                date_parts = line.split()
                for part in date_parts:
                    if part.count('.') == 2 and all(c.isdigit() or c == '.' for c in part):
                        receipt_data['date'] = part
                        break
            
            # Поиск статуса
            if 'статус' in line.lower():
                if ':' in line:
                    receipt_data['status'] = line.split(':')[1].strip()
                elif i + 1 < len(lines):
                    receipt_data['status'] = lines[i + 1].strip()
            
            # Поиск отправителя
            if 'отправитель' in line.lower():
                if ':' in line:
                    receipt_data['sender'] = line.split(':')[1].strip()
                elif i + 1 < len(lines):
                    receipt_data['sender'] = lines[i + 1].strip()
            
            # Поиск телефона
            if 'телефон' in line.lower():
                next_line = lines[i + 1] if i + 1 < len(lines) else ''
                phone = next_line.strip() if ':' not in line else line.split(':')[1].strip()
                if phone and ('+' in phone or phone.replace(' ', '').isdigit()):
                    receipt_data['phone'] = phone
        
        return receipt_data
    except Exception as e:
        logger.error(f"Ошибка парсинга чека: {e}")
        return None

def recognize_text_with_tesseract(image_bytes):
    """Распознает текст на изображении с помощью Tesseract OCR"""
    try:
        # Открываем изображение из байтов
        image = Image.open(io.BytesIO(image_bytes))
        
        # Настраиваем параметры распознавания
        custom_config = r'--oem 3 --psm 6 -l rus+eng'
        
        # Распознаем текст
        text = pytesseract.image_to_string(image, config=custom_config)
        
        # Парсим информацию из чека
        receipt_info = parse_receipt(text)
        
        return receipt_info
    except Exception as e:
        logger.error(f"Ошибка распознавания текста: {e}")
        return None

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        # Получаем файл фото
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Распознаем текст
        receipt_info = recognize_text_with_tesseract(downloaded_file)
        
        if receipt_info and receipt_info['amount']:
            # Форматируем информацию о чеке
            receipt_text = "🧾 *Информация из чека:*\n"
            if receipt_info['amount']:
                receipt_text += f"├─💰 Сумма: *{receipt_info['amount']:,.2f}* ₽\n"
            if receipt_info['date']:
                receipt_text += f"├─📅 Дата: *{receipt_info['date']}*\n"
            if receipt_info['status']:
                receipt_text += f"├─✅ Статус: *{receipt_info['status']}*\n"
            if receipt_info['sender']:
                receipt_text += f"├─👤 Отправитель: *{receipt_info['sender']}*\n"
            if receipt_info['phone']:
                receipt_text += f"└─📱 Телефон: *{receipt_info['phone']}*\n"
            
            # Создаем клавиатуру для подтверждения
            markup = types.InlineKeyboardMarkup()
            confirm_button = types.InlineKeyboardButton("✅ Использовать как платёж", 
                                                      callback_data=f"use_receipt_{receipt_info['amount']}")
            markup.add(confirm_button)
            
            # Отправляем фото с распознанным текстом
            bot.reply_to(message, receipt_text, parse_mode="Markdown", reply_markup=markup)
            
        else:
            bot.reply_to(message, "❌ Не удалось распознать информацию в чеке.")
            
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        bot.reply_to(message, "❌ Произошла ошибка при обработке фото.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('use_receipt_'))
def handle_receipt_confirmation(call):
    try:
        amount = float(call.data.split('_')[2])
        
        # Находим следующий неоплаченный месяц
        params = db["params"]
        next_month = 1
        while str(next_month) in db["payments"] and next_month <= params["term"]:
            next_month += 1
            
        if next_month > params["term"]:
            bot.answer_callback_query(call.id, "❌ Все платежи уже внесены!")
            return
            
        # Создаем временный платеж
        payment_id = f"{call.message.chat.id}_{next_month}"
        if "pending_payments" not in db:
            db["pending_payments"] = {}
            
        db["pending_payments"][payment_id] = {
            "amount": amount,
            "month": next_month,
            "from_user": call.message.chat.id,
            "status": "pending",
            "receipt_photo": call.message.reply_to_message.photo[-1].file_id
        }
        
        # Отправляем на подтверждение
        for approver_id in db.get("payment_approvers", []):
            markup = types.InlineKeyboardMarkup()
            approve_button = types.InlineKeyboardButton("✅ Подтвердить", 
                                                      callback_data=f"approve_{payment_id}")
            reject_button = types.InlineKeyboardButton("❌ Отклонить", 
                                                     callback_data=f"reject_{payment_id}")
            markup.add(approve_button, reject_button)
            
            # Отправляем фото чека
            bot.send_photo(approver_id, 
                         call.message.reply_to_message.photo[-1].file_id,
                         caption=f"📝 Платёж на сумму *{amount:,.2f}* ₽",
                         parse_mode="Markdown",
                         reply_markup=markup)
            
        bot.answer_callback_query(call.id, "✅ Платёж отправлен на подтверждение!")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        
    except Exception as e:
        logger.error(f"Ошибка обработки подтверждения чека: {e}")
        bot.answer_callback_query(call.id, "❌ Произошла ошибка")

if __name__ == "__main__":
    main()