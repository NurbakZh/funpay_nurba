from __future__ import annotations

import base64
import json
import logging
import os.path
import random
import re
import traceback
import aiohttp
from datetime import datetime
from typing import Optional, TYPE_CHECKING, Union, Dict, Any
from configs.config import NS_GIFT_LOGIN, NS_GIFT_PASS
from pathlib import Path

from pip._internal.cli.main import main
from requests.cookies import RequestsCookieJar

from FunPayAPI.common.enums import OrderStatuses
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent, OrderStatusChangedEvent

if TYPE_CHECKING:
    from .cardinal import Cardinal

from tg_bot import CBT, keyboards
from tg_bot.bot import CallbackQuery, Message
from bs4 import BeautifulSoup as bs, PageElement

try:
    import typing_extensions
except ImportError:
    main(["install", "-U", "typing_extensions"])
    import typing_extensions

try:
    from pydantic import BaseModel
except ImportError:
    main(["install", "-U", "pydantic"])
    from pydantic import BaseModel

from requests import Session
import time
from uuid import uuid4

from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

LOGGER_PREFIX = "[AutoSteam]"
logger = logging.getLogger(f"FPC.AutoSteam")

API_URL = "https://api.ns.gifts/api/v1/"
CURRENCIES = ("kzt", "rub", "uah")


def log(msg, lvl: str = "info", **kwargs):
    return getattr(logger, lvl)(f"{LOGGER_PREFIX} {msg}", **kwargs)


class NsGiftsOrder(BaseModel):
    custom_id: str
    status: int
    service_id: int
    quantity: float
    total: float
    login: str
    date: datetime

    @classmethod
    def parse(cls, data: dict):
        return cls.model_validate(data)


class Operation(BaseModel):
    custom_id: str
    status: int
    new_balance: str
    msg: str

    @classmethod
    def parse(cls, data: dict):
        return cls.model_validate(data)


class NoBalance(Exception):
    def __init__(self, balance):
        self.balance = balance

    def __str__(self):
        return f"Не хватает баланса для оплаты услуги. Твой баланс: {self.balance} USD"


class APIError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return str(self.message)


class NoFoundLogin(APIError):
    def __init__(self, login):
        super().__init__(f"⚠️ Логин не найден, либо регион аккаунта - не СНГ. Пожалуйста, перепроверьте логин и регион.\n\n"+

"Если ваш регион не Россия, Украина, Казахстан - отправьте команду «!возврат» без кавычек\n"+
"Если вы ошиблись логином то отправьте верный логин Steam (Не ник)\n"+

"∟ Узнать логин можно по этой ссылке:\n"+
"https://telegra.ph/Gde-poluchit-Login-Steam-02-01")
        self.login = login


class NoAuthError(Exception):
    def __init__(self):
        super().__init__("Не авторизован. Отсутствуют учётные данные.")


class API:
    def __init__(self, login: str, password: str):
        self.login, self.password = login, password
        self._token = None
        self.exp = None
        self.session = Session()
        self._balance = None
        self.cookies = {}
        self._rate = None
        self._lastRateUpdate = None
        self._currency_rates = None
        self._last_currency_update = None

    @property
    def token(self):
        if not self._token or self.exp <= time.time():
            self.auth()
        return self._token

    @property
    def balance(self):
        if self._balance is None and self.is_authorized:
            self._balance = self.check_balance()
        return int(self._balance)

    @property
    def is_authorized(self):
        return self.login and self.password

    def is_has_money(self, sum):
        try:
            return self.check_balance() >= sum
        except:
            logger.error(f"Ошибка при сравнении сумма баланса и суммы заказа: {self.balance} <= {sum}")
            return True

    async def get_exchange_rate(self, from_currency: str, to_currency: str, modifier: float = 1.0) -> Optional[float]:
        """Получает курс обмена с учетом модификатора."""
        if from_currency.upper() == to_currency.upper():
            return 1.0

        if not self._currency_rates or (time.time() - self._last_currency_update) > 3600:
            await self.update_currency_rates()

        if not self._currency_rates:
            return None

        try:
            from_currency = from_currency.lower()
            to_currency = to_currency.lower()

            direct_key = f"{from_currency}/{to_currency}"
            if direct_key in self._currency_rates:
                return float(self._currency_rates[direct_key]) * modifier

            reverse_key = f"{to_currency}/{from_currency}"
            if reverse_key in self._currency_rates:
                return (1 / float(self._currency_rates[reverse_key])) * modifier

            from_usd_key = f"{from_currency}/usd"
            to_usd_key = f"{to_currency}/usd"

            from_usd = self._currency_rates.get(from_usd_key)
            to_usd = self._currency_rates.get(to_usd_key)

            if from_usd is not None and to_usd is not None:
                return (float(to_usd) / float(from_usd)) * modifier

            return None

        except Exception as e:
            logger.error(f"Ошибка при обработке курсов: {e}")
            return None

    async def update_currency_rates(self) -> Optional[Dict[str, Any]]:
        """Обновляет курсы валют через API."""
        try:
            response = await self._request("steam/get_currency_rate")
            self._currency_rates = response
            self._last_currency_update = time.time()
            return response
        except Exception as e:
            logger.error(f"Ошибка при обновлении курсов валют: {e}")
            return None

    def _request(self, endpoint: str, payload: dict = None, method="post", no_auth=False, attempts=3, **kwargs) -> \
        Union[dict, int, float]:
        if not self.is_authorized:
            raise NoAuthError
        payload = payload or {}
        headers = {}
        if not no_auth:
            headers["Authorization"] = f"Bearer {self.token}"
        payload.setdefault("custom_id", str(uuid4()))
        while attempts:
            try:
                res = getattr(self.session, method)(f"{API_URL}{endpoint}", json=payload, headers=headers, **kwargs)
                result = res.json()
                break
            except Exception as e:
                logger.error("Ошибка запроса API: %s", traceback.format_exc())
                attempts -= 1
                time.sleep(2)
        if not isinstance(result, dict):
            return result
        if detail := result.get("detail"):
            if detail in ("Insufficient balance", 'Недостаточно средств'):
                raise NoBalance(self.check_balance())
            if "There is no such login" in detail:
                raise NoFoundLogin(payload.get('data'))
            if detail == "Invalid login details!":
                raise NoAuthError()
            raise APIError(result)
        return result

    @property
    def rate(self):
        if self._rate is None or (time.time() - self._lastRateUpdate) > 30:
            self._rate = self.steam_rate()
        return self._rate

    def course(self, _from, _to="usd"):
        from_cur, to_cur = _from.lower(), _to.lower()
        if from_cur == to_cur: return 1.0
        base = 'usd'
        rate = self.rate
        if from_cur == base: return float(rate.get(f'{to_cur}/{base}'))
        if to_cur == base: return 1 / float(rate.get(f'{from_cur}/{base}'))
        return float(rate.get(f'{to_cur}/{base}')) / float(rate.get(f'{from_cur}/{base}'))

    def convert(self, amount, from_cur, to_cur="usd"):
        rate = self.course(from_cur, to_cur)
        return None if rate is None else amount * rate

    def steam_rate(self):
        response = self._request("steam/get_currency_rate")
        self._rate = response
        log(f"Актуальный курс валют: {self._rate}")
        self._lastRateUpdate = time.time()
        return response

    def auth(self):
        res = self._request("get_token", {"email": self.login, "password": self.password}, no_auth=True)
        self._token, self.exp = res['access_token'], res['valid_thru']

    def check_balance(self):
        res = self._request("check_balance", {})
        try:
            self._balance = float(res)
        except Exception as e:
            logger.error(f"Ошибка при получении баланса: {str(e)}")
            logger.debug("TRACEBACK", exc_info=True)
            return res
        return self._balance

    def get_amount(self, amount: int):
        return self._request("steam/get_amount", {"amount": amount})

    def pay_order(self, custom_id: str) -> Operation:
        result = Operation.parse(self._request("pay_order", {"custom_id": custom_id}))
        self._balance = float(result.new_balance.split(" ")[0])
        return result

    def create_order(self, service_id: int, quantity: float, data: str = None) -> NsGiftsOrder:
        payload = {"service_id": service_id, "quantity": quantity}
        if data:
            payload["data"] = data
        return NsGiftsOrder.parse(self._request("create_order", payload))

    def steam_dep(self, steam_login: str, usd: Union[int, float]) -> Operation:
        if not self.is_has_money(usd):
            raise NoBalance(self.balance)
        order = self.create_order(1, round(usd, 2), steam_login)
        return self.pay_order(order.custom_id)

    @staticmethod
    def create_cookie_jar_from_dict(cookie_dict):
        cookie_jar = RequestsCookieJar()
        for name, value in cookie_dict.items():
            cookie_jar.set(name, value)
        return cookie_jar


NAME = "Auto Steam"
VERSION = "0.0.4"
CREDITS = "@nurba_zh"
DESCRIPTION = "Плагин для авто-пополнения Steam"
UUID = "ddf8b65f-1bc6-4ca2-bb76-6b2d187f6272"
SETTINGS_PAGE = True

logger.info(f"{LOGGER_PREFIX} Плагин успешно запущен.")

ORDERS = {}
TG_STATES, STATES = {}, {}
SETTINGS: Optional['Settings'] = None


def _get_path(filename):
    if "." not in filename:
        filename += ".json"
    parts = f"storage/plugins/auto_steam/{filename}".split("/")
    return os.path.join(os.path.dirname(__file__), "..", *parts)


folder_path = os.path.join(os.path.dirname(__file__), "..", *"storage/plugins/auto_steam".split("/"))
if not os.path.exists(folder_path):
    os.makedirs(folder_path)


def _load_file(filename):
    _path = _get_path(filename)
    if not os.path.exists(_path):
        return {}
    with open(_path, encoding="utf-8") as file:
        data = json.load(file)
    return data


def _save_file(filename, data):
    _path = _get_path(filename)
    data = data.dict() if hasattr(data, "dict") else data
    with open(_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def _get_files(): return [_get_path(f) for f in ["states.json", "settings.json", "orders.json"]]


class Settings(BaseModel):
    enabled: bool = True
    lots_activated: bool = True
    notification_not_balance: bool = True
    notification_new_order: bool = False
    notification_order_completed: bool = True
    notification_error: bool = True

    autoback_bad_amoount: bool = True
    min: Union[int, float] = 15
    max: Union[int, float] = 30000
    autoback_bad_curr: bool = True
    obschet: float = 1  # обсчет в процентах спецом для степаныча

    again_complete_orders_no_balance: bool = False

    def __getitem__(self, item): return getattr(self, item)

    def __setitem__(self, key, value): setattr(self, key, value); save_settings()

    def clear(self): self.enabled = True; save_settings()

    def toggle(self, p): setattr(self, p, not getattr(self, p)); save_settings()


class OrderStatus:
    CLOSED = 'CLOSED'
    PENDING = 'PENDING'
    WAIT_LOGIN = 'WAIT_LOGIN'
    WAIT_ACCEPT = 'WAIT_ACCEPT'
    ERROR = 'ERROR'
    NO_MONEY = 'NO_MONEY'
    REFUND = 'REFUND'

Os = OrderStatus


class Order:
    def __init__(self, id, chat, buyer, amount, login=None, status=Os.WAIT_LOGIN, currency="RUB", no_money=False):
        self.id = id
        self.chat = chat
        self.buyer = buyer
        self.amount = amount
        self.login = login
        self.status = status
        self.currency = currency
        self.no_money = no_money

    def edit(self, **kw): [setattr(self, k, v) for k, v in kw.items()]; save_orders()

    def delete(self): del ORDERS[self.id]; save_orders()


def load_states(): global STATES; STATES = _load_file("states")


def save_states(): global STATES; _save_file("states", STATES)


def load_settings(): global SETTINGS; SETTINGS = Settings(**_load_file("settings"))


def save_settings(): global SETTINGS; _save_file("settings", SETTINGS)


def load_orders(): global ORDERS; ORDERS = {k: Order(**v) for k, v in _load_file('orders').items()}


def save_orders(): global ORDERS; _save_file('orders', {k: v.__dict__ for k, v in ORDERS.items()})


load_orders()
load_states()
load_settings()


class _CBT:
    UPDATE_TOKEN = "tg_update_token"
    UPDATE_MNEMONIC = "tg_update_mnemonic"
    UPDATE_ADDRESS = 'tg_edit_address'
    SETTINGS_PLUGIN = f'{CBT.PLUGIN_SETTINGS}:{UUID}'
    TOGGLE_NOTIFICATIONS = 'TOGGLE_NOTIFICATIONS'
    NOTIFICATIONS_MENU = "NOTIFICATIONS_MENU"
    SWITCH_LOTS = "SWITCH_LOTS"
    TOGGLE_SETTINGS_PARAM = "TOGGLE_SETTINGS_PARAM"
    EDIT_MNEMONIC = "EDIT_MNEMONIC"
    CLEAR_SETTINGS = 'CLEAR_SETTINGS'
    PAYMENT_METHOD_CHANGE = "PAYMENT_METHOD_CHANGE"
    GET_FILES = "GET_FILES"
    OTPRAVKA = "OTPRAVKA"
    OTPRAVKA_LOGIN = "OTPRAVKA_LOGIN"
    OTPRAVKA_CURRENCY = "OTPRAVKA_CURRENCY"
    OTPRAVKA_AMOUNT = "OTPRAVKA_AMOUNT"
    EDIT_LIMITS = 'EDIT_LIMITS'
    HANDLE_NO_BALANCE_ORDERS = 'HANDLE_NO_BALANCE_ORDERS'
    EDIT_OBS = 'EDIT_OBS'


def _is_on(var): return "🟢" if var else "🔴"


def main_kb():
    kb = K(row_width=1).add(
        B(f"{_is_on(SETTINGS.enabled)} Авто-пополнение Steam", None, f"{_CBT.TOGGLE_SETTINGS_PARAM}:enabled"),
        B(f"{'🕹 Деа' if SETTINGS.lots_activated else '🕹 А'}ктивировать лоты", None, _CBT.SWITCH_LOTS)) \
        .row(B("🔔 Настройки уведомлений", None, _CBT.NOTIFICATIONS_MENU)) \
        .row(B(f"{_is_on(SETTINGS.autoback_bad_amoount)} Авто-возврат заказов вне лимита", None,
               F"{_CBT.TOGGLE_SETTINGS_PARAM}:autoback_bad_amoount"))
    if SETTINGS.autoback_bad_amoount:
        kb.row(
            B(f"Мин: {SETTINGS.min} ₽", None, f"{_CBT.EDIT_LIMITS}:min"),
            B(f"Макс: {SETTINGS.max} ₽", None, f"{_CBT.EDIT_LIMITS}:max")
        )
    kb.row(B(f"{_is_on(SETTINGS.autoback_bad_curr)} Авто-возврат если не валюта не СНГ",
             None, f"{_CBT.TOGGLE_SETTINGS_PARAM}:autoback_bad_curr"))
    kb.row(B(f"Кидать {'больше' if SETTINGS.obschet >= 0 else 'меньше'} на {SETTINGS.obschet}%", None, _CBT.EDIT_OBS))
    kb.row(B(f"🗑 Сбросить", None, _CBT.CLEAR_SETTINGS),
           B("📁 Файлы", None, f"{_CBT.GET_FILES}:all")) \
        .row(B("◀️ Назад", None, f"{CBT.EDIT_PLUGIN}:{UUID}:0"))
    return kb


def notifications_kb():
    return K(row_width=1).add(*[
        B(f"{_is_on(getattr(SETTINGS, param))} {name}", None, f"{_CBT.TOGGLE_NOTIFICATIONS}:{param}")
        for param, name in [
            ("notification_order_completed", "Заказ выполнен"),
            ("notification_new_order", "Новый заказ на Steam"),
            ("notification_not_balance", "Закончился баланс")
        ]
    ]).row(B("◀️ Назад", None, _CBT.SETTINGS_PLUGIN))


_edit_plugin = keyboards.edit_plugin


def new_edit_plugin(c: 'Cardinal', uuid: str, offset: int = 0, ask_to_delete: bool = False):
    kb = _edit_plugin(c, uuid, offset, ask_to_delete)
    if uuid == UUID:
        kb.keyboard.insert(0, [B("👨🏼‍💻 Разработчик", f"https://t.me/{CREDITS[1:]}")])
    return kb


keyboards.edit_plugin = new_edit_plugin


def main_text():
    return f"""<b>🔵 Настройки плагина Auto-Steam</b>

∟ Логин: <code>{api.login}</code>
∟ Пароль: <code>{api.password}</code>

∟ Баланс: <code>{api.check_balance()} USD</code>
∟ Доступные валюты: <code>{', '.join(CURRENCIES)}</code>

∟ Актуальный курс <b>USD</b>:
    • <code>{api.rate['kzt/usd']} KZT</code>
    • <code>{api.rate['rub/usd']} RUB</code> 
    • <code>{api.rate['uah/usd']} UAH</code>"""


def notifications_text(): return f"🔔 <b>Настройки уведомлений</b>"


def _get_lots(cardinal: 'Cardinal', get_ids=True) -> list[Union[PageElement, int]]:
    html = bs(cardinal.account.method("get", "/lots/1086/trade", {}, {}, raise_not_200=True).text, "html.parser")
    elems = html.find_all('a', {"class": "tc-item"})
    if not elems: html.find_all('a', {"class": "tc-item warning"})
    return [int(id['data-offer']) for id in elems] if get_ids else elems


def _switch_state_lot(cardinal, lot_id, active=0):
    try:
        fields = cardinal.account.get_lot_fields(lot_id)
        fields.active = bool(active)
        cardinal.account.save_lot(fields)
        return 1
    except:
        log(f'Ошибка при изменении активности лота {lot_id}')
        logger.debug("TRACEBACK", exc_info=True)
        return 0


def init(cardinal: 'Cardinal'):
    global notifications, tg_logs, api

    tg = cardinal.telegram
    bot = tg.bot
    tg_logs = TgLogs(cardinal)

    Runner(cardinal).start()

    cardinal.add_telegram_commands(UUID, [
        ("otpravka", "создать заказ на пополнение Steam", True),
    ])

    def send(chat_id, msg, reply_markup=None, **kwargs):
        return bot.send_message(chat_id, msg, reply_markup=reply_markup, parse_mode="HTML", **kwargs)

    def answer_cb(c: CallbackQuery, msg=None, alert=False):
        return bot.answer_callback_query(c.id, msg, show_alert=alert)

    def _edit_msg(m: Message, text, reply_markup=None, **kwargs):
        return bot.edit_message_text(text, m.chat.id, m.message_id, **kwargs, reply_markup=reply_markup)

    def handle_otpravka(m: Message):
        print("DEBUG: Starting otpravka command")
        bot.send_message(m.chat.id, "Введите логин Steam:")
        tg.set_state(m.chat.id, m.message_id, m.from_user.id, "waiting_login", {})

    def handle_otpravka_login(m: Message):
        print(f"DEBUG: Received message for login: {m.text}")
        if not tg.check_state(m.chat.id, m.from_user.id, "waiting_login"):
            print("DEBUG: Not in waiting_login state")
            return
            
        login = m.text.strip()
        print(f"DEBUG: Processing login: {login}")
        
        if not re.match(r'^[a-zA-Z0-9_]{3,}$', login):
            print("DEBUG: Invalid login format")
            return bot.send_message(m.chat.id, "❌ Неверный формат логина. Используйте только латинские буквы, цифры и подчеркивание.")
        
        print("DEBUG: Login valid, showing currency keyboard")
        kb = K(row_width=1).add(
            B("KZT", None, f"{_CBT.OTPRAVKA_CURRENCY}:kzt"),
            B("RUB", None, f"{_CBT.OTPRAVKA_CURRENCY}:rub"),
            B("UAH", None, f"{_CBT.OTPRAVKA_CURRENCY}:uah")
        )
        bot.send_message(m.chat.id, "Выберите валюту:", reply_markup=kb)
        tg.set_state(m.chat.id, m.message_id, m.from_user.id, "waiting_currency", {"login": login})

    def handle_otpravka_currency(c: CallbackQuery):
        print(f"DEBUG: Received currency callback: {c.data}")
        if not tg.check_state(c.message.chat.id, c.from_user.id, "waiting_currency"):
            print("DEBUG: Not in waiting_currency state")
            return
            
        currency = c.data.split(":")[-1]
        print(f"DEBUG: Selected currency: {currency}")
        
        state = tg.get_state(c.message.chat.id, c.from_user.id)
        if not state or "login" not in state["data"]:
            print("DEBUG: No login found in state")
            return bot.send_message(c.message.chat.id, "❌ Ошибка: данные не найдены. Начните заново.")
        
        state_data = state["data"]
        state_data["currency"] = currency
        tg.set_state(c.message.chat.id, c.message.message_id, c.from_user.id, "waiting_amount", state_data)
        
        print("DEBUG: Asking for amount")
        bot.edit_message_text("Введите сумму пополнения:", c.message.chat.id, c.message.message_id)

    def handle_otpravka_amount(m: Message):
        print(f"DEBUG: Received amount message: {m.text}")
        try:
            state = states.get(m.from_user.username)
            if not state or state["state"] != "wait_amount":
                print(f"DEBUG: Invalid state for user {m.from_user.username}")
                return
            
            login = state["data"]["login"]
            currency = state["data"]["currency"]
            print(m.text, "m.text")
            amount = float(m.text.replace(',', '.'))
            
            print(f"DEBUG: Processing order - Login: {login}, Currency: {currency}, Amount: {amount}")
            
            # Convert amount to USD if needed
            if currency.upper() != "USD":
                exchange_rate = api.course(currency, "USD")
                if exchange_rate:
                    amount_usd = round(amount / exchange_rate, 2)
                    print(f"DEBUG: Converted {amount} {currency} to {amount_usd} USD")
                else:
                    print(f"DEBUG: Failed to get exchange rate for {currency} -> USD")
                    bot.send_message(m.chat.id, "❌ Ошибка при получении курса валюты. Попробуйте позже.")
                    return
            else:
                amount_usd = amount
            
            # Apply markup
            amount_with_markup = amount_usd + (amount_usd * (SETTINGS.obschet / 100))
            print(f"DEBUG: Amount with markup: {amount_with_markup} USD")
            
            # Try to perform the top-up
            try:
                operation = api.steam_dep(login, amount_with_markup)
                print(f"DEBUG: Top-up successful - Operation: {operation}")
                
                # Create order record with CLOSED status
                order = Order(
                    id=f"TG_{int(time.time())}",
                    chat=m.chat.id,
                    buyer=m.from_user.username or str(m.from_user.id),
                    amount=amount,
                    login=login,
                    status=Os.CLOSED,
                    currency=currency
                )
                orders.add(order)
                
                # Send success message
                success_msg = f"✅ Средства успешно отправлены!\n\n"
                success_msg += f"∟ Логин Steam: {login}\n"
                success_msg += f"∟ Сумма пополнения: {amount} {currency}\n\n"
                success_msg += f"❤️ Спасибо за покупку!"
                bot.send_message(m.chat.id, success_msg)
                
                # Clear state
                states.clear(m.from_user.username)
                
            except NoFoundLogin as e:
                print(f"DEBUG: Login not found: {str(e)}")
                # Create order record with ERROR status to allow refund
                order = Order(
                    id=f"TG_{int(time.time())}",
                    chat=m.chat.id,
                    buyer=m.from_user.username or str(m.from_user.id),
                    amount=amount,
                    login=login,
                    status=Os.ERROR,
                    currency=currency
                )
                orders.add(order)
                error_msg = Texts.login_not_found(login)
                print(f"DEBUG: Sending error message: {error_msg}")
                bot.send_message(m.chat.id, error_msg)
                
            except NoBalance as e:
                print(f"DEBUG: Not enough balance: {str(e)}")
                error_msg = Texts.no_balance()
                print(f"DEBUG: Sending error message: {error_msg}")
                bot.send_message(m.chat.id, error_msg)
                tg_logs.no_balance(amount, login, currency, m.chat.id, f"TG_{int(time.time())}")
                
            except APIError as ex:
                print(f"DEBUG: API Error: {ex.message}")
                logger.error(f"Ошибка при пополнении Steam. Ответ сервера: {ex.message}")
                logger.debug("TRACEBACK", exc_info=True)
                error_msg = Texts.error()
                print(f"DEBUG: Sending error message: {error_msg}")
                bot.send_message(m.chat.id, error_msg)
                tg_logs.error(amount, currency, f"TG_{int(time.time())}", m.chat.id, f"Ответ сервера Steam пополнения: {ex.message}")
                
        except Exception as e:
            print(f"DEBUG: Unexpected error: {str(e)}")
            logger.error(f"Ошибка при обработке команды отправака: {str(e)}")
            logger.debug("TRACEBACK", exc_info=True)
            bot.send_message(m.chat.id, "❌ Произошла ошибка при обработке команды. Попробуйте позже.")

    tg.msg_handler(handle_otpravka, commands=['otpravka'])
    tg.msg_handler(handle_otpravka_login, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, "waiting_login"))
    tg.cbq_handler(handle_otpravka_currency, lambda c: c.data.startswith(f"{_CBT.OTPRAVKA_CURRENCY}:"))
    tg.msg_handler(handle_otpravka_amount, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, "waiting_amount"))

    def edit_limits(c: CallbackQuery):
        arg = c.data.split(":")[-1]

        def _edit_limits(m: Message):
            key = tg.get_state(m.chat.id, m.from_user.id)['data']['key']
            try:
                amount = float(m.text)
                if amount < 15:
                    raise Exception
            except:
                return bot.send_message(m.chat.id, "❌ Неверное значение. Пожалуйста, введите число.")
            SETTINGS[key] = amount
            bot.send_message(m.chat.id, main_text(), reply_markup=main_kb())

        __handle_state_message(c.message.chat.id, c.from_user.id, _CBT.EDIT_LIMITS, _edit_limits,
                               f"Отправь мне новую {'мин' if arg == 'min' else 'макс'}имальную сумму покупки пополнения в рублях",
                               {"key": arg}, cb=c)

    def open_menu(c: CallbackQuery):
        _edit_msg(c.message, main_text(), reply_markup=main_kb())

    def open_menu_msg(m: Message):
        bot.send_message(m.chat.id, main_text(), reply_markup=main_kb())

    def notifications_menu(c: CallbackQuery):
        _edit_msg(c.message, notifications_text(), reply_markup=notifications_kb())

    def toggle_notifications(c: CallbackQuery):
        SETTINGS.toggle(c.data.split(":")[-1])
        _edit_msg(c.message, notifications_text(), reply_markup=notifications_kb())

    def activate_deacitvate_lots(c: CallbackQuery):
        _prefix = 'Деа' if SETTINGS.lots_activated else "А"
        count = 0
        for lot_id in (all_lots := _get_lots(cardinal)):
            lot_url = f"https://funpay.com/lots/offerEdit?node=2418&offer={lot_id}"
            result = _switch_state_lot(cardinal, lot_id, not SETTINGS.lots_activated)
            if result:
                count += 1
            else:
                bot.send_message(c.message.chat.id,
                                 f'❌ Ошибка при {_prefix}ктивации лота <a href="{lot_url}">{lot_id}</a>')
                continue
            bot.send_message(c.message.chat.id, (
                '🌑 Деа' if SETTINGS.lots_activated else '🌕 А') + f'ктивировал лот <a href="{lot_url}">{lot_id}</a>')
        SETTINGS.lots_activated = not SETTINGS.lots_activated
        save_settings()
        bot.delete_message(c.message.chat.id, c.message.id)
        send(c.message.chat.id, main_text(), main_kb())
        bot.send_message(c.message.chat.id, f'✅<b> {_prefix}ктивировал <code>{count}/{len(all_lots)}</code> лотов</b>')

    def toggle_param(c: CallbackQuery):
        SETTINGS.toggle(c.data.split(":")[-1])
        _edit_msg(c.message, main_text(), reply_markup=main_kb())

    cfg = {"login": NS_GIFT_LOGIN, "password": NS_GIFT_PASS}
    api = API(**cfg)

    def clear_settings(c: CallbackQuery):
        global SETTINGS
        SETTINGS = Settings();
        save_settings()
        answer_cb(c, f"🔄 Настройки успешно сброшены", alert=True)
        try:
            _edit_msg(c.message, main_text(), reply_markup=main_kb())
        except:
            ...

    def get_files(c: CallbackQuery):
        file = c.data.split(":")[-1]
        files = _get_files() if file == "all" else (_get_path(file),)
        for _file in files:
            if os.path.exists(_file):
                bot.send_document(c.message.chat.id, open(_file, "rb"))
        answer_cb(c)

    def handle_no_money_orders(c: CallbackQuery):
        r = bot.send_message(c.message.chat.id, f"<b>⏰ начал обрабатывать заказы, на которые не хватило баланса...</b>")
        _handle_no_money_orders(cardinal)
        bot.delete_message(c.message.chat.id, r.id)
        bot.send_message(c.message.chat.id, f"<b>✅ Обработка закончена.\n\n"
                                            f"Подробнее - /logs</b>")

    def edit_obs(c: CallbackQuery):
        def edited_obs(m: Message):
            try:
                a = float(m.text)
            except Exception as e:
                return bot.send_message(m.chat.id, f"Отправь цифру")
            SETTINGS.obschet = a
            save_settings()
            bot.send_message(m.chat.id, main_text(), reply_markup=main_kb())
        __handle_state_message(c.message.chat.id, c.from_user.id, _CBT.EDIT_OBS, edited_obs,
                               "Отправь мне процент обсчета. Меньше 0, "
                               "если кидать меньше на указанный процент, больше 0, если кидать больше", cb=c)

    tg.cbq_handler(open_menu, lambda c: c.data.startswith(_CBT.SETTINGS_PLUGIN))
    tg.cbq_handler(toggle_notifications, lambda c: c.data.startswith(f"{_CBT.TOGGLE_NOTIFICATIONS}:"))
    tg.cbq_handler(notifications_menu, lambda c: c.data == _CBT.NOTIFICATIONS_MENU)
    tg.cbq_handler(toggle_param, lambda c: c.data.startswith(f"{_CBT.TOGGLE_SETTINGS_PARAM}:"))
    tg.msg_handler(open_menu_msg, commands=['auto_steam'])
    tg.cbq_handler(activate_deacitvate_lots, lambda c: c.data == _CBT.SWITCH_LOTS)
    tg.cbq_handler(clear_settings, lambda c: c.data == _CBT.CLEAR_SETTINGS)
    tg.cbq_handler(get_files, lambda c: c.data.startswith(f"{_CBT.GET_FILES}:"))
    tg.cbq_handler(edit_limits, lambda c: c.data.startswith(f"{_CBT.EDIT_LIMITS}:"))
    tg.cbq_handler(handle_no_money_orders, lambda c: c.data.startswith(f"{_CBT.HANDLE_NO_BALANCE_ORDERS}"))
    tg.cbq_handler(edit_obs, lambda c: c.data.startswith(f"{_CBT.EDIT_OBS}"))

    tg.add_command_to_menu("auto_steam", "Настройки плагина auto-steam")


class FpSt:
    LWAIT_LOGIN = 'WAIT_LOGIN'
    WAIT_ACCEPT = 'WAIT_ACCEPT'


class Orders:
    dc = ORDERS

    def add(self, order): self.dc[order.id] = order; save_orders()

    def get(self, id) -> Order: return self.dc.get(id)

    def all(self, **filtres): return [i for i in list(self.dc.values()) if
                                      all(getattr(i, k) in (v if isinstance(v, (list, tuple)) else (v,))
                                          for k, v in filtres.items())]


class States:
    storage = STATES

    def get(self, us, default=None): return self.storage.get(us, default)

    def set(self, us, state, data={}): self.storage[us] = {'state': state, 'data': data}; save_states()

    def clear(self, us): del self.storage[us]; save_states()

    def check(self, us, state): return self.get(us, {}).get('state') in (
        state if isinstance(state, (list, tuple)) else (state,))

    def update_data(self, us, data, _replace=False):
        _data = self.get(us, {}).get('data')
        if _replace: _data = {}
        _data.update(data)
        save_states()


class Texts:
    @staticmethod
    def error():
        return f"""❌ Ошибка! Сообщил продавцу, ожидайте!"""

    @staticmethod
    def login_not_found(login):
        return f"""
⚠️ Логин не найден, либо регион аккаунта - не Россия, Украина, Казахстан. Пожалуйста, перепроверьте логин и регион.
Если ваш регион не совпадает - отправьте команду «!возврат» без кавычек

∟ Если вы ошиблись логином то отправьте верный логин Steam (Не ник)
∟ Узнать логин можно по этой ссылке:
https://telegra.ph/Gde-poluchit-Login-Steam-02-01"""

    @staticmethod
    def no_balance():
        return (
            f"⏳ В данный момент наблюдается высокая нагрзука. Пожалуйста, ожидайте своей очереди. Спасибо за Ваше терпение!\n\n"
            f"Ваша позиция в очереди: #{random.randint(6, 9)}")


class TgLogs:
    def __init__(self, c: 'Cardinal'):
        self.c = c
        self.bot = self.c.telegram.bot

    def _kb(self, order_id: object, chat_id: object) -> object:
        return K().add(
            B("💰 Заказ", F"https://funpay.com/orders/{order_id}"),
            B("💬 Чат", f"https://funpay.com/chat/?node={chat_id}")
        )

    def _send(self, text, kb=None, **kw):
        try:
            for chat_id in self.c.telegram.authorized_users:
                self.bot.send_message(chat_id, text, reply_markup=kb, **kw)
        except Exception as e:
            log(f"Ошибка при отправке уведомления: {str(e)}", lvl="error")
            logger.error("TRACEBACK", exc_info=True)

    def no_balance(self, amount, login, currency, chat_id, order_id):
        if not SETTINGS.notification_not_balance: return
        postfix = "\n\n🕖 Заказ будет выполнен автоматически после пополнения баланса на сайте поставщика" if \
            SETTINGS.again_complete_orders_no_balance else ""
        self._send(f"""<b>🫰 не хватает денег для пополнения Steam

∟ Логин Steam: <code>{login}</code>
∟ Сумма пополнения: <code>{amount}</code> {currency}</b>{postfix}""",
                   kb=self._kb(order_id, chat_id))

    def order_completed(self, amount, curr, login, chat_id, order_id, _is_after_dep_balance=False):
        if not SETTINGS.notification_order_completed: return
        prefix = "Успешное пополнение Steam" if not _is_after_dep_balance else \
            "Успешное отложенное Steam пополнение"
        self._send(f"""🔷 <b>{prefix}

∟ Сумма пополнения: <code>{amount}</code> {curr}
∟ Логин Steam: <code>{login}</code></b>""", kb=self._kb(order_id, chat_id))

    def error(self, amount, curr, order_id, chat_id, message_error):
        if not SETTINGS.notification_error: return
        self._send(f"""❌ <b>Ошибка при пополнении <code>{amount}</code> {curr} Steam</b>

<code>{message_error}</code>""", kb=self._kb(order_id, chat_id))

    def new_order(self, amount, curr, order_id, chat_id, buyer_username):
        if not SETTINGS.notification_new_order: return
        self._send(f"""🧿 <b>Новый заказ на пополнение Steam

∟ Сумма пополнения: <code>{amount}</code> {curr}
∟ Покупатель: <code>{buyer_username}</code></b>""", kb=self._kb(order_id, chat_id))

    def bad_amount(self, amount, currency, order_id, chat_id):
        self._send(f"""<b>⛔️ Недопустимая сумма Steam пополнения <code>{amount}</code> {currency}

∟ Мин. сумма: <code>{SETTINGS.min}</code>
∟ Макс. сумма: <code>{SETTINGS.max}</code>

Оформите возврат по заказу <a href='https://funpay.com/orders/{order_id}/'>#{order_id}</a> </b>""",
                   kb=self._kb(order_id, chat_id))


states, orders = States(), Orders()


def _handle_next_order(c: 'Cardinal', buyer_username: str):
    orders_list = orders.all(buyer=buyer_username, status=(Os.WAIT_LOGIN, Os.WAIT_ACCEPT))
    if not orders_list: return
    order = orders_list[0]
    _handle_order(c, order, is_old=True)


# noinspection PyTypeChecker
def _handle_order(c: 'Cardinal', order: Order, is_old=False):
    states.set(order.buyer, order.status, {"order_id": order.id})


def new_order(c: 'Cardinal', e: NewOrderEvent):
    crrncy = e.order.description[:3]
    subcat = e.order.subcategory_name.strip().lower()
    if 'steam' not in subcat or "пополнение баланса" not in subcat: return
    if crrncy.lower() not in CURRENCIES:
        if SETTINGS.autoback_bad_curr:
            c.account.refund(e.order.id)
        c.send_message(e.order.chat_id, f"Доступные валюты: {', '.join(CURRENCIES)}\n\n"
                                        f"Купленная валюта: {crrncy}")
        return
    amount_in_lot = e.order.description.split(",")[-1].strip()
    if amount_in_lot.isdigit():
        e.order.amount *= int(amount_in_lot)
    sub_rum = api.convert(e.order.amount, crrncy, 'rub')
    log(f"Новый заказ на пополнение Steam #{e.order.id}. Покупатель: {e.order.buyer_username}. "
        f"Сумма: {e.order.amount} {crrncy}. Итоговая сумма: {sub_rum} RUB")
    
    order = Order(e.order.id, e.order.chat_id, e.order.buyer_username, e.order.amount, currency=crrncy)
    orders.add(order)
    tg_logs.new_order(order.amount, order.currency, order.id, order.chat, order.buyer)
    _handle_order(c, order)


# =============== обработчики сообщений ============= #

def __find_login(text): return next(iter(re.findall(r'\b[a-zA-Z0-9_]{3,}\b', text)), None)


def __handle_steam_deposit(c: 'Cardinal', chat_id, username, _not_answer_errors=()) -> Operation | int:
    try:
        order_id = states.get(username)['data']['order_id']
        order = orders.get(order_id)
        amount = api.convert(order.amount, order.currency)
        operation = api.steam_dep(order.login, amount + (amount * (SETTINGS.obschet / 100)))
    except NoFoundLogin:
        if NoFoundLogin in _not_answer_errors: return -4
        c.send_message(chat_id, Texts.login_not_found(order.login))
        order.edit(status=Os.WAIT_LOGIN)
        states.set(order.buyer, Os.WAIT_LOGIN, {"order_id": order.id})
        return -4
    except APIError as ex:
        if APIError in _not_answer_errors: return -2
        logger.error(f"Ошибка при пополнении Steam. Ответ сервера: {ex.message}")
        logger.debug("TRACEBACK", exc_info=True)
        c.send_message(chat_id, Texts.error())
        tg_logs.error(order.amount, order.currency, order.id, order.chat,
                      f"Ответ сервера Steam пополнения: {ex.message}")
        return -2
    except Exception as ex:
        if type(ex) in _not_answer_errors: return -1
        logger.error(f"Ошибка при пополнении Steam: {str(ex)}")
        logger.debug("TRACEBACK", exc_info=True)
        c.send_message(chat_id, Texts.error())
        tg_logs.error(order.amount, order.currency, order.id, order.chat, traceback.format_exc())
        return -1
    else:
        return operation


def _complete_order(c: 'Cardinal', chat_id, username, _order_id=None, _next_order=True, _after_no_money=False,
                    _not_answer_errors=()):
    log(f"{username} подтвердил логин. Пополняю стим...")
    op = __handle_steam_deposit(c, chat_id, username, _not_answer_errors=_not_answer_errors)
    if isinstance(op, int): return op
    order_id = _order_id or states.get(username)['data']['order_id']
    order = orders.get(order_id)
    order.edit(status=Os.CLOSED)
    tg_logs.order_completed(order.amount, order.currency, order.login, order.chat, order.id,
                            _is_after_dep_balance=_after_no_money)
    
    # Update lots after successful payment
    update_lots_topup(c, order.currency, order.amount)
    
    states.clear(order.buyer)
    if _next_order:
        _handle_next_order(c, order.buyer)


def new_msg(c: 'Cardinal', e: NewMessageEvent):
    if e.message.author == c.account.username: return
    
    # Handle other states
    if e.message.text.strip() == "+" and states.check(e.message.author, FpSt.WAIT_ACCEPT):
        return _handle_accept_order(c, e)
        
    if states.check(e.message.author, (FpSt.LWAIT_LOGIN, FpSt.WAIT_ACCEPT)):
        login = __find_login(e.message.text)
        if login:
            return _handle_send_login_user(c, e, login)


# =============== обработка возврата ================= #

def new_order_status_changed(c: 'Cardinal', e: OrderStatusChangedEvent):
    if not orders.all(buyer=e.order.buyer_username): return
    if e.order.status not in (OrderStatuses.CLOSED, OrderStatuses.REFUNDED): return
    order = orders.get(e.order.id)
    if not order or order.status in (Os.CLOSED, Os.WAIT_LOGIN): return
    new_status = Os.CLOSED if e.order.status == OrderStatuses.CLOSED else Os.WAIT_LOGIN
    order.edit(status=new_status)
    log(f"Заказ {e.order.id} {'подтвержден' if new_status == Os.CLOSED else 'возвращен'}. Изменил его статус на {new_status}")


# ===================== цикл чекер заказов =============== #

def _handle_no_money_orders(cardinal: 'Cardinal'):
    orders_list = orders.all(no_money=True)
    orders_list.sort(key=lambda x: (x.amount, CURRENCIES.index(x.currency.lower())))
    for o in orders_list:
        log(f"Пытаюсь повторно выполнить заказ #{o.id} на {o.amount} {o.currency} после пополнения баланса. Покупатель: {o.buyer}.")
        result = _complete_order(cardinal, o.chat, o.buyer, _order_id=o.id,
                                 _not_answer_errors=(NoBalance,))
        if result == -4:
            log(f"Не удалось повторно выполнить заказ #{o.id} на {o.amount} {o.currency}. Не нашел логин Steam: {o.login}")
            time.sleep(60)
            break
        if result == -3:
            log(f"Не удалось повторно выполнить заказ #{o.id} на {o.amount} {o.currency}. Нет денег. Мой баланс: {api.balance}")
            time.sleep(60)
            break
        if result == -2:
            log(f"Не удалось повторно выполнить заказ #{o.id} на {o.amount} {o.currency}. Ответ сервера Steam API")
            time.sleep(60)
            break
        if result == -1:
            log(f"Не удалось повторно выполнить заказ #{o.id} на {o.amount} {o.currency}. Ошибка при пополнении Steam")
            time.sleep(60)
            break
    time.sleep(60)


class Runner:
    def __init__(self, cardinal: 'Cardinal'): self.cardinal = cardinal

    def _run(self):
        while True:
            _handle_no_money_orders(self.cardinal)

    def start(self):
        # Thread(target=self._run).start()
        # log("Запустил цикл чекер заказов")
        return self


BIND_TO_PRE_INIT = [init]
BIND_TO_NEW_ORDER = [new_order]
BIND_TO_NEW_MESSAGE = [new_msg]
BIND_TO_ORDER_STATUS_CHANGED = [new_order_status_changed]
BIND_TO_DELETE = None


def update_lots_topup(cardinal: Cardinal, currency: str, amount: float):
    """Обновляет лоты пополнения Steam после успешной оплаты"""
    try:
        # Получаем текущий баланс в USD
        current_balance = api.check_balance()
        if current_balance is None:
            log("Ошибка: не удалось получить баланс", lvl="error")
            return
            
        log(f"Текущий баланс в USD: {current_balance}")
        
        # Получаем актуальные курсы валют
        rate = api.rate
        if not rate or not isinstance(rate, dict):
            log("Ошибка: не удалось получить курсы валют или неверный формат", lvl="error")
            return
            
        log(f"Актуальные курсы: {rate}")
        
        # Обновляем лоты для каждой валюты
        for curr in CURRENCIES:
            try:
                # Конвертируем баланс в текущую валюту для количества
                converted_balance = api.convert(current_balance, "usd", curr)
                if converted_balance is None:
                    log(f"Ошибка: не удалось конвертировать баланс в {curr.upper()}", lvl="error")
                    continue
                    
                log(f"Конвертированный баланс в {curr.upper()}: {converted_balance}")
                
                # Рассчитываем курс относительно рубля
                if curr.lower() == "rub":
                    price = 1.0  # 1 RUB = 1 RUB
                else:
                    try:
                        # Получаем курс через USD, обрабатывая строковые значения
                        rub_to_usd_str = rate.get('rub/usd')
                        curr_to_usd_str = rate.get(f'{curr.lower()}/usd')
                        
                        if rub_to_usd_str is None or curr_to_usd_str is None:
                            log(f"Ошибка: отсутствует курс для {curr.upper()}", lvl="error")
                            continue
                            
                        print(rub_to_usd_str, curr_to_usd_str, "rub_to_usd_str, curr_to_usd_str")
                        # Преобразуем строки в числа, заменяя запятые на точки
                        rub_to_usd = float(str(rub_to_usd_str).replace(',', '.'))
                        curr_to_usd = float(str(curr_to_usd_str).replace(',', '.'))
                        
                        if rub_to_usd == 0 or curr_to_usd == 0:
                            log(f"Ошибка: неверный курс для {curr.upper()}", lvl="error")
                            continue
                            
                        price = rub_to_usd / curr_to_usd  # Цена в рублях за единицу валюты
                    except (ValueError, AttributeError, TypeError) as e:
                        log(f"Ошибка при обработке курсов для {curr.upper()}: {str(e)}", lvl="error")
                        continue
                
                # Добавляем накрутку 6%
                price_with_markup = price * 1.06
                log(f"Курс {curr.upper()} к RUB: {price} (с накруткой: {price_with_markup})")
                
                # Обновляем лот для текущей валюты
                offer_id = {
                    "kzt": 42111797,
                    "rub": 42111224,
                    "uah": 41456968
                }.get(curr.lower())
                
                if offer_id:
                    try:
                        lot_fields = cardinal.account.get_lot_fields(offer_id)
                        if lot_fields is None:
                            log(f"Ошибка: не удалось получить поля лота {offer_id}", lvl="error")
                            continue
                            
                        print(price_with_markup, "price_with_markup")
                        # Форматируем значения перед установкой
                        formatted_price = str(round(price_with_markup, 2)).replace('.', ',')
                        formatted_amount = str(int(converted_balance))
                        
                        if not hasattr(lot_fields, 'price') or not hasattr(lot_fields, 'amount'):
                            log(f"Ошибка: лот {offer_id} не имеет необходимых атрибутов", lvl="error")
                            continue
                            
                        lot_fields.price = formatted_price
                        lot_fields.amount = formatted_amount
                        
                        cardinal.account.save_lot(lot_fields)
                        log(f"Обновлен лот {offer_id} для валюты {curr.upper()}")
                    except Exception as e:
                        log(f"Ошибка при обновлении лота {offer_id}: {str(e)}", lvl="error")
                        logger.error("TRACEBACK", exc_info=True)
                
            except Exception as e:
                log(f"Ошибка при обновлении лота для валюты {curr}: {str(e)}", lvl="error")
                logger.error("TRACEBACK", exc_info=True)
                
    except Exception as e:
        log(f"Ошибка при обновлении лотов: {str(e)}", lvl="error")
        logger.error("TRACEBACK", exc_info=True)


def _handle_moneyback_steam(c, e):
    """Обработчик команды !возврат"""
    try:
        state = states.get(e.message.author)
        if not state:
            print("DEBUG: No state found for user")
            c.send_message(e.message.chat_id, "❌ Нет активных заказов для возврата.")
            return
            
        order_id = state['data'].get('order_id')
        if not order_id:
            print("DEBUG: No order_id found in state")
            c.send_message(e.message.chat_id, "❌ Нет активных заказов для возврата.")
            return
            
        order = orders.get(order_id)
        if not order:
            print(f"DEBUG: No order found with id {order_id}")
            c.send_message(e.message.chat_id, "❌ Заказ не найден.")
            return
            
        if c.account.get_order(order_id).status in (OrderStatuses.CLOSED, OrderStatuses.REFUNDED):
            print(f"DEBUG: Order {order_id} is already closed or refunded")
            c.send_message(order.chat, f"Заказ #{order.id} уже завершён")
            states.clear(order.buyer)
            return
            
        print(f"DEBUG: Processing refund for order {order_id}")
        c.account.refund(order.id)
        log(f"Покупатель {order.buyer} запросил возврат заказа #{order.id}. Статус заказа был: {order.status}. Деньги вернул, статс изменил на {Os.WAIT_LOGIN}")
        order.edit(status=Os.WAIT_LOGIN)
        _handle_next_order(c, e.message.author)
        
    except Exception as ex:
        print(f"DEBUG: Error in _handle_moneyback_steam: {str(ex)}")
        logger.error(f"Ошибка при обработке команды возврата: {str(ex)}")
        logger.debug("TRACEBACK", exc_info=True)
        c.send_message(e.message.chat_id, "❌ Произошла ошибка при обработке команды возврата. Попробуйте позже.")
