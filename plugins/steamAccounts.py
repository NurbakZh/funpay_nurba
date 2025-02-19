from __future__ import annotations

import json
import requests
import os
from bs4 import BeautifulSoup
import schedule
import time
import threading
import pytz
import FunPayAPI.types
from FunPayAPI.account import Account
from logging import getLogger
from telebot.types import Message, InlineKeyboardMarkup as K, InlineKeyboardButton as B
from tg_bot import static_keyboards as skb
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional
from parser_helper import get_account_game_link
from plugins.parser import generate_random_id, translate_text

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
    def __init__(self, login: str, password: str, email_login: str = None, email_password: str = None, is_rented: bool = False, time_of_rent: Optional[datetime] = None):
        self.login = login
        self.password = password
        self.email_login = email_login
        self.email_password = email_password
        self.is_rented = is_rented
        self.time_of_rent = time_of_rent

    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "password": self.password,
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
            email_login=data.get("emailLogin"),
            email_password=data.get("emailPassword"),
            is_rented=data["isRented"],
            time_of_rent=datetime.fromisoformat(data["timeOfRent"]) if data["timeOfRent"] else None
        )
        return account

class Game:
    def __init__(self, name: str, lot_name: str, prices: Dict[str, Dict[str, str]], accounts: List[Account] = None, edition_name: str = ""):
        self.name = name
        self.lot_name = lot_name
        self.prices = prices  # Now expects Dict[str, Dict[str, str]]
        self.accounts = accounts or []
        self.edition_name = edition_name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "lot_name": self.lot_name,
            "prices": self.prices,
            "accounts": [account.to_dict() for account in self.accounts],
            "edition_name": self.edition_name
        }

    @staticmethod
    def from_dict(data: dict) -> 'Game':
        accounts = [Account.from_dict(acc) for acc in data["accounts"]]
        return Game(
            name=data["name"],
            lot_name=data["lot_name"],
            prices=data["prices"],
            accounts=accounts,
            edition_name=data["edition_name"]
        )

def save_games(games: List[Game]):
    storage_dir = os.path.join(os.path.dirname(__file__), '../storage/plugins')
    os.makedirs(storage_dir, exist_ok=True)
    file_path = os.path.join(storage_dir, 'accounts.json')
    
    data = [game.to_dict() for game in games]
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def edit_game(lot_name: str, updated_data: dict):
    """
    Edit a game's data in the storage.
    
    The updated_data dict can contain any of these fields:
    - name: str - The display name of the game
    - lot_name: str - The name used in lots/listings
    - edition_name: str - The edition name of the game
    - prices: Dict[str, Dict[str, Union[float, str]]] - Price data in format:
        {
            "duration": {
                "price": float,  # The price amount
                "url": str      # The lot URL
            }
        }
        Example:
        {
            "1h": {
                "price": 1000.0,
                "url": "https://funpay.com/lots/123"
            }
        }
    
    :param lot_name: The lot_name of the game to edit
    :param updated_data: Dictionary containing the fields to update
    """
    games = load_games()
    for game in games:
        if game.lot_name == lot_name:
            # Update game attributes from the provided data
            for key, value in updated_data.items():
                if hasattr(game, key):
                    # For prices, validate the structure
                    if key == "prices":
                        # Ensure each duration has price and url
                        for duration, price_data in value.items():
                            if not isinstance(price_data, dict):
                                continue
                            if "price" not in price_data:
                                price_data["price"] = 0.0
                            if "url" not in price_data:
                                price_data["url"] = ""
                    setattr(game, key, value)
            break
    save_games(games)

def load_games() -> List[Game]:
    storage_dir = os.path.join(os.path.dirname(__file__), '../storage/plugins')
    file_path = os.path.join(storage_dir, 'accounts.json')
    
    if not os.path.exists(file_path):
        return []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return [Game.from_dict(game_data) for game_data in data]

duration_names = {
    "1h": "1 час",
    "3h": "3 часа",
    "6h": "6 часов",
    "1d": "1 день",
    "3d": "3 дня",
    "5d": "5 дней",
    "7d": "7 дней"
}

def format_game_info(game: Game) -> str:
    info_msg = f"🎮 *{game.lot_name}* - *{game.name}*\n\n"
    info_msg += "💰 *Цены:*\n"
    for duration, price_data in game.prices.items():
        readable_duration = duration_names.get(duration, duration)
        info_msg += f"• *{readable_duration}*: {price_data['price']} руб."
        if price_data.get('url'):
            info_msg += f" ([Ссылка на лот]({price_data['url']}))"
        info_msg += "\n"
    info_msg += f"\n📊 *Аккаунтов в базе:* {len(game.accounts)}\n"
    info_msg += f"🔄 *Арендовано:* {sum(1 for acc in game.accounts if acc.is_rented)}\n"
    info_msg += f"✅ *Доступно:* {sum(1 for acc in game.accounts if not acc.is_rented)}\n"
    
    if game.accounts:
        info_msg += "\n📝 *Список аккаунтов:*\n"
        for i, acc in enumerate(game.accounts, 1):
            status = "🔒 Арендован" if acc.is_rented else "✅ Доступен"
            info_msg += f"{i}. Логин: {acc.login} - {status}\n"
    
    return info_msg

def generate_summary_text(game_name: str, duration: str, ru: bool) -> str:
    if ru:
        return f"🖤❤️【{game_name}】❤️🖤【STEAM】🖤❤️【Аренда на {duration} (онлайн)】❤️🖤【Авто-выдача】🖤❤️"
    else:
        return f"🖤❤️【{game_name}】❤️🖤【STEAM】🖤❤️【Rent for {duration} (online)】❤️🖤【Auto-delivery】🖤❤️"

def generate_description_text(game_name: str) -> str:
    return f"""❗️Стоит АВТО-ВЫДАЧА❗️
❗️Если объявление активно, то товар АКТУАЛЕН❗️
❗️После оплаты бот в течение 30 секунд автоматически вышлет все данные для входа в аккаунт с игрой на указанный срок. Не нужно спрашивать, на месте ли продавец или свободен ли аккаунт❗️
❗️Если хотите узнать определённое количество свободных аккаунтов по конкретной игре, напишите в чат продавцу команду !arenda {game_name}❗️

🔽❗️❓Как проходит аренда?❓❗️🔽
1️⃣ Произвести оплату лота с нужным вам временем.
2️⃣ Получить данные от аккаунта.
3️⃣ По завершении аренды подтвердить заказ и оставить отзыв (по желанию).

🔥❗️Вы получаете личный онлайн-аккаунт с доступом ко всем сетевым функциям, а не общий или оффлайн-аккаунт! На весь срок аренды вы будете единственным владельцем❗️🔥
🔥❗️После оплаты лота сразу начинается отсчёт❗️🔥
🔥❗️Если игра имеет сторонние привязки (Ubisoft, EA и т.д.), вы получите и данные от них. После покупки бот вышлет команду с кодом для входа вместе со всеми данными❗️🔥

❗️Что запрещено делать на аккаунте?❗️
⭕️ Менять данные (кроме имени и аватарки). Нарушение этого правила приведёт к блокировке аккаунта без возврата денег.
⭕️ Использовать читы или стороннее ПО – мгновенное прекращение услуги.
⭕️ Передавать данные третьим лицам – строго запрещено.

✅ Соблюдайте правила и играйте без лишних проблем! 🎮🔥"""

def init_commands(cardinal: Cardinal):
    if not cardinal.telegram:
        return
    tg = cardinal.telegram
    bot = cardinal.telegram.bot

    def create_lot(message: Message, game: Game):
        try:
            node_id = get_account_game_link(game.lot_name)
            lot_fields = cardinal.account.get_lots_variants(node_id, edition_name=game.edition_name)
            game_options = lot_fields["game_options"]
            platform_options = lot_fields["platform_options"]
            type_of_lot = lot_fields["type_of_lot"]
            
            suitable_game_option = {'value': '', 'text': ''}
            suitable_platform_option = {'value': '', 'text': ''}

            if game_options is not None:
                suitable_game_option = next((option for option in game_options if game.name in option["text"]), None)
                if suitable_game_option is None:
                    suitable_game_option = next((option for option in game_options if option["text"] in ["Steam", "PC", "PC (Steam)", "(PC) Steam"]), None)

            if platform_options is not None:
                suitable_platform_option = next((option for option in platform_options if option["text"] in ["Steam", "PC", "PC (Steam)", "(PC) Steam"]), None)
                if not suitable_platform_option:
                    raise Exception(f"No suitable platform option found for 'Steam' or 'PC'")

            for duration, price_data in game.prices.items():
                readable_duration = duration_names.get(duration, duration)
                summary = generate_summary_text(game.name, readable_duration, ru=True)
                summary_en = generate_summary_text(game.name, translate_text(readable_duration, "en"), ru=False)
                description = generate_description_text(game.name)

                lot_fields = {
                    "active": "on",
                    "deactivate_after_sale": "",
                    "query": "",
                    "form_created_at": generate_random_id(),
                    "node_id": node_id,
                    "server_id": suitable_game_option["value"],
                    "location": "",
                    "deleted": "",
                    "fields[summary][ru]": summary,
                    "fields[summary][en]": summary_en,
                    "auto_delivery": "",
                    "price": price_data["price"],
                    "amount": len(game.accounts),
                    "fields[game]": game.name,
                    "fields[platform]": suitable_platform_option["value"],
                    "fields[desc][ru]": description,
                    "fields[desc][en]": translate_text(description, "en"),
                    "fields[type]": type_of_lot["value"] if type_of_lot else '',
                    "fields[type2]": type_of_lot["value"] if type_of_lot else ''
                }

                lot = FunPayAPI.types.LotFields(0, lot_fields)
                url = create_new_lot(cardinal.account, lot)
                bot.send_message(message.chat.id, f"✅ Создан лот {game.name} для аренды на {readable_duration}")
            profile = cardinal.account.get_user(cardinal.account.id)
            lots = profile.get_lots()
            counter = 6
            for lot in lots:
                if str(lot.subcategory.id) == str(node_id):
                    game.prices[list(duration_names.keys())[counter]]["url"] = lot.public_link
                    counter -= 1
            edit_game(game.lot_name, {"prices": game.prices})
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при создании лота: {e}")

    def update_lot(message: Message, game: Game):
        try:
            node_id = get_account_game_link(game.lot_name)
            for duration, price_data in game.prices.items():
                readable_duration = duration_names.get(duration, duration)
                game_id = price_data["url"].split("=")[-1]
                lot_fields = cardinal.account.get_lots_field(node_id, game_id)
                if len(game.accounts) > 0:
                    lot_fields["active"] = "on"
                    lot_fields["amount"] = len(game.accounts)
                else:
                    lot_fields["active"] = "off"
                    lot_fields["amount"] = 0
                lot = FunPayAPI.types.LotFields(game_id, lot_fields)
                final_lot_id = lot.lot_id
                fields = lot.fields
                fields["offer_id"] = final_lot_id
                fields["csrf_token"] = cardinal.account.csrf_token
                lot.set_fields(fields)
                cardinal.account.save_lot(lot)
                logger.info(f"[LOTS COPY] Изменил лот {node_id}.")
                bot.send_message(message.chat.id, f"Обновлен лот для {game.name} аренды на {readable_duration}")
        except Exception as e:
            bot.send_message(message.chat.id, f"Ошибка при обновлении лота: {e}")

    def handle_add_account(message: Message):
        msg = bot.send_message(message.chat.id, "�� Введите логин аккаунта Steam:")
        bot.register_next_step_handler(msg, process_login_step)

    def process_login_step(message: Message):
        login = message.text
        msg = bot.send_message(message.chat.id, "🔑 Введите пароль аккаунта Steam:")
        bot.register_next_step_handler(msg, process_password_step, login)

    def process_password_step(message: Message, login: str):
        password = message.text
        msg = bot.send_message(message.chat.id, "📧 Введите логин от почты:")
        bot.register_next_step_handler(msg, process_email_login_step, login, password)

    def process_email_login_step(message: Message, login: str, password: str):
        email_login = message.text
        msg = bot.send_message(message.chat.id, "🔐 Введите пароль от почты:")
        bot.register_next_step_handler(msg, process_email_password_step, login, password, email_login)

    def process_email_password_step(message: Message, login: str, password: str, email_login: str):
        email_password = message.text
        msg = bot.send_message(message.chat.id, "📝 Введите название лота в FunPay:")
        bot.register_next_step_handler(msg, process_lot_step, login, password, email_login, email_password)

    def process_lot_step(message: Message, login: str, password: str, email_login: str, email_password: str):
        lot_name = message.text
        msg = bot.send_message(message.chat.id, "🎯 Введите название игры в FunPay:")
        bot.register_next_step_handler(msg, process_game_step, login, password, email_login, email_password, lot_name)

    def process_game_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str):
        game_name = message.text
        msg = bot.send_message(message.chat.id, "📌 Введите название издания игры в FunPay:")
        bot.register_next_step_handler(msg, process_edition_step, login, password, email_login, email_password, lot_name, game_name)

    def process_edition_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str):
        edition_name = message.text
        msg = bot.send_message(message.chat.id, "💰 Введите цену аренды за 1 час (в рублях):")
        bot.register_next_step_handler(msg, process_price_1h_step, login, password, email_login, email_password, lot_name, game_name, edition_name)

    def process_price_1h_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str):
        try:
            price_1h = str(float(message.text))
            msg = bot.send_message(message.chat.id, "💰 Введите цену аренды за 3 часа (в рублях):")
            bot.register_next_step_handler(msg, process_price_3h_step, login, password, email_login, email_password, lot_name, game_name, edition_name, {"1h": {"price": price_1h, "url": ""}})
        except ValueError:
            bot.send_message(message.chat.id, "❌ Неверный формат, пожалуйста начните заново используя /add_account")

    def process_price_3h_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str, prices: dict):
        try:
            price_3h = float(message.text)
            prices["3h"] = {"price": price_3h, "url": ""}
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 6 часов (в рублях):")
            bot.register_next_step_handler(msg, process_price_6h_step, login, password, email_login, email_password, lot_name, game_name, edition_name, prices)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат, пожалуйста начните заново используя /add_account")

    def process_price_6h_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str, prices: dict):
        try:
            price_6h = float(message.text)
            prices["6h"] = {"price": price_6h, "url": ""}
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 1 день (в рублях):")
            bot.register_next_step_handler(msg, lambda m: process_price_1d_step(m, login, password, email_login, email_password, lot_name, game_name, edition_name, prices))
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат, пожалуйста начните заново используя /add_account")

    def process_price_1d_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str, prices: dict):
        try:
            prices["1d"] = {"price": float(message.text), "url": ""}
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 3 дня (в рублях):")
            bot.register_next_step_handler(msg, lambda m: process_price_3d_step(m, login, password, email_login, email_password, lot_name, game_name, edition_name, prices))
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат, пожалуйста начните заново используя /add_account")

    def process_price_3d_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str, prices: dict):
        try:
            prices["3d"] = {"price": float(message.text), "url": ""}
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 5 дней (в рублях):")
            bot.register_next_step_handler(msg, lambda m: process_price_5d_step(m, login, password, email_login, email_password, lot_name, game_name, edition_name, prices))
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат, пожалуйста начните заново используя /add_account")

    def process_price_5d_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str, prices: dict):
        try:
            prices["5d"] = {"price": float(message.text), "url": ""}
            msg = bot.send_message(message.chat.id, "Введите цену аренды за 7 дней (в рублях):")
            bot.register_next_step_handler(msg, lambda m: process_price_7d_step(m, login, password, email_login, email_password, lot_name, game_name, edition_name, prices))
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат, пожалуйста начните заново используя /add_account")

    def process_price_7d_step(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str, prices: dict):
        try:
            prices["7d"] = {"price": float(message.text), "url": ""}
            process_remaining_prices(message, login, password, email_login, email_password, lot_name, game_name, edition_name, prices)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный формат, пожалуйста начните заново используя /add_account")

    def process_remaining_prices(message: Message, login: str, password: str, email_login: str, email_password: str, lot_name: str, game_name: str, edition_name: str, prices: dict):
        games = load_games()
        account = Account(login, password, email_login, email_password)
        
        try:
            # Find existing game or create new one
            game = next((g for g in games if g.lot_name == lot_name), None)
            if game:
                game.accounts.append(account)
            else:
                game = Game(game_name, lot_name, prices, [account], edition_name)
                games.append(game)
            
            save_games(games)
            bot.send_message(message.chat.id, f"✅ Аккаунт успешно добавлен для игры {game_name}, создаю лоты...")
            create_lot(message, game)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при добавлении аккаунта: {e}")

    def handle_add_account_to_game(message: Message):
        games = load_games()
        if not games:
            bot.send_message(message.chat.id, "❌ В базе данных нет игр. Сначала добавьте игру через /add_account")
            return

        keyboard = K()
        for game in games:
            keyboard.add(B(game.name, callback_data=f"select_game:{game.name}"))
        
        bot.send_message(message.chat.id, "🎮 Выберите игру:", reply_markup=keyboard)

    def handle_get_info_about_game(message: Message):
        games = load_games()
        if not games:
            bot.send_message(message.chat.id, "❌ В базе данных нет игр. Сначала добавьте игру через /add_account")
            return

        keyboard = K()
        for game in games:
            keyboard.add(B(game.name, callback_data=f"info_game:{game.name}"))
        
        bot.send_message(message.chat.id, "🎮 Выберите игру для просмотра информации:", reply_markup=keyboard)

    def handle_delete_account_from_game(message: Message):
        games = load_games()
        if not games:
            bot.send_message(message.chat.id, "❌ В базе данных нет игр. Сначала добавьте игру через /add_account")
            return

        keyboard = K()
        for game in games:
            keyboard.add(B(game.name, callback_data=f"delete_from_game:{game.name}"))
        
        bot.send_message(message.chat.id, "🎮 Выберите игру:", reply_markup=keyboard)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("select_game:"))
    def handle_message(call):
        game_name = call.data.split(":")[1].strip()
        empty_markup = K([])
        # Delete the keyboard
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        
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
        msg = bot.send_message(message.chat.id, "Введите логин от почты:")
        bot.register_next_step_handler(msg, lambda m: process_email_login_for_existing_game_step(m, game_name, login, password))
    def process_email_login_for_existing_game_step(message: Message, game_name: str, login: str, password: str):
        email_login = message.text
        msg = bot.send_message(message.chat.id, "Введите пароль от почты:")
        bot.register_next_step_handler(msg, lambda m: add_account_to_existing_game(m, game_name, login, password, email_login))

    def add_account_to_existing_game(message: Message, game_name: str, login: str, password: str, email_login: str):
        email_password = message.text
        games = load_games()
        game = next((g for g in games if g.name == game_name), None)
        
        try:
            if game:
                account = Account(login, password, email_login, email_password)
                game.accounts.append(account)
                save_games(games)
                bot.send_message(message.chat.id, f"Аккаунт успешно добавлен к игре {game_name}")
                update_lot(message, game)
            else:
                bot.send_message(message.chat.id, f"❌ Игра {game_name} не найдена в базе данных")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при добавлении аккаунта: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith(("info_game:", "delete_from_game:")))
    def handle_game_callbacks(call):
        action, game_name = call.data.split(":")
        
        # Delete the keyboard
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        
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
        
        # Delete the keyboard
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        
        games = load_games()
        game = next((g for g in games if g.name == game_name), None)
        
        if game:
            game.accounts = [acc for acc in game.accounts if acc.login != login]
            save_games(games)
            bot.send_message(call.message.chat.id, f"Аккаунт {login} успешно удален из игры {game_name}")
            update_lot(call.message, game)
        else:
            bot.send_message(call.message.chat.id, "Игра не найдена")

    def create_new_lot(acc: Account, lot: FunPayAPI.types.LotFields):
        """
        Создает лот на нашем аккаунте.

        :param acc: экземпляр аккаунта, на котором нужно создать лот.
        :param lot: экземпляр лота.
        """
        lot_id = lot.lot_id
        fields = lot.fields
        fields["offer_id"] = "0"
        fields["csrf_token"] = acc.csrf_token
        lot.set_fields(fields)
        lot.lot_id = 0

        attempts = 3
        while attempts:
            try:
                url = acc.save_lot(lot)
                logger.info(f"[LOTS COPY] Создал лот {lot_id}.")
                return url
            except Exception as e:
                print(e)
                logger.error(f"[LOTS COPY] Не удалось создать лот {lot_id}.")
                logger.debug("TRACEBACK", exc_info=True)
                if isinstance(e, FunPayAPI.exceptions.RequestFailedError):
                    logger.debug(e.response.content.decode())
                time.sleep(2)
                attempts -= 1
        else:
            raise Exception("Failed to create lot after multiple attempts")

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
