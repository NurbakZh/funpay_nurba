from __future__ import annotations

from os.path import exists
from typing import TYPE_CHECKING

import random
import telebot
import schedule
import threading
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from cardinal import Cardinal

import FunPayAPI.types
from FunPayAPI.account import Account
from logging import getLogger
from telebot.types import Message, InlineKeyboardMarkup as K, InlineKeyboardButton as B
from tg_bot import static_keyboards as skb
import os
import time
import json
import pytz
from parser_helper import get_promo_game_link, parse_steam_search, parse_steam_app_page, calculate_price_in_rubles, translate_text, parse_steam_currency_page


NAME = "Lots Add Plugin"
VERSION = "0.0.1"
DESCRIPTION = "Данный плагин позволяет добавлять лоты, в зависимости от цены на игры в стиме"
CREDITS = "@nurba_zh"
SETTINGS_PAGE = False
UUID = "f5b3b3b4-0b3b-4b3b-8b3b-0b3b3b3b3b3b"

logger = getLogger("FPC.create_lots_plugin")
RUNNING = False

settings = {
    "with_secrets": False,
    "rub_uah_rate": 2.43,
    "rub_usd_rate": 103.1,
    "uah_kzt_rate_steam_currency": 12.6,
    "uah_en_rate_steam_currency": 42.18,
    "income": {
        "1_100": 5,
        "101_500": 15,
        "501_2000": 25,
        "2001_5000": 35,
        "5001_plus": 45,
    },
    "steamLoginSecureUa": None,
    "steamLoginSecureUs": None,
}

def get_game_prices(game_name):
    prices = parse_steam_currency_page("https://steam-currency.ru/")
    if prices["uah_kzt_rate"] is not None:
        settings["uah_kzt_rate_steam_currency"] = prices["uah_kzt_rate"]
    if prices["uah_en_rate"] is not None:
        settings["uah_en_rate_steam_currency"] = prices["uah_en_rate"]
    app_url_ua = parse_steam_search(game_name, settings.get('steamLoginSecureUa')) + f"?cc=ua"
    app_details_ua = parse_steam_app_page(app_url_ua, settings.get('steamLoginSecureUa'))
    name_ua = app_details_ua.get('название', 'Название не найдено')
    price_ua = app_details_ua.get('цена в гривнах', 'Цена не найдена')
    price_rub_ua = calculate_price_in_rubles(price_ua, settings["rub_uah_rate"], settings["income"])

    app_url_en = parse_steam_search(game_name, settings.get('steamLoginSecureUs')) + f"?cc=us"
    app_details_en = parse_steam_app_page(app_url_en, settings.get('steamLoginSecureUs'))
    price_en = app_details_en.get('цена в гривнах', 'Цена не найдена')
    price_rub_en = calculate_price_in_rubles(price_en, settings["rub_usd_rate"], settings["income"])

    app_url_ge = parse_steam_search(game_name) + f"?cc=ge"
    app_details_ge = parse_steam_app_page(app_url_ge)
    price_ge = app_details_ge.get('цена в гривнах', 'Цена не найдена')
    if price_ge is None:
        price_rub_ge = price_rub_ua
    else:
        price_uah_ge = calculate_price_in_rubles(price_ge, settings["uah_en_rate_steam_currency"], settings["income"])
        price_rub_ge = calculate_price_in_rubles(price_ge, settings["rub_usd_rate"], settings["income"])
        price_ua = float(price_ua.replace('$', '').replace('руб.', '').replace('₸', '').replace('₴', '').replace(' ', '').replace(',', '.').replace('USD', ''))
        if price_ua and price_uah_ge and abs(price_ua - price_uah_ge) / price_uah_ge > 0.15:
            price_rub_ge = price_rub_en

    app_url_kz = parse_steam_search(game_name) + f"?cc=kz"
    app_details_kz = parse_steam_app_page(app_url_kz)
    price_kz = app_details_kz.get('цена в гривнах', 'Цена не найдена')
    if price_kz is None:
        price_rub_kz = price_rub_ua
    else:
        price_uah_kz = calculate_price_in_rubles(price_kz, 1 / settings["uah_kzt_rate_steam_currency"], settings["income"])
        price_rub_kz = calculate_price_in_rubles(price_uah_kz, settings["rub_uah_rate"], settings["income"])
        if price_ua and price_uah_kz and abs(price_ua - price_uah_kz) / price_uah_kz > 0.15:
            price_rub_kz = price_rub_en
            
    app_url_ru = parse_steam_search(game_name) + f"?cc=ru"
    app_details_ru = parse_steam_app_page(app_url_ru)
    price_ru = app_details_ru.get('цена в гривнах')
    if price_ru is None:
        price_ru = price_rub_ua
    else:
        price_ru = float(price_ru.replace('$', '').replace('руб.', '').replace('₸', '').replace('₴', '').replace(' ', '').replace(',', '.').replace('USD', ''))

    return {
        "price_rub_ua": price_rub_ua,
        "price_rub_en": price_rub_en,
        "price_rub_ge": price_rub_ge,
        "price_rub_kz": price_rub_kz,
        "price_ru": price_ru,
        "name_ua": name_ua
    }

def generate_summary_text(region: str, game_name: str) -> str:
    if region == "СНГ":
        return f"🔴🟡🔵СТРАНЫ 𝐂𝐈𝐒🔴🟡🔵🎁𝐒𝐓𝐄𝐀𝐌 𝐆𝐈𝐅𝐓🎁🔴🟡🔵{game_name}🔴🟡🔵"
    else:
        return f"🔴🟡🔵{region}🔴🟡🔵🎁𝐒𝐓𝐄𝐀𝐌 𝐆𝐈𝐅𝐓🎁🔴🟡🔵{game_name}🔴🟡🔵"

def generate_description_text(region: str, game_name: str) -> str:
    if region != "СНГ":
        return (
            "❗️ Перед покупкой: напишите о намерении приобрести товар.\n"
            "❗️ Стоимость товара всегда актуальна, даже с учётом скидок в Steam.\n"
            f"❗️ Игра отправляется подарком на ваш Steam-аккаунт в: {region}\n"
            "❗️ Все сделки легальные: игра лицензионная и отправляется напрямую со Steam.\n\n"
            "📌 Порядок покупки:\n"
            "1 Оплатить товар.\n"
            "2 Отправить мне ссылку на ваш профиль Steam.\n"
            "3 Принять заявку в друзья от моего аккаунта.\n"
            "4 Дождаться отправки игры на ваш аккаунт.\n"
            "5 Принять подарок и наслаждаться игрой.\n"
            "❗️❗️❗️ Если я не отвечу вам сразу, подождите — ответ будет, как только я окажусь за компьютером. ❗️❗️❗️\n"
            "❗️❗️❗️ Если нужны другие версии игры (или любые другие игры), пишите в личные сообщения! 😁\n\n"
        )
    else:
        return (
            "❗️ Перед покупкой: напишите о намерении приобрести товар.\n"
            "❗️ Стоимость товара всегда актуальна, даже с учётом скидок в Steam.\n"
            "❗️ Игра отправляется подарком на ваш Steam-аккаунт в регионы: Армения, Азербайджан, Республика Беларусь, Грузия, Киргизстан, Республика Молдова, Таджикистан, Туркменистан, или Узбекистан.\n"
            "❗️ Все сделки легальные: игра лицензионная и отправляется напрямую со Steam.\n\n"
            "📌 Порядок покупки:\n"
            "1 Оплатить товар.\n"
            "2 Отправить мне ссылку на ваш профиль Steam.\n"
            "3 Принять заявку в друзья от моего аккаунта.\n"
            "4 Дождаться отправки игры на ваш аккаунт.\n"
            "5 Принять подарок и наслаждаться игрой.\n"
            "❗️❗️❗️ Если я не отвечу вам сразу, подождите — ответ будет, как только я окажусь за компьютером. ❗️❗️❗️\n"
            "❗️❗️❗️ Если нужны другие версии игры (или любые другие игры), пишите в личные сообщения! 😁\n\n"
        )

def save_game_and_lot_names(game_name, lot_name, node_id, region, price):
    try:
        storage_dir = os.path.join(os.path.dirname(__file__), '../storage/plugins')
        os.makedirs(storage_dir, exist_ok=True)
        file_path = os.path.join(storage_dir, 'game_lot_names.json')

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            with open(file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        else:
            data = []

        existing_entry = next((item for item in data if 
            item['game_name'] == game_name and 
            item['lot_name'] == lot_name and
            item['node_id'] == node_id and 
            item['region'] == region), None)

        if existing_entry:
            existing_entry['price'] = price
        else:
            data.append({'game_name': game_name, 'lot_name': lot_name, "node_id": node_id, "region": region, "price": price})

        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
    except json.JSONDecodeError as e:
        print(f"An error occurred while parsing the JSON file: {e}")
    except Exception as e:
        print(f"An error occurred while saving game and lot names: {e}")


def generate_random_id():
    first_three_digits = random.randint(100, 999)
    remaining_digits = random.randint(1000000, 9999999)
    return f"{first_three_digits}{remaining_digits}"

def get_children_ids(obj):
    ids = []
    for attr in dir(obj):
        if not attr.startswith("__"):
            value = getattr(obj, attr)
            if attr == "subcategory" and hasattr(value, '__dict__'):
                fullname = getattr(value, 'fullname', None)
                if fullname and "Ключи" in fullname:
                    parent_id = getattr(obj, 'id', 'No ID')
                    child_id = getattr(value, 'id', 'No ID')
                    ids.append((parent_id, child_id))
    return ids

def update_lots(cardinal, bot, message):
    logger.info(f"[LOTS UPDATE] Начал процесс обновления цен.")
    prices = parse_steam_currency_page("https://steam-currency.ru/")
    if prices["uah_kzt_rate"] is not None:
        settings["uah_kzt_rate_steam_currency"] = prices["uah_kzt_rate"]
    if prices["uah_en_rate"] is not None:
        settings["uah_en_rate_steam_currency"] = prices["uah_en_rate"]
    profile = cardinal.account.get_user(cardinal.account.id)
    lots = profile.get_lots()
    all_lots_ids = []
    for lot in lots:
        lots_ids = get_children_ids(lot)
        all_lots_ids.extend(lots_ids)

    storage_dir = os.path.join(os.path.dirname(__file__), '../storage/plugins')
    file_path = os.path.join(storage_dir, 'game_lot_names.json')

    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            saved_data = json.load(file)
    else:
        saved_data = []

    for parent_id, lot_id in all_lots_ids:
        lot_fields = cardinal.account.get_lots_field(lot_id, parent_id)
        countryCode = 'ua'
        if lot_fields['fields[region]'] not in ["Россия", "Казахстан", "Украина", "СНГ"]:
            countryCode = 'us'
        saved_lot = next((item for item in saved_data if item['node_id'] == str(lot_id)), None)
        if saved_lot:
            game_name = saved_lot['game_name']
            lot_name = saved_lot['lot_name']
            game_prices = get_game_prices(game_name)
            price_for_russia = game_prices["price_rub_ua"]
            price_for_kazakhstan = game_prices["price_rub_kz"]
            price_for_cis = game_prices["price_rub_ge"]
        
            if countryCode == 'us':
                new_price_rub = game_prices["price_rub_en"]
            else:
                if lot_fields['fields[region]'] == "Россия":
                    new_price_rub = price_for_russia
                elif lot_fields['fields[region]'] == "Казахстан":
                    new_price_rub = price_for_kazakhstan
                elif lot_fields['fields[region]'] == "СНГ":
                    new_price_rub = price_for_cis
                else:
                    new_price_rub = game_prices["price_rub_ua"]

            if str(new_price_rub) != lot_fields['price']:
                lot_fields['price'] = str(new_price_rub)
                lot_fields['active'] = 'on'
                lot_fields['amount'] = '1000'
                lot = FunPayAPI.types.LotFields(parent_id, lot_fields)
                final_lot_id = lot.lot_id
                fields = lot.fields
                fields["offer_id"] = final_lot_id
                fields["csrf_token"] = cardinal.account.csrf_token
                lot.set_fields(fields)

                try:
                    cardinal.account.save_lot(lot)
                    logger.info(f"[LOTS COPY] Изменил лот {parent_id}.")
                    
                    for item in saved_data:
                        if item['node_id'] == str(lot_id):
                            item['price'] = float(new_price_rub)
                    
                    with open(file_path, 'w', encoding='utf-8') as file:
                        json.dump(saved_data, file, ensure_ascii=False, indent=4)
                except Exception as e:
                    print(e)
                    logger.error(f"[LOTS COPY] Не удалось изменить лот {parent_id}.")
                    logger.debug("TRACEBACK", exc_info=True)
                    if isinstance(e, FunPayAPI.exceptions.RequestFailedError):
                        logger.debug(e.response.content.decode())
                bot.send_message(message.chat.id, f"Лот для региона {lot_fields['fields[region]']} **обновлен**: Игра: {game_name}, Лот: {lot_name}", parse_mode='Markdown')

def schedule_task(cardinal, bot, message):
    moscow_tz = pytz.timezone('Europe/Moscow')
    def job():
        now = datetime.now(moscow_tz)
        if now.hour == 21 and now.minute == 00:
            update_lots(cardinal, bot, message)

    schedule.every().minute.do(job)

    while True:
        schedule.run_pending()
        time.sleep(1)

def init_commands(cardinal: Cardinal):
    if not cardinal.telegram:
        return
    tg = cardinal.telegram
    bot = cardinal.telegram.bot

    def start_background_task(cardinal, bot, message):
        task_thread = threading.Thread(target=schedule_task, args=(cardinal, bot, message))
        task_thread.daemon = True
        task_thread.start()

    def handle_start_check(message: Message):
        try:
            start_background_task(cardinal, bot, message)
        except Exception as e:  
            print(e)

    def handle_add_lot(message: Message):
        try:
            if settings["rub_uah_rate"] is None or settings["rub_usd_rate"] is None or settings["income"] is None:
                bot.send_message(message.chat.id, "Пожалуйста сначала запустите комманду /set_config_price для установки курса валют(курс брать из https://steam-currency.ru/?ref=dtf.ru) и желаемое прибыли в процентах")
                return 
            # if settings["steamLoginSecureUa"] is None or settings["steamLoginSecureUs"] is None:
            #     bot.send_message(message.chat.id, "Пожалуйста сначала запустите комманду /set_config_steam для установки Steam аккаунтов")
            #     return 
            msg = bot.send_message(message.chat.id, "Введите название игры в Steam:")
            bot.register_next_step_handler(msg, process_game_name_step)
        except Exception as e:  
            print(e)

    def process_game_name_step(message: Message):
        try:
            game_name = message.text
            game_prices = get_game_prices(game_name)
            bot.send_message(message.chat.id, f"Игра: {game_prices['name_ua']}\nЦена с долларов: {game_prices['price_rub_en']} руб.\nЦена с гривен: {game_prices['price_rub_ua']} руб.")
            msg = bot.send_message(message.chat.id, "Введите название лота:")
            bot.register_next_step_handler(msg, process_lot_name_steap, game_name, game_prices["price_rub_ua"], game_prices["price_rub_en"], game_prices["price_rub_ge"], game_prices["price_rub_kz"], game_prices["price_ru"])
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def process_lot_name_steap(message: Message, game_name, price_rub_ua, price_rub_en, price_rub_ge, price_rub_kz, price_ru):
        try:
            lot_name = message.text
            msg = bot.send_message(message.chat.id, "Введите название игры в FunPay:")
            bot.register_next_step_handler(msg, process_description_step, game_name, price_rub_ua, price_rub_en, price_rub_ge, price_rub_kz, price_ru, lot_name)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def process_description_step(message: Message, game_name, price_rub_ua, price_rub_en, price_rub_ge, price_rub_kz, price_ru, lot_name):
        try:
            funpay_game_name = message.text
            node_id = get_promo_game_link(lot_name)
            lot_fields = cardinal.account.get_lots_variants(node_id)
            game_options = lot_fields["game_options"]
            platform_options = lot_fields["platform_options"]
            type_of_lot = lot_fields["type_of_lot"]
            side_options = lot_fields["side_options"]
            launcher_s = lot_fields["launcher_s"]

            suitable_game_option = {'value': '', 'text': ''}
            suitable_side_option = {'value': '', 'text': ''}
            suitable_platform_option = {'value': '', 'text': ''}

            if game_options is not None:
                suitable_game_option = next((option for option in game_options if funpay_game_name in option["text"]), None)
                if suitable_game_option is None:
                    suitable_game_option = next((option for option in game_options if option["text"] in ["Steam", "PC", "PC (Steam)"]), None)

            if side_options is not None:
                suitable_side_option = next((option for option in side_options if option["text"] in ["Steam", "PC", "PC (Steam)"]), None)

            if platform_options is not None:
                suitable_platform_option = next((option for option in platform_options if option["text"] in ["Steam", "PC", "PC (Steam)"]), None)
                if not suitable_platform_option:
                    raise Exception(f"No suitable platform option found for 'Steam' or 'PC'")

            price_for_russia = price_rub_ua
            price_for_kazakhstan = price_rub_kz
            price_for_cis = price_rub_ge
            if price_rub_ua and price_ru and abs(price_rub_ua - price_ru) / price_ru > 0.15:
                price_for_russia = price_rub_en
            regions = ["Россия", "Казахстан", "Украина", "СНГ", "Турция", "Аргентина"]
            prices = [price_for_russia, price_for_kazakhstan, price_rub_ua, price_for_cis, price_rub_en, price_rub_en]
           
            for region, price in zip(regions, prices):
                if region == 'СНГ':
                    summary = generate_summary_text(region, game_name)
                    summary_en = generate_summary_text("CIS countries", game_name)
                else:
                    summary = generate_summary_text(region, game_name)
                    summary_en = generate_summary_text(translate_text(region, "en"), game_name)
                description = generate_description_text(region, game_name)
                lot_fields = {
                    "active": "on",
                    "deactivate_after_sale": "",
                    "query": "",
                    "form_created_at": generate_random_id(),
                    "node_id": node_id,
                    "side_id": suitable_side_option["value"],
                    "server_id": suitable_game_option["value"],
                    "location": "",
                    "deleted": "",
                    "fields[summary][ru]": summary,
                    "fields[summary][en]": summary_en,
                    "auto_delivery": "",
                    "price": price,
                    "amount": "1000",
                    "fields[platform]": suitable_platform_option["value"],
                    "fields[method]": "Подарком",
                    "fields[desc][ru]": description,
                    "fields[desc][en]": translate_text(description, "en"),
                    "fields[region]": region,
                    "secrets": "",
                    "fields[type]": type_of_lot["value"] if type_of_lot else '',
                }
                if launcher_s is not None:
                    lot_fields["fields[launcher]"] = launcher_s
                if price is not None:
                    save_game_and_lot_names(funpay_game_name, lot_name, node_id, region, price)
                    lot = FunPayAPI.types.LotFields(0, lot_fields)
                    create_lot(cardinal.account, lot)
                    bot.send_message(message.chat.id, f"Лот для региона {region} создан: Игра: {game_name}, Лот: {lot_name}")
                    logger.info(f"Sent message to chat {message.chat.id} for region {region}, game {game_name}, lot {lot_name}")
                else:
                    bot.send_message(message.chat.id, f"Лот для региона {region} не создан: Игра: {game_name}, поскольку цена не нашлась")

            pl_obj = cardinal.plugins[UUID]
            commands_text_list = []
            for i in pl_obj.commands:
                command_description = pl_obj.commands[i]
                commands_text_list.append(f"/{i} - {command_description}"
                              f"{'' if command_description.endswith('.') else '.'}")

            commands_text = "\n\n".join(commands_text_list)
            text = f"{pl_obj.name}\nСпасибо за использование плагина! Не забудтье ачать ежедневное обновление цен в 9 вечера(ВАЖНО: вызывайте эту комманду только один раз за использование бота). Повторяю вам доступные команды:\n\n{commands_text}"

            bot.send_message(message.chat.id, text)

        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def handle_config_get_steam(message: Message):
        setam_login_ua = settings.get("steamLoginSecureUa")
        if setam_login_ua is None: 
            setam_login_ua = "не установлено"
        setam_login_us = settings.get("steamLoginSecureUs")
        if setam_login_us is None: 
            setam_login_us = "не установлено"
        bot.send_message(
            message.chat.id,
            f"*Текущие токенны аккаунтов в стим:*\n"
            f"*Токен для Украины:* {setam_login_ua}\n"
            f"*Токен для США:* {setam_login_us}\n",
            parse_mode="Markdown"
        )

    def handle_config(message: Message):
        rub_uah_rate = settings.get("rub_uah_rate")
        if rub_uah_rate is None: 
            rub_uah_rate = "не установлено"
        rub_usd_rate = settings.get("rub_usd_rate")
        if rub_usd_rate is None: 
            rub_usd_rate = "не установлено" 
        uah_kzt_rate = settings.get("uah_kzt_rate_steam_currency")
        if uah_kzt_rate is None: 
            uah_kzt_rate = "не установлено" 
        uah_en_rate = settings.get("uah_en_rate_steam_currency")
        if uah_en_rate is None: 
            uah_en_rate = "не установлено" 
        
        income = settings.get("income", {})
        income_1_100 = income.get("1_100")
        if income_1_100 is None:
            income_1_100 = "не установлено"
        income_101_500 = income.get("101_500")
        if income_101_500 is None:
            income_101_500 = "не установлено"
        income_501_2000 = income.get("501_2000")
        if income_501_2000 is None:
            income_501_2000 = "не установлено"
        income_2001_5000 = income.get("2001_5000")
        if income_2001_5000 is None:
            income_2001_5000 = "не установлено"
        income_5001_plus = income.get("5001_plus")
        if income_5001_plus is None:
            income_5001_plus = "не установлено"

        bot.send_message(
            message.chat.id,
            f"*Текущие ваши настройки:*\n"
            f"*Курс грв/руб:* {rub_uah_rate} 💵\n"
            f"*Курс доллар/руб:* {rub_usd_rate} 💵\n"\
            f"*Текущие настройки Steam(обновляется в 21-00 по Мск):*\n"
            f"*Курс грв/кзт:* {uah_kzt_rate} 💵\n"
            f"*Курс доллар/грв:* {uah_en_rate} 💵\n"
            f"*Желаемая прибыль:*\n"
            f"*1 до 100руб:* {income_1_100} 💰\n"
            f"*101 до 500руб:* {income_101_500} 💰\n"
            f"*501 до 2000руб:* {income_501_2000} 💰\n"
            f"*2001 до 5000руб:* {income_2001_5000} 💰\n"
            f"*5001руб+:* {income_5001_plus} 💰",
            parse_mode="Markdown"
        )

    def handle_config_steam(message: Message):
        msg = bot.send_message(message.chat.id, "Введите steamLoginSecure для Украины(берется из Cookies, после входа в аккаунт Steam):")
        bot.register_next_step_handler(msg, process_steam_ua_step)

    def process_steam_ua_step(message: Message):
        settings["steamLoginSecureUa"] = message.text
        msg = bot.send_message(message.chat.id, "Введите steamLoginSecure для США(берется из Cookies, после входа в аккаунт Steam):")
        bot.register_next_step_handler(msg, process_steam_us_step)

    def process_steam_us_step(message: Message):
        settings["steamLoginSecureUs"] = message.text
        bot.send_message(message.chat.id, "Конфигурации Steam сохранены")

    def handle_config_price(message: Message):
        msg = bot.send_message(message.chat.id, "Введите курс грв/руб:")
        bot.register_next_step_handler(msg, process_rub_uah_rate_step)

    def process_rub_uah_rate_step(message: Message):
        try:
            settings["rub_uah_rate"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Введите курс доллар/руб:")
            bot.register_next_step_handler(msg, process_rub_usd_rate_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            handle_config_price(message)

    def process_rub_usd_rate_step(message: Message):
        try:
            settings["rub_usd_rate"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 1 до 100руб:")
            bot.register_next_step_handler(msg, process_income_1_100_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            handle_config_price(message)

    def process_income_1_100_step(message: Message):
        try:
            settings["income"] = {}
            settings["income"]["1_100"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 101 до 500руб:")
            bot.register_next_step_handler(msg, process_income_101_500_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_101_500_step(message: Message):
        try:
            settings["income"]["101_500"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 501 до 2000руб:")
            bot.register_next_step_handler(msg, process_income_501_2000_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_501_2000_step(message: Message):
        try:
            settings["income"]["501_2000"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 2001 до 5000руб:")
            bot.register_next_step_handler(msg, process_income_2001_5000_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_2001_5000_step(message: Message):
        try:
            settings["income"]["2001_5000"] = float(message.text)
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 5001руб+:")
            bot.register_next_step_handler(msg, process_income_5001_plus_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_5001_plus_step(message: Message):
        try:
            settings["income"]["5001_plus"] = float(message.text)
            bot.send_message(message.chat.id, "Конфигурации сохранены")
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def get_lots_info(tg_msg: Message, profile: FunPayAPI.types.UserProfile) -> list[FunPayAPI.types.LotFields]:
        """
        Получает данные о всех лотах (кроме валюты) на текущем аккаунте.

        :param tg_msg: экземпляр Telegram-сообщения-триггера.
        :param profile: экземпляр текущего аккаунта.

        :return: список экземпляров лотов.
        """
        result = []
        for i in profile.get_lots():
            if i.subcategory.type == FunPayAPI.types.SubCategoryTypes.CURRENCY:
                continue
            attempts = 3
            while attempts:
                try:
                    lot_fields = cardinal.account.get_lot_fields(i.id)
                    fields = lot_fields.fields
                    if "secrets" in fields.keys():
                        if not settings.get("with_secrets"):
                            fields["secrets"] = ""
                            del fields["auto_delivery"]
                    result.append(lot_fields)
                    logger.info(f"[LOTS COPY] Получил данные о лоте {i.id}.")
                    break
                except:
                    logger.error(f"[LOTS COPY] Не удалось получить данные о лоте {i.id}.")
                    logger.debug("TRACEBACK", exc_info=True)
                    time.sleep(2)
                    attempts -= 1
            else:
                bot.send_message(tg_msg.chat.id, f"❌ Не удалось получить данные о "
                                                 f"<a href=\"https://funpay.com/lots/offer?id={i.id}\">лоте {i.id}</a>."
                                                 f" Пропускаю.")
                time.sleep(1)
                continue
            time.sleep(0.5)
        return result

    def create_lot(acc: Account, lot: FunPayAPI.types.LotFields):
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
                acc.save_lot(lot)
                logger.info(f"[LOTS COPY] Создал лот {lot_id}.")
                return
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
        ("add_lot", "создает лот на основе игры, которую вы ввели", True),
        ("set_config_price", "конфигурирует курс валюты и желаемую прибыль", True),
        ("set_config_steam", "конфигурация токенов аккаунтов steam", True),
        ("get_config_steam", "получить информацию о токенах аккаунтов steam", True),
        ("get_config_price", "получить информацию об актуальной конфигурации курсов валют", True),
        ("start_background_task", "начать ежедневное обновление цен в 9 вечера(ВАЖНО: вызывайте эту комманду только один раз за использование бота)", True),
    ])

    tg.msg_handler(handle_add_lot, commands=["add_lot"])
    tg.msg_handler(handle_config_price, commands=["set_config_price"])
    tg.msg_handler(handle_config_steam, commands=["set_config_steam"])
    tg.msg_handler(handle_config_get_steam, commands=["get_config_steam"])
    tg.msg_handler(handle_config, commands=["get_config_price"])
    tg.msg_handler(handle_start_check, commands=["start_background_task"])


BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
