from __future__ import annotations

import json
from logging import getLogger
from typing import TYPE_CHECKING, Dict
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "Xbox Storage Plugin"
VERSION = "0.0.1"
DESCRIPTION = "Плагин для хранения и управления данными Xbox аккаунтов"
CREDITS = "@nurba_zh"
SETTINGS_PAGE = False
UUID = "f5b3b3b4-0b3b-4b3b-8b3b-0b3b3b3b5b9b"

logger = getLogger("FPC.xbox_storage_plugin")

TEMP_STORAGE = {}

ACCOUNT_PAGE = "xbox_acc_page"
ACCOUNT_SELECT = "xbox_acc_select"
EDIT_ACCOUNT = "xbox_edit_acc"
ACCOUNT_VIEW = "xbox_acc_view"

ACCOUNTS_PER_PAGE = 5

def load_accounts(filename='storage/plugins/xbox_accounts.json') -> Dict:
    """Загружает данные аккаунтов из JSON файла."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_accounts(accounts: Dict, filename='storage/plugins/xbox_accounts.json'):
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
    
    # Add account buttons with view callback data
    for login in current_accounts:
        keyboard.add(InlineKeyboardButton(
            text=login,
            callback_data=f"{ACCOUNT_VIEW}_{login}"
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
    return (f"📋 Данные аккаунта Xbox:\n\n"
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
        msg = cardinal.telegram.bot.send_message(
            message.chat.id,
            "📧 Введите логин аккаунта Xbox:"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_login_step)

    def process_login_step(message):
        """Обрабатывает ввод логина."""
        login = message.text
        TEMP_STORAGE[message.chat.id] = {"login": login}
        msg = cardinal.telegram.bot.send_message(
            message.chat.id,
            "🔑 Введите пароль аккаунта:"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_password_step)

    def process_password_step(message):
        """Обрабатывает ввод пароля."""
        chat_id = message.chat.id
        TEMP_STORAGE[chat_id]["password"] = message.text
        msg = cardinal.telegram.bot.send_message(
            chat_id,
            "📧 Введите email аккаунта:"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_email_step)

    def process_email_step(message):
        """Обрабатывает ввод email."""
        chat_id = message.chat.id
        TEMP_STORAGE[chat_id]["email"] = message.text
        msg = cardinal.telegram.bot.send_message(
            chat_id,
            "🔑 Введите пароль от email:"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_email_password_step)

    def process_email_password_step(message):
        """Обрабатывает ввод пароля от email."""
        chat_id = message.chat.id
        TEMP_STORAGE[chat_id]["email_password"] = message.text
        msg = cardinal.telegram.bot.send_message(
            chat_id,
            "📝 Введите дополнительную информацию (или '-' если нет):"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_additional_info_step)

    def process_additional_info_step(message):
        """Обрабатывает ввод дополнительной информации."""
        chat_id = message.chat.id
        data = TEMP_STORAGE[chat_id]
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
        
        # Update password in xbox_accounts.json if account exists there
        try:
            with open('storage/plugins/xbox_accounts.json', 'r', encoding='utf-8') as f:
                games_data = json.load(f)
                
            for game in games_data:
                for account in game.get('accounts'):
                    if account.get('login') == data['login']:
                        account['password'] = data['password']
                    
            with open('storage/plugins/xbox_accounts.json', 'w', encoding='utf-8') as f:
                json.dump(games_data, f, ensure_ascii=False, indent=4)
        except FileNotFoundError:
            logger.warning("xbox_accounts.json not found, skipping password update in games")
        except Exception as e:
            logger.error(f"Error updating password in xbox_accounts.json: {e}")
        
        cardinal.telegram.bot.send_message(
            chat_id,
            f"✅ Аккаунт {data['login']} успешно сохранен!"
        )
        del TEMP_STORAGE[chat_id]

    def handle_change_account(message):
        """Начинает процесс изменения данных конкретного аккаунта."""
        try:
            login = message.text.split(maxsplit=1)[1]
            accounts = load_accounts()
            if login in accounts:
                TEMP_STORAGE[message.chat.id] = {"login": login}
                msg = cardinal.telegram.bot.send_message(
                    message.chat.id,
                    "Введите новый пароль (или '-' чтобы оставить текущий):"
                )
                cardinal.telegram.bot.register_next_step_handler(msg, process_edit_password_step)
            else:
                cardinal.telegram.bot.send_message(
                    message.chat.id,
                    f"❌ Аккаунт {login} не найден."
                )
        except IndexError:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                "❌ Пожалуйста, укажите логин аккаунта после команды.\n"
                "Пример: /change_xbox_account login123"
            )

    def process_edit_password_step(message):
        """Обрабатывает изменение пароля."""
        chat_id = message.chat.id
        accounts = load_accounts()
        current_account = accounts[TEMP_STORAGE[chat_id]["login"]]
        TEMP_STORAGE[chat_id]["password"] = message.text if message.text != "-" else current_account["password"]
        
        msg = cardinal.telegram.bot.send_message(
            chat_id,
            "Введите новый email (или '-' чтобы оставить текущий):"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_edit_email_step)

    def process_edit_email_step(message):
        """Обрабатывает изменение email."""
        chat_id = message.chat.id
        accounts = load_accounts()
        current_account = accounts[TEMP_STORAGE[chat_id]["login"]]
        TEMP_STORAGE[chat_id]["email"] = message.text if message.text != "-" else current_account["email"]
        
        msg = cardinal.telegram.bot.send_message(
            chat_id,
            "Введите новый пароль от email (или '-' чтобы оставить текущий):"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_edit_email_password_step)

    def process_edit_email_password_step(message):
        """Обрабатывает изменение пароля от email."""
        chat_id = message.chat.id
        accounts = load_accounts()
        current_account = accounts[TEMP_STORAGE[chat_id]["login"]]
        TEMP_STORAGE[chat_id]["email_password"] = message.text if message.text != "-" else current_account["email_password"]
        
        msg = cardinal.telegram.bot.send_message(
            chat_id,
            "Введите новую дополнительную информацию (или '-' чтобы оставить текущую):"
        )
        cardinal.telegram.bot.register_next_step_handler(msg, process_edit_additional_info_step)

    def process_edit_additional_info_step(message):
        """Обрабатывает изменение дополнительной информации."""
        chat_id = message.chat.id
        data = TEMP_STORAGE[chat_id]
        accounts = load_accounts()
        current_account = accounts[data["login"]]
        
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
        
        # Update password in xbox_accounts.json if account exists there
        try:
            with open('storage/plugins/xbox_accounts.json', 'r', encoding='utf-8') as f:
                games_data = json.load(f)
                
            for game in games_data:
                for account in game.get('accounts'):
                    if account.get('login') == data['login']:
                        account['password'] = data['password']
                    
            with open('storage/plugins/xbox_accounts.json', 'w', encoding='utf-8') as f:
                json.dump(games_data, f, ensure_ascii=False, indent=4)
        except FileNotFoundError:
            logger.warning("xbox_accounts.json not found, skipping password update in games")
        except Exception as e:
            logger.error(f"Error updating password in xbox_accounts.json: {e}")
        
        cardinal.telegram.bot.send_message(
            chat_id,
            f"✅ Данные аккаунта {data['login']} успешно обновлены!\n\n" +
            format_account_info(accounts[data["login"]])
        )
        del TEMP_STORAGE[chat_id]

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
            login = message.text.split(maxsplit=1)[1]
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
                "❌ Пожалуйста, укажите логин аккаунта после команды.\nПример: /get_xbox_account login123"
            )

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
            keyboard = create_account_keyboard(accounts, page)
            
            cardinal.telegram.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=c.message.message_id,
                reply_markup=keyboard
            )
            
        elif data.startswith(ACCOUNT_VIEW):
            # Handle account viewing
            login = '_'.join(data.split('_')[2:])  # Get everything after ACCOUNT_VIEW_
            accounts = load_accounts()
            
            if login in accounts:
                cardinal.telegram.bot.send_message(
                    chat_id,
                    format_account_info(accounts[login])
                )
            else:
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"❌ Аккаунт {login} не найден."
                )
        
        elif data.startswith(ACCOUNT_SELECT):
            # Handle account selection
            login = '_'.join(data.split('_')[2:])  # Get everything after ACCOUNT_SELECT_
            accounts = load_accounts()
            
            if login in accounts:
                TEMP_STORAGE[chat_id] = {"login": login}
                msg = cardinal.telegram.bot.send_message(
                    chat_id,
                    "Введите новый пароль (или '-' чтобы оставить текущий):"
                )
                cardinal.telegram.bot.register_next_step_handler(msg, process_edit_password_step)
            else:
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"❌ Аккаунт {login} не найден."
                )
        
        elif data.startswith("delete_xbox_acc_"):
            # Handle account deletion
            login = '_'.join(data.split('_')[2:])  # Get everything after delete_xbox_acc_
            accounts = load_accounts()
            
            if login in accounts:
                del accounts[login]
                save_accounts(accounts)
                
                # Update keyboard if there are still accounts
                if accounts:
                    keyboard = create_delete_keyboard(accounts)
                    cardinal.telegram.bot.edit_message_reply_markup(
                        chat_id=chat_id,
                        message_id=c.message.message_id,
                        reply_markup=keyboard
                    )
                else:
                    cardinal.telegram.bot.edit_message_text(
                        "Список аккаунтов пуст.",
                        chat_id=chat_id,
                        message_id=c.message.message_id
                    )
                
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"✅ Аккаунт {login} успешно удален."
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

    def handle_delete_account(message):
        """Удаляет конкретный аккаунт по логину."""
        try:
            login = message.text.split(maxsplit=1)[1]
            accounts = load_accounts()
            if login in accounts:
                del accounts[login]
                save_accounts(accounts)
                cardinal.telegram.bot.send_message(
                    message.chat.id,
                    f"✅ Аккаунт {login} успешно удален."
                )
            else:
                cardinal.telegram.bot.send_message(
                    message.chat.id,
                    f"❌ Аккаунт {login} не найден."
                )
        except IndexError:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                "❌ Пожалуйста, укажите логин аккаунта после команды.\n"
                "Пример: /delete_xbox_account login123"
            )

    def handle_delete_accounts(message):
        """Отображает список всех аккаунтов для удаления."""
        accounts = load_accounts()
        if not accounts:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                "Список аккаунтов пуст."
            )
            return
        
        keyboard = create_delete_keyboard(accounts)
        cardinal.telegram.bot.send_message(
            message.chat.id,
            "Выберите аккаунт для удаления:",
            reply_markup=keyboard
        )

    def create_delete_keyboard(accounts: Dict, page: int = 0) -> InlineKeyboardMarkup:
        """Создает клавиатуру с аккаунтами для удаления."""
        keyboard = InlineKeyboardMarkup()
        accounts_list = list(accounts.keys())
        total_pages = (len(accounts_list) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE
        
        start_idx = page * ACCOUNTS_PER_PAGE
        end_idx = start_idx + ACCOUNTS_PER_PAGE
        current_accounts = accounts_list[start_idx:end_idx]
        
        # Add account buttons with delete callback data
        for login in current_accounts:
            keyboard.add(InlineKeyboardButton(
                text=f"🗑️ {login}",
                callback_data=f"delete_xbox_acc_{login}"
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
        ("add_new_xbox_account", "добавить новый Xbox аккаунт", True),
        ("get_xbox_accounts", "показать список аккаунтов", True),
        ("get_xbox_account", "получить информацию о конкретном аккаунте", True),
        ("change_xbox_accounts", "изменить данные аккаунтов", True),
        ("change_xbox_account", "изменить данные конкретного аккаунта", True),
        ("delete_xbox_account", "удалить конкретный аккаунт", True),
        ("delete_xbox_accounts", "показать список аккаунтов для удаления", True),
    ])

    # Register handlers
    cardinal.telegram.msg_handler(handle_add_account, commands=["add_new_xbox_account"])
    cardinal.telegram.msg_handler(handle_get_accounts, commands=["get_xbox_accounts"])
    cardinal.telegram.msg_handler(handle_get_account, commands=["get_xbox_account"])
    cardinal.telegram.msg_handler(handle_change_account, commands=["change_xbox_account"])
    cardinal.telegram.msg_handler(handle_change_accounts, commands=["change_xbox_accounts"])
    cardinal.telegram.msg_handler(handle_delete_account, commands=["delete_xbox_account"])
    cardinal.telegram.msg_handler(handle_delete_accounts, commands=["delete_xbox_accounts"])
    cardinal.telegram.cbq_handler(handle_callback_query, lambda c: c.data.startswith((ACCOUNT_PAGE, ACCOUNT_SELECT, ACCOUNT_VIEW, "delete_xbox_acc_")))

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
