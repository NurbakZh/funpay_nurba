from __future__ import annotations

import json
from logging import getLogger
from typing import TYPE_CHECKING, Dict
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "Steam Storage Plugin"
VERSION = "0.0.1"
DESCRIPTION = "Плагин для хранения и управления данными Steam аккаунтов"
CREDITS = "@nurba_zh"
SETTINGS_PAGE = False
UUID = "f5b3b3b4-0b3b-4b3b-8b3b-0b3b3b3b3b6b"

logger = getLogger("FPC.steam_storage_plugin")

TEMP_STORAGE = {}

ACCOUNT_PAGE = "acc_page"
ACCOUNT_SELECT = "acc_select"
EDIT_ACCOUNT = "edit_acc"

ACCOUNTS_PER_PAGE = 5

def load_accounts(filename='storage/plugins/steam_accounts.json') -> Dict:
    """Загружает данные аккаунтов из JSON файла."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_accounts(accounts: Dict, filename='storage/plugins/steam_accounts.json'):
    """Сохраняет данные аккаунтов в JSON файл."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(accounts, f, ensure_ascii=False, indent=4)

def create_account_keyboard(accounts: Dict, page: int = 0) -> InlineKeyboardMarkup:
    """Создает клавиатуру с аккаунтами для пагинации."""
    keyboard = InlineKeyboardMarkup()
    accounts_list = list(accounts.keys())
    total_pages = (len(accounts_list) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE
    
    start_idx = page * ACCOUNTS_PER_PAGE
    end_idx = start_idx + ACCOUNTS_PER_PAGE
    current_accounts = accounts_list[start_idx:end_idx]
    
    # Add account buttons
    for login in current_accounts:
        keyboard.add(InlineKeyboardButton(
            text=login,
            callback_data=f"{ACCOUNT_SELECT}_{login}"
        ))
    
    # Add navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="⬅️",
            callback_data=f"{ACCOUNT_PAGE}_{page-1}"
        ))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            text="➡️",
            callback_data=f"{ACCOUNT_PAGE}_{page+1}"
        ))
    if nav_buttons:
        keyboard.add(*nav_buttons)
    
    return keyboard

def format_account_info(account_data: Dict) -> str:
    """Форматирует информацию об аккаунте для отображения."""
    return (f"📋 Данные аккаунта:\n\n"
            f"🔑 Логин: {account_data['login']}\n"
            f"🔒 Пароль: {account_data['password']}\n"
            f"📧 Email: {account_data['email']}\n"
            f"🔑 Пароль Email: {account_data['email_password']}\n"
            f"ℹ️ Доп. информация: {account_data['additional_info']}")

def init_commands(cardinal: Cardinal):
    """Инициализирует команды бота."""
    if not cardinal.telegram:
        return

    def handle_add_account(message):
        """Начинает процесс добавления нового аккаунта."""
        chat_id = message.chat.id
        try:
            login = message.text.split()[1]  
            TEMP_STORAGE[chat_id] = {"login": login, "step": "password"}
            cardinal.telegram.bot.send_message(
                chat_id,
                "Введите пароль для аккаунта:"
            )
        except IndexError:
            cardinal.telegram.bot.send_message(
                chat_id,
                "❌ Пожалуйста, укажите логин аккаунта после команды.\nПример: /add_new_steam_account login123"
            )

    def handle_get_accounts(message):
        """Отображает список всех аккаунтов."""
        accounts = load_accounts()
        if not accounts:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                "Список аккаунтов пуст."
            )
            return
        
        keyboard = create_account_keyboard(accounts)
        cardinal.telegram.bot.send_message(
            message.chat.id,
            "Выберите аккаунт:",
            reply_markup=keyboard
        )

    def handle_get_account(message):
        """Получает информацию о конкретном аккаунте."""
        try:
            login = message.text.split()[1]
            accounts = load_accounts()
            if login in accounts:
                cardinal.telegram.bot.send_message(
                    message.chat.id,
                    format_account_info(accounts[login])
                )
            else:
                cardinal.telegram.bot.send_message(
                    message.chat.id,
                    f"❌ Аккаунт {login} не найден."
                )
        except IndexError:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                "❌ Пожалуйста, укажите логин аккаунта после команды.\nПример: /get_account login123"
            )

    def handle_change_account(message):
        """Начинает процесс изменения данных конкретного аккаунта."""
        chat_id = message.chat.id
        try:
            login = message.text.split()[1]
            accounts = load_accounts()
            if login in accounts:
                TEMP_STORAGE[chat_id] = {
                    "login": login,
                    "step": "edit_password",
                    "edit_mode": True
                }
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"Редактирование аккаунта {login}\n"
                    "Введите новый пароль (или '-' чтобы оставить текущий):"
                )
            else:
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"❌ Аккаунт {login} не найден."
                )
        except IndexError:
            cardinal.telegram.bot.send_message(
                chat_id,
                "❌ Пожалуйста, укажите логин аккаунта после команды.\n"
                "Пример: /change_account login123"
            )

    def handle_message(message):
        """Обрабатывает сообщения в процессе добавления/редактирования аккаунта."""
        chat_id = message.chat.id
        if chat_id not in TEMP_STORAGE:
            return

        data = TEMP_STORAGE[chat_id]
        step = data["step"]
        accounts = load_accounts()

        # Handle editing mode
        if data.get("edit_mode"):
            current_account = accounts[data["login"]]
            
            if step == "edit_password":
                data["password"] = message.text if message.text != "-" else current_account["password"]
                data["step"] = "edit_email"
                cardinal.telegram.bot.send_message(
                    chat_id,
                    "Введите новый email (или '-' чтобы оставить текущий):"
                )
            
            elif step == "edit_email":
                data["email"] = message.text if message.text != "-" else current_account["email"]
                data["step"] = "edit_email_password"
                cardinal.telegram.bot.send_message(
                    chat_id,
                    "Введите новый пароль от email (или '-' чтобы оставить текущий):"
                )
            
            elif step == "edit_email_password":
                data["email_password"] = message.text if message.text != "-" else current_account["email_password"]
                data["step"] = "edit_additional_info"
                cardinal.telegram.bot.send_message(
                    chat_id,
                    "Введите новую дополнительную информацию (или '-' чтобы оставить текущую):"
                )
            
            elif step == "edit_additional_info":
                data["additional_info"] = message.text if message.text != "-" else current_account["additional_info"]
                
                # Update account data
                accounts[data["login"]] = {
                    "login": data["login"],
                    "password": data["password"],
                    "email": data["email"],
                    "email_password": data["email_password"],
                    "additional_info": data["additional_info"]
                }
                save_accounts(accounts)
                
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"✅ Данные аккаунта {data['login']} успешно обновлены!\n\n" +
                    format_account_info(accounts[data["login"]])
                )
                del TEMP_STORAGE[chat_id]

        # Handle regular adding mode
        else:
            if step == "password":
                data["password"] = message.text
                data["step"] = "email"
                cardinal.telegram.bot.send_message(chat_id, "Введите email аккаунта:")
            
            elif step == "email":
                data["email"] = message.text
                data["step"] = "email_password"
                cardinal.telegram.bot.send_message(chat_id, "Введите пароль от email:")
            
            elif step == "email_password":
                data["email_password"] = message.text
                data["step"] = "additional_info"
                cardinal.telegram.bot.send_message(chat_id, "Введите дополнительную информацию (или '-' если нет):")
            
            elif step == "additional_info":
                data["additional_info"] = message.text if message.text != "-" else ""
                accounts = load_accounts()
                accounts[data["login"]] = {
                    "login": data["login"],
                    "password": data["password"],
                    "email": data["email"],
                    "email_password": data["email_password"],
                    "additional_info": data["additional_info"]
                }
                save_accounts(accounts)
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"✅ Аккаунт {data['login']} успешно сохранен!"
                )
                del TEMP_STORAGE[chat_id]

    def handle_change_accounts(message):
        """Отображает список всех аккаунтов для редактирования."""
        accounts = load_accounts()
        if not accounts:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                "Список аккаунтов пуст."
            )
            return
        
        keyboard = create_edit_keyboard(accounts)
        cardinal.telegram.bot.send_message(
            message.chat.id,
            "Выберите аккаунт для редактирования:",
            reply_markup=keyboard
        )

    def handle_callback_query(c: CallbackQuery):
        """Обрабатывает нажатия на инлайн кнопки."""
        chat_id = c.message.chat.id
        data = c.data

        if data.startswith(ACCOUNT_PAGE):
            # Handle pagination
            page = int(data.split('_')[-1])
            accounts = load_accounts()
            
            # Check if this is from get_accounts or change_accounts
            is_edit_mode = "редактирования" in c.message.text
            keyboard = create_edit_keyboard(accounts, page) if is_edit_mode else create_account_keyboard(accounts, page)
            
            cardinal.telegram.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=c.message.message_id,
                reply_markup=keyboard
            )
            
        elif data.startswith(ACCOUNT_SELECT):
            # Handle account selection
            login = data.split('_')[-1]
            accounts = load_accounts()
            
            # Check if this is from get_accounts or change_accounts
            is_edit_mode = "редактирования" in c.message.text
            
            if login in accounts:
                if is_edit_mode:
                    # Start edit process
                    TEMP_STORAGE[chat_id] = {
                        "login": login,
                        "step": "edit_password",
                        "edit_mode": True
                    }
                    cardinal.telegram.bot.send_message(
                        chat_id,
                        f"Редактирование аккаунта {login}\n"
                        "Введите новый пароль (или '-' чтобы оставить текущий):"
                    )
                else:
                    # Just show account info
                    cardinal.telegram.bot.send_message(
                        chat_id,
                        format_account_info(accounts[login])
                    )
            else:
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"❌ Аккаунт {login} не найден."
                )
        
        # Answer the callback query to remove loading state
        cardinal.telegram.bot.answer_callback_query(c.id)

    def create_edit_keyboard(accounts: Dict, page: int = 0) -> InlineKeyboardMarkup:
        """Создает клавиатуру с аккаунтами для редактирования."""
        keyboard = InlineKeyboardMarkup()
        accounts_list = list(accounts.keys())
        total_pages = (len(accounts_list) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE
        
        start_idx = page * ACCOUNTS_PER_PAGE
        end_idx = start_idx + ACCOUNTS_PER_PAGE
        current_accounts = accounts_list[start_idx:end_idx]
        
        # Add account buttons with edit callback data
        for login in current_accounts:
            keyboard.add(InlineKeyboardButton(
                text=f"✏️ {login}",
                callback_data=f"{ACCOUNT_SELECT}_{login}"
            ))
        
        # Add navigation buttons
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=f"{ACCOUNT_PAGE}_{page-1}"
            ))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(
                text="➡️",
                callback_data=f"{ACCOUNT_PAGE}_{page+1}"
            ))
        if nav_buttons:
            keyboard.add(*nav_buttons)
        
        return keyboard

    # Register commands and handlers
    cardinal.add_telegram_commands(UUID, [
        ("add_new_steam_account", "добавить новый Steam аккаунт", True),
        ("get_accounts", "показать список аккаунтов", True),
        ("get_account", "получить информацию о конкретном аккаунте", True),
        ("change_accounts", "изменить данные аккаунтов", True),
        ("change_account", "изменить данные конкретного аккаунта", True),
    ])

    # Register handlers
    cardinal.telegram.msg_handler(handle_add_account, commands=["add_new_steam_account"])
    cardinal.telegram.msg_handler(handle_get_accounts, commands=["get_accounts"])
    cardinal.telegram.msg_handler(handle_get_account, commands=["get_account"])
    cardinal.telegram.msg_handler(handle_change_account, commands=["change_account"])
    cardinal.telegram.msg_handler(handle_change_accounts, commands=["change_accounts"])
    cardinal.telegram.msg_handler(handle_message)
    
    # Fix: Add lambda function for callback query handler
    cardinal.telegram.cbq_handler(handle_callback_query, lambda c: c.data.startswith((ACCOUNT_PAGE, ACCOUNT_SELECT)))

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None