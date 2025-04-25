from __future__ import annotations

import base64
import json
import logging
import os.path
import random
import re
import traceback
from datetime import datetime
from typing import Optional, TYPE_CHECKING, Union

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


class OrderStatus:
    CLOSED = "closed"
    NO_BALANCE = "no_balance"
    PENDING = "pending"
    REFUND = "refund"


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
        super().__init__(f"Логин {login} не найден")
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
CREDITS = "@arthells"
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
    REFUND = 'REFUND'
    PENDING = 'PENDING'
    WAIT_LOGIN = 'WAIT_LOGIN'
    WAIT_ACCEPT = 'WAIT_ACCEPT'
    ERROR = 'ERROR'
    NO_MONEY = 'NO_MONEY'


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
    # kb.row(B("♻️ Обработать заказы, на которые нет денег", None, _CBT.HANDLE_NO_BALANCE_ORDERS))
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

    def send(chat_id, msg, reply_markup=None, **kwargs):
        return bot.send_message(chat_id, msg, reply_markup=reply_markup, parse_mode="HTML", **kwargs)

    def answer_cb(c: CallbackQuery, msg=None, alert=False):
        return bot.answer_callback_query(c.id, msg, show_alert=alert)

    def _edit_msg(m: Message, text, reply_markup=None, **kwargs):
        return bot.edit_message_text(text, m.chat.id, m.message_id, **kwargs, reply_markup=reply_markup)

    def __handle_state_message(chat_id, user_id, state, handler_message, msg_text, state_data={}, reply_markup=None,
                               cb=None, clear_state_after=True):
        if state not in TG_STATES:
            if clear_state_after:
                def wrapped_handler(m):
                    handler_message(m)
                    tg.clear_state(m.chat.id, m.from_user.id, True)

                tg.msg_handler(wrapped_handler, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, state))
                TG_STATES[state] = wrapped_handler
            else:
                tg.msg_handler(handler_message, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, state))
                TG_STATES[state] = handler_message
        result = bot.send_message(chat_id, msg_text,
                                  reply_markup=reply_markup or K().add(B("🚫 Отменить", None, CBT.CLEAR_STATE)))
        tg.set_state(chat_id, result.message_id, user_id, state, state_data)
        if cb:
            bot.answer_callback_query(cb.id)

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

    cfg = {"login": "agure4ek", "password": "ESXK8nKpus"}
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
⚠️ Логин «{login}» не найден, либо регион аккаунта - не СНГ. Пожалуйста, перепроверьте логин и регион.

Если ваш регион не СНГ - отправьте команду «!возврат» без кавычек"""

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
    # if SETTINGS.min > sub_rum or sub_rum > SETTINGS.max:
    #     if SETTINGS.autoback_bad_amoount:
    #         c.account.refund(e.order.id)
    #     c.send_message(e.order.chat_id, f"Недопустимая сумма пополнения\n\n"
    #                                     f"∟ Минимум: {SETTINGS.min} RUB\n"
    #                                     f"∟ Максимум: {SETTINGS.max} RUB\n\n"
    #                                     f"Пожалуйста, оплатите лот на другую сумму")
    #     if not SETTINGS.autoback_bad_amoount:
    #         tg_logs.bad_amount(e.order.amount, crrncy, e.order.id, e.order.chat_id)
    #     return
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
    except NoBalance:
        if NoBalance in _not_answer_errors: return -3
        c.send_message(chat_id, Texts.no_balance())
        tg_logs.no_balance(order.amount, order.login, order.currency, order.id, order.chat)
        order.edit(status=Os.NO_MONEY)
        return -3
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
    states.clear(order.buyer)
    if _next_order:
        _handle_next_order(c, order.buyer)


def _handle_accept_order(c: 'Cardinal', e: NewMessageEvent):
    _complete_order(c, e.message.chat_id, e.message.author)


def _handle_send_login_user(c: 'Cardinal', e: NewMessageEvent, _login):
    order_id = states.get(e.message.author)['data']['order_id']
    order = orders.get(order_id)
    order.edit(login=_login, status=Os.WAIT_ACCEPT)
    states.set(order.buyer, order.status, {"order_id": order.id})


def _handle_moneyback_steam(c, e):
    state = states.get(e.message.author)
    if not state: return
    order_id = state['data'].get('order_id')
    if not order_id: return
    order = orders.get(order_id)
    if order.status in (Os.CLOSED, Os.REFUND): return
    if c.account.get_order(order_id).status in (OrderStatuses.CLOSED, OrderStatuses.REFUNDED):
        c.send_message(order.chat, f"Заказ #{order.id} уже завершён")
        states.clear(order.buyer)
        return
    c.account.refund(order.id)
    # c.send_message(order.chat, f"Хорошо, я вернул средства за этот заказ")
    log(f"Покупатель {order.buyer} запросил возврат заказа #{order.id}. Статус заказа был: {order.status}. Деньги вернул, статс изменил на {Os.REFUND}")
    order.edit(status=Os.REFUND)
    _handle_next_order(c, e.message.author)


def new_msg(c: 'Cardinal', e: NewMessageEvent):
    if e.message.author == c.account.username: return
    if e.message.text.strip() == "+" and states.check(e.message.author, FpSt.WAIT_ACCEPT):
        return _handle_accept_order(c, e)
    if states.check(e.message.author, (FpSt.LWAIT_LOGIN, FpSt.WAIT_ACCEPT)) and (
    _login := __find_login(e.message.text)):
        return _handle_send_login_user(c, e, _login)
    if e.message.text == "!возврат":
        return _handle_moneyback_steam(c, e)


# =============== обработка возврата ================= #

def new_order_status_changed(c: 'Cardinal', e: OrderStatusChangedEvent):
    if not orders.all(buyer=e.order.buyer_username): return
    if e.order.status not in (OrderStatuses.CLOSED, OrderStatuses.REFUNDED): return
    order = orders.get(e.order.id)
    if not order or order.status in (Os.CLOSED, Os.REFUND): return
    new_status = Os.CLOSED if e.order.status == OrderStatuses.CLOSED else Os.REFUND
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
