from __future__ import annotations

import json
import requests
import os
from bs4 import BeautifulSoup
import schedule
import time
import threading
import pytz
from FunPayAPI.account import Account
from logging import getLogger
from telebot.types import Message, InlineKeyboardMarkup as K, InlineKeyboardButton as B
from tg_bot import static_keyboards as skb
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "Steam Accounts Plugin"
VERSION = "0.0.1"
DESCRIPTION = "Данный плагин позволяет управлять системой аренды аккаунтов на Steam"
CREDITS = "@nurba_zh"
SETTINGS_PAGE = False
UUID = "f5b3b3b4-0b3b-4b3b-8b3b-0b3b3b3b3b5b"

logger = getLogger("FPC.steam_accounts_plugin")
RUNNING = False

class Account:
    def __init__(self, login: str, password: str, code_mail: str, email_login: str = None, email_password: str = None, is_rented: bool = False, time_of_rent: Optional[datetime] = None):
        self.login = login
        self.password = password
        self.code_mail = code_mail
        self.email_login = email_login
        self.email_password = email_password
        self.is_rented = is_rented
        self.time_of_rent = time_of_rent

    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "password": self.password,
            "codeMail": self.code_mail,
            "emailLogin": self.email_login,
            "emailPassword": self.email_password,
            "isRented": self.is_rented,
            "timeOfRent": self.time_of_rent.isoformat() if self.time_of_rent else None
        }

    @staticmethod
    def from_dict(data: dict) -> 'Account':
        account = Account(
            login=data["login"],
            password=data["password"],
            code_mail=data["codeMail"],
            email_login=data.get("emailLogin"),
            email_password=data.get("emailPassword"),
            is_rented=data["isRented"],
            time_of_rent=datetime.fromisoformat(data["timeOfRent"]) if data["timeOfRent"] else None
        )
        return account

class Game:
    def __init__(self, name: str, prices: Dict[str, float], accounts: List[Account] = None):
        self.name = name
        self.prices = prices
        self.accounts = accounts or []

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "prices": self.prices,
            "accounts": [account.to_dict() for account in self.accounts]
        }

    @staticmethod
    def from_dict(data: dict) -> 'Game':
        accounts = [Account.from_dict(acc) for acc in data["accounts"]]
        return Game(
            name=data["name"],
            prices=data["prices"],
            accounts=accounts
        )

def save_games(games: List[Game]):
    storage_dir = os.path.join(os.path.dirname(__file__), '../storage/plugins')
    os.makedirs(storage_dir, exist_ok=True)
    file_path = os.path.join(storage_dir, 'accounts.json')
    
    data = [game.to_dict() for game in games]
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_games() -> List[Game]:
    storage_dir = os.path.join(os.path.dirname(__file__), '../storage/plugins')
    file_path = os.path.join(storage_dir, 'accounts.json')
    
    if not os.path.exists(file_path):
        return []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return [Game.from_dict(game_data) for game_data in data]

def format_game_info(game: Game) -> str:
    duration_names = {
        "1h": "1 час",
        "3h": "3 часа",
        "6h": "6 часов",
        "1d": "1 день",
        "3d": "3 дня",
        "5d": "5 дней",
        "7d": "7 дней"
    }
    
    info_msg = f"🎮 *{game.name}*\n\n"
    info_msg += "💰 *Цены:*\n"
    for duration, price in game.prices.items():
        readable_duration = duration_names.get(duration, duration)
        info_msg += f"• *{readable_duration}*: {price} руб.\n"
    info_msg += f"\n📊 *Аккаунтов в базе:* {len(game.accounts)}\n"
    info_msg += f"🔄 *Арендовано:* {sum(1 for acc in game.accounts if acc.is_rented)}\n"
    info_msg += f"✅ *Доступно:* {sum(1 for acc in game.accounts if not acc.is_rented)}\n"
    
    if game.accounts:
        info_msg += "\n📝 *Список аккаунтов:*\n"
        for i, acc in enumerate(game.accounts, 1):
            status = "🔒 Арендован" if acc.is_rented else "✅ Доступен"
            info_msg += f"{i}. Логин: {acc.login} - {status}\n"
    
    return info_msg

def init_commands(cardinal: Cardinal):
    if not cardinal.telegram:
        return
    tg = cardinal.telegram
    bot = cardinal.telegram.bot

    def handle_add_account(message: Message):
        msg = bot.send_message(message.chat.id, "Введите логин аккаунта Steam:")
        bot.register_next_step_handler(msg, process_login_step)

    def process_login_step(message: Message):
        login = message.text
        msg = bot.send_message(message.chat.id, "Введите пароль аккаунта Steam:")
        bot.register_next_step_handler(msg, process_password_step, login)

    def process_password_step(message: Message, login: str):
        password = message.text
        msg = bot.send_message(message.chat.id, "Введите почту, с которой будут приходить коды авторизации:")
        bot.register_next_step_handler(msg, process_email_step, login, password)

    def process_email_step(message: Message, login: str, password: str):
        email = message.text
        msg = bot.send_message(message.chat.id, "Введите логин от почты:")
        bot.register_next_step_handler(msg, process_email_login_step, login, password, email)

    def process_email_login_step(message: Message, login: str, password: str, email: str):
        email_login = message.text
        msg = bot.send_message(message.chat.id, "Введите пароль от почты:")
        bot.register_next_step_handler(msg, process_email_password_step, login, password, email, email_login)

    def process_email_password_step(message: Message, login: str, password: str, email: str, email_login: str):
        email_password = message.text
        msg = bot.send_message(message.chat.id, "Введите название игры в FunPay:")
        bot.register_next_step_handler(msg, process_game_step, login, password, email, email_login, email_password)

    def process_game_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str):
        game_name = message.text
        msg = bot.send_message(message.chat.id, "Введите цену аренды за 1 час (в рублях):")
        bot.register_next_step_handler(msg, process_price_1h_step, login, password, email, email_login, email_password, game_name)

    def process_price_1h_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str):
        try:
            price_1h = float(message.text)
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 3 часа (в рублях):")
            bot.register_next_step_handler(msg, process_price_3h_step, login, password, email, email_login, email_password, game_name, price_1h)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат цены. Пожалуйста, введите число.")
            process_game_step(message, login, password, email, email_login, email_password)

    def process_price_3h_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str, price_1h: float):
        try:
            price_3h = float(message.text)
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 6 часов (в рублях):")
            bot.register_next_step_handler(msg, process_price_6h_step, login, password, email, email_login, email_password, game_name, price_1h, price_3h)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат цены. Пожалуйста, введите число.")
            process_price_1h_step(message, login, password, email, email_login, email_password, game_name)

    def process_price_6h_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str, price_1h: float, price_3h: float):
        try:
            price_6h = float(message.text)
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 1 день (в рублях):")
            bot.register_next_step_handler(
                msg, 
                lambda m: process_price_1d_step(m, login, password, email, email_login, email_password, game_name, {
                    "1h": price_1h,
                    "3h": price_3h,
                    "6h": price_6h
                })
            )
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат цены. Пожалуйста, введите число.")
            process_price_3h_step(message, login, password, email, email_login, email_password, game_name, price_1h)

    def process_price_1d_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str, prices: dict):
        try:
            prices["1d"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 3 дня (в рублях):")
            bot.register_next_step_handler(msg, lambda m: process_price_3d_step(m, login, password, email, email_login, email_password, game_name, prices))
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат цены. Пожалуйста, введите число.")
            process_price_6h_step(message, login, password, email, email_login, email_password, game_name, prices["1h"], prices["3h"])

    def process_price_3d_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str, prices: dict):
        try:
            prices["3d"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 5 дней (в рублях):")
            bot.register_next_step_handler(msg, lambda m: process_price_5d_step(m, login, password, email, email_login, email_password, game_name, prices))
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат цены. Пожалуйста, введите число.")
            process_price_1d_step(message, login, password, email, email_login, email_password, game_name, prices)

    def process_price_5d_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str, prices: dict):
        try:
            prices["5d"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 7 дней (в рублях):")
            bot.register_next_step_handler(msg, lambda m: process_price_7d_step(m, login, password, email, email_login, email_password, game_name, prices))
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат цены. Пожалуйста, введите число.")
            process_price_3d_step(message, login, password, email, email_login, email_password, game_name, prices)

    def process_price_7d_step(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str, prices: dict):
        try:
            prices["7d"] = float(message.text)
            process_remaining_prices(message, login, password, email, email_login, email_password, game_name, prices)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат цены. Пожалуйста, введите число.")
            process_price_5d_step(message, login, password, email, email_login, email_password, game_name, prices)

    def process_remaining_prices(message: Message, login: str, password: str, email: str, email_login: str, email_password: str, game_name: str, prices: dict):
        games = load_games()
        account = Account(login, password, email, email_login, email_password)
        
        # Find existing game or create new one
        game = next((g for g in games if g.name == game_name), None)
        if game:
            game.accounts.append(account)
        else:
            game = Game(game_name, prices, [account])
            games.append(game)
        
        save_games(games)
        bot.send_message(message.chat.id, f"Аккаунт успешно добавлен для игры {game_name}")

    def handle_add_account_to_game(message: Message):
        games = load_games()
        if not games:
            bot.send_message(message.chat.id, "В базе данных нет игр. Сначала добавьте игру через /add_account")
            return

        keyboard = K()
        for game in games:
            keyboard.add(B(game.name, callback_data=f"select_game:{game.name}"))
        
        bot.send_message(message.chat.id, "Выберите игру:", reply_markup=keyboard)

    def handle_get_info_about_game(message: Message):
        games = load_games()
        if not games:
            bot.send_message(message.chat.id, "В базе данных нет игр.")
            return

        keyboard = K()
        for game in games:
            keyboard.add(B(game.name, callback_data=f"info_game:{game.name}"))
        
        bot.send_message(message.chat.id, "Выберите игру для просмотра информации:", reply_markup=keyboard)

    def handle_delete_account_from_game(message: Message):
        games = load_games()
        if not games:
            bot.send_message(message.chat.id, "В базе данных нет игр.")
            return

        keyboard = K()
        for game in games:
            keyboard.add(B(game.name, callback_data=f"delete_from_game:{game.name}"))
        
        bot.send_message(message.chat.id, "Выберите игру:", reply_markup=keyboard)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("select_game:"))
    def handle_message(call):
        game_name = call.data.split(":")[1].strip()
        
        # Get game info from database
        games = load_games()
        game = next((g for g in games if g.name == game_name), None)
        
        if game:
            # Format game info message
            info_msg = format_game_info(game)
            bot.send_message(call.message.chat.id, info_msg, parse_mode="Markdown")
        
        msg = bot.send_message(call.message.chat.id, "Введите логин аккаунта Steam:")
        bot.register_next_step_handler(msg, lambda m: process_login_for_existing_game_step(m, game_name))

    def process_login_for_existing_game_step(message: Message, game_name: str):
        login = message.text
        msg = bot.send_message(message.chat.id, "Введите пароль аккаунта Steam:")
        bot.register_next_step_handler(msg, lambda m: process_password_for_existing_game_step(m, game_name, login))

    def process_password_for_existing_game_step(message: Message, game_name: str, login: str):
        password = message.text
        msg = bot.send_message(message.chat.id, "Введите почту, с которой будут приходить коды авторизации:")
        bot.register_next_step_handler(msg, lambda m: add_account_to_existing_game(m, game_name, login, password))

    def add_account_to_existing_game(message: Message, game_name: str, login: str, password: str):
        email = message.text
        games = load_games()
        game = next((g for g in games if g.name == game_name), None)
        
        if game:
            account = Account(login, password, email)
            game.accounts.append(account)
            save_games(games)
            bot.send_message(message.chat.id, f"Аккаунт успешно добавлен к игре {game_name}")
        else:
            bot.send_message(message.chat.id, f"Игра {game_name} не найдена в базе данных")

    @bot.callback_query_handler(func=lambda call: call.data.startswith(("info_game:", "delete_from_game:")))
    def handle_game_callbacks(call):
        action, game_name = call.data.split(":")
        games = load_games()
        game = next((g for g in games if g.name == game_name), None)
        
        if not game:
            bot.answer_callback_query(call.id, "Игра не найдена")
            return

        if action == "info_game":
            info_msg = format_game_info(game)
            bot.send_message(call.message.chat.id, info_msg, parse_mode="Markdown")
        
        elif action == "delete_from_game":
            if not game.accounts:
                bot.send_message(call.message.chat.id, "В этой игре нет аккаунтов.")
                return
                
            keyboard = K()
            for acc in game.accounts:
                status = "🔒" if acc.is_rented else "✅"
                keyboard.add(B(f"{status} {acc.login}", callback_data=f"delete_account:{game_name}:{acc.login}"))
            
            bot.send_message(call.message.chat.id, "Выберите аккаунт для удаления:", reply_markup=keyboard)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("delete_account:"))
    def handle_delete_account(call):
        _, game_name, login = call.data.split(":")
        games = load_games()
        game = next((g for g in games if g.name == game_name), None)
        
        if game:
            game.accounts = [acc for acc in game.accounts if acc.login != login]
            save_games(games)
            bot.send_message(call.message.chat.id, f"Аккаунт {login} успешно удален из игры {game_name}")
        else:
            bot.send_message(call.message.chat.id, "Игра не найдена")

    cardinal.add_telegram_commands(UUID, [
        ("add_account", "добавить новый аккаунт и игру", True),
        ("add_account_to_game", "добавить аккаунт к существующей игре", True),
        ("get_info_about_game", "получить информацию об игре", True),
        ("delete_account_from_game", "удалить аккаунт из игры", True),
    ])

    tg.msg_handler(handle_add_account, commands=["add_account"])
    tg.msg_handler(handle_add_account_to_game, commands=["add_account_to_game"])
    tg.msg_handler(handle_get_info_about_game, commands=["get_info_about_game"])
    tg.msg_handler(handle_delete_account_from_game, commands=["delete_account_from_game"])

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
