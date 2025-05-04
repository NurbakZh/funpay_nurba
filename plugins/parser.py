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

import Utils.config_loader as cfg_loader
import FunPayAPI.types
from FunPayAPI.account import Account
from logging import getLogger
from telebot.types import Message, InlineKeyboardMarkup as K, InlineKeyboardButton as B
from tg_bot import static_keyboards as skb
import os
import time
import json
import pytz
from parser_helper import check_for_last, get_promo_game_link, parse_steam_search, parse_steam_app_page, parse_steam_edition_page, calculate_price_in_rubles, translate_text, parse_steam_currency_page


NAME = "Lots Add Plugin"
VERSION = "0.0.3"
DESCRIPTION = "Данный плагин позволяет добавлять лоты, в зависимости от цены на игры в стиме"
CREDITS = "@nurba_zh"
SETTINGS_PAGE = False
UUID = "f5b3b3b4-0b3b-4b3b-8b3b-0b3b3b3b3b3b"

logger = getLogger("FPC.create_lots_plugin")
RUNNING = False

settings = {
    "background_task": False,
    "with_secrets": False,
    "uah_kzt_rate": 12.6,
    "rub_uah_rate": 2.43,
    "income": {
        "1_100": 5,
        "101_500": 15,
        "501_2000": 25,
        "2001_5000": 35,
        "5001_plus": 45,
    }
}

def get_game_prices(game_name, edition_id = None, kz_uah: bool = False, ru_uah: bool = False, kz_rub: bool = False, ru_kz: bool = False):
    app_url_ua = f"https://store.steampowered.com/app/{game_name}" + f"?cc=ua"
    if edition_id:
        app_details_ua = parse_steam_edition_page(app_url_ua, edition_id)
    else:
        app_details_ua = parse_steam_app_page(app_url_ua)
    name_ua = app_details_ua.get('название', 'Название не найдено')
    price_ua = app_details_ua.get('цена в гривнах', 'Цена не найдена')
    price_ua = float(price_ua.replace('$', '').replace('руб.', '').replace('₸', '').replace('₴', '').replace(' ', '').replace(',', '.').replace('USD', ''))
    price_rub_ua = calculate_price_in_rubles(price_ua, settings["rub_uah_rate"], settings["income"])

    app_url_kz = f"https://store.steampowered.com/app/{game_name}" + f"?cc=kz"
    if edition_id:
        app_details_kz = parse_steam_edition_page(app_url_kz, edition_id)
    else:
        app_details_kz = parse_steam_app_page(app_url_kz)
    price_kz = app_details_kz.get('цена в гривнах', 'Цена не найдена')
    if price_kz is None:
        price_rub_kz = price_rub_ua
    else:
        price_rub_kz = calculate_price_in_rubles(price_kz, float(settings["rub_uah_rate"])/float(settings["uah_kzt_rate"]), settings["income"])
    
    app_url_ru = f"https://store.steampowered.com/app/{game_name}" + f"?cc=ru"
    if edition_id:
        app_details_ru = parse_steam_edition_page(app_url_ru, edition_id)
    else:
        app_details_ru = parse_steam_app_page(app_url_ru)
    price_ru = app_details_ru.get('цена в гривнах', 'Цена не найдена')
    if price_ru is None:
        price_ru = "error"
    else:
        price_ru = calculate_price_in_rubles(price_ru, 1, settings["income"])

    return {
        "price_rub_ua": price_rub_ua,
        "price_rub_kz": price_rub_kz,
        "price_ru": price_ru,
        "name_ua": name_ua
    }

def generate_summary_text(region: str, game_name: str) -> str:
        return f"🔴🟡🔵{region}🔴🟡🔵🎁𝐒𝐓𝐄𝐀𝐌 𝐆𝐈𝐅𝐓🎁🔴🟡🔵{game_name}🔴🟡🔵"

def generate_description_text(region: str, game_name: str) -> str:
    return (
        "❗️ Перед покупкой: напишите о намерении приобрести товар.\n"
        "❗️ Стоимость товара всегда актуальна, даже с учётом скидок в Steam.\n"
        f"❗️ Игра отправляется подарком на ваш Steam-аккаунт в: {region}\n"
        "❗️ Все сделки легальные: игра лицензионная и отправляется напрямую со Steam.\n"
        "❗️ Нужно, чтобы был снят лимит в 5 долларов.\n\n"
        "📌 Порядок покупки:\n"
        "1 Оплатить товар.\n"
        "2 Отправить мне ссылку на ваш профиль Steam.\n"
        "3 Принять заявку в друзья от моего аккаунта.\n"
        "4 Дождаться отправки игры на ваш аккаунт.\n"
        "5 Принять подарок и наслаждаться игрой.\n"
        "❗️❗️❗️ Если я не отвечу вам сразу, подождите — ответ будет, как только я окажусь за компьютером. ❗️❗️❗️\n"
        "❗️❗️❗️ Если нужны другие версии игры (или любые другие игры), пишите в личные сообщения! 😁\n\n"
    )

def save_game_and_lot_names(game_id, funpay_game_name, lot_name, node_id, region, price, kz_uah, kz_rub, ru_uah, ru_kz, server_id):
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
            item.get('game_id') == game_id and 
            item.get('funpay_game_name') == funpay_game_name and
            item.get('lot_name') == lot_name and
            item.get('node_id') == node_id and
            item.get('region') == region), None)

        if existing_entry:
            existing_entry['price'] = price
        else:
            data.append({
                'game_id': game_id, 
                'funpay_game_name': funpay_game_name, 
                'lot_name': lot_name, 
                "node_id": node_id, 
                "region": region, 
                "price": price, 
                "ru_kz": ru_kz, 
                "ru_uah": ru_uah, 
                "kz_uah": kz_uah, 
                "kz_rub": kz_rub,
                "server_id": server_id
            })

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
    
    # Создаем уникальный ключ для каждого лота, чтобы избежать дублирования
    processed_lots = set()
    
    for parent_id, lot_id in all_lots_ids:
        attempts = 15
        while attempts > 0:
            try:
                lot_fields = cardinal.account.get_lots_field(lot_id, parent_id)
                
                # Создаем уникальный ключ для лота на основе lot_id и region
                lot_key = f"{lot_id}_{lot_fields.get('fields[region]', '')}"
                
                # Если этот лот уже был обработан, пропускаем его
                if lot_key in processed_lots:
                    logger.info(f"[LOTS UPDATE] Пропускаю уже обработанный лот {lot_id} для региона {lot_fields.get('fields[region]', '')}")
                    break
                
                # Добавляем в множество обработанных лотов
                processed_lots.add(lot_key)
                
                countryCode = 'ua'
                if lot_fields['fields[region]'] not in ["Россия", "Казахстан", "Украина", "СНГ"]:
                    countryCode = 'us'
                
                if 'server_id' in lot_fields:
                    if lot_fields['server_id'] is not None:
                        saved_lot = next((item for item in saved_data if item['node_id'] == str(lot_id) 
                            and item['server_id'] == lot_fields['server_id'] 
                            and item['region'] == lot_fields['fields[region]']), None)
                else:
                    saved_lot = next((item for item in saved_data if item['node_id'] == str(lot_id)
                        and item['region'] == lot_fields['fields[region]']), None)
                
                if saved_lot:
                    game_id = saved_lot['game_id']
                    funpay_game_name = saved_lot['funpay_game_name']
                    lot_name = saved_lot['lot_name']
                    kz_uah = saved_lot['kz_uah']
                    ru_uah = saved_lot['ru_uah']
                    kz_rub = saved_lot['kz_rub']
                    ru_kz = saved_lot['ru_kz']
                    game_prices = get_game_prices(game_name = game_id, kz_uah = kz_uah, ru_uah = ru_uah, kz_rub = kz_rub, ru_kz = ru_kz)
                    price_for_russia = game_prices["price_rub_ua"] if game_prices["price_ru"] == "error" else game_prices["price_ru"]
                    price_for_kazakhstan = game_prices["price_rub_kz"]
                
                    if lot_fields['fields[region]'] == "Россия":
                        new_price_rub = price_for_russia
                        if ru_uah:
                            new_price_rub = game_prices["price_rub_ua"]
                        elif ru_kz:
                            new_price_rub = price_for_kazakhstan
                    elif lot_fields['fields[region]'] == "Казахстан":
                        new_price_rub = price_for_kazakhstan
                        if kz_uah:
                            new_price_rub = game_prices["price_rub_ua"]
                        elif kz_rub:
                            new_price_rub = price_for_russia
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
                            logger.info(f"[LOTS COPY] Изменил лот {parent_id} для региона {lot_fields['fields[region]']}.")
                            
                            for item in saved_data:
                                if item['node_id'] == str(lot_id) and item['region'] == lot_fields['fields[region]']:
                                    item['price'] = float(new_price_rub)
                            
                            # Записываем обновленные данные в файл после КАЖДОГО обновления цены
                            with open(file_path, 'w', encoding='utf-8') as file:
                                json.dump(saved_data, file, ensure_ascii=False, indent=4)
                                
                            bot.send_message(message.chat.id, f"Лот для региона {lot_fields['fields[region]']} **обновлен**: Игра: {funpay_game_name}, Лот: {lot_name}", parse_mode='Markdown')
                        except Exception as e:
                            print(e)
                            logger.error(f"[LOTS COPY] Не удалось изменить лот {parent_id} для региона {lot_fields['fields[region]']}.")
                            logger.debug("TRACEBACK", exc_info=True)
                            if isinstance(e, FunPayAPI.exceptions.RequestFailedError):
                                logger.debug(e.response.content.decode())
                    else:
                        logger.info(f"[LOTS COPY] Не изменял лот {parent_id} для региона {lot_fields['fields[region]']}.")
                break  # Успешно обработали лот, выходим из цикла попыток
            except FunPayAPI.exceptions.RequestFailedError as e:
                if e.response.status_code == 403 and attempts > 1:
                    logger.warning(f"[LOTS UPDATE] Получена ошибка 403. Осталось попыток: {attempts-1}. Повторная попытка через 5 минут.")
                    attempts -= 1
                    time.sleep(300)  # Ждем 5 минут перед следующей попыткой
                else:
                    logger.error(f"[LOTS UPDATE] Не удалось обработать лот {lot_id}. Ошибка: {str(e)}")
                    logger.debug("TRACEBACK", exc_info=True)
                    break
            except Exception as e:
                logger.error(f"[LOTS UPDATE] Не удалось обработать лот {lot_id}. Ошибка: {str(e)}")
                logger.debug("TRACEBACK", exc_info=True)
                break
            time.sleep(10)

def schedule_task(cardinal, bot, message):
    moscow_tz = pytz.timezone('Europe/Moscow')
    def job():
        now = datetime.now(moscow_tz)
        if now.hour == 21 and now.minute == 10:
            update_lots(cardinal, bot, message)

    schedule.every().minute.do(job)

    while True:
        schedule.run_pending()
        time.sleep(1)

def init_commands(cardinal: Cardinal):
    PARSER_CFG = cfg_loader.load_parser_config("configs/parser.cfg")

    settings.update({
        "rub_uah_rate": float(PARSER_CFG.get("rates", "rub_uah_rate")),
        "uah_kzt_rate": float(PARSER_CFG.get("rates", "uah_kzt_rate")),
        "income": {
            "1_100": int(PARSER_CFG.get("income", "1_100")),
            "101_500": int(PARSER_CFG.get("income", "101_500")),
            "501_2000": int(PARSER_CFG.get("income", "501_2000")),
            "2001_5000": int(PARSER_CFG.get("income", "2001_5000")),
            "5001_plus": int(PARSER_CFG.get("income", "5001_plus")),
        }
    })

    if not cardinal.telegram:
        return
    tg = cardinal.telegram
    bot = cardinal.telegram.bot

    def get_last_email(message: Message):
        last_email = check_for_last()
        if last_email == "нет кода" or last_email == "нет почты":
            bot.send_message(message.chat.id, f"Последнее сообщение из почты с кодом не найдено")
        else:
            bot.send_message(message.chat.id, f"Код из последнего сообщения из почты: {last_email}")

    def start_background_task(cardinal, bot, message):
        task_thread = threading.Thread(target=schedule_task, args=(cardinal, bot, message))
        task_thread.daemon = True
        task_thread.start()

    def handle_start_check(message: Message):
        try:
            settings["background_task"] = True
            start_background_task(cardinal, bot, message)
        except Exception as e:  
            print(e)

    def handle_start_forced_check(message: Message):
        try:
            bot.send_message(message.chat.id, "Начинаю форсированное обновление цен")
            update_lot(cardinal, bot, message)
        except Exception as e:
            print(e)

    def handle_add_edition(message: Message):
        try:
            if settings["rub_uah_rate"] is None or settings["income"] is None:
                bot.send_message(message.chat.id, "Пожалуйста сначала запустите комманду /set_config_price для установки курса валют(курс брать из https://steam-currency.ru/?ref=dtf.ru) и желаемое прибыли в процентах")
                return 
            msg = bot.send_message(message.chat.id, "Введите id игры(для издания) в Steam:")
            bot.register_next_step_handler(msg, process_edition_id_step)
        except Exception as e:  
            print(e)

    def process_edition_id_step(message: Message):
        try:
            edition_id = message.text
            msg = bot.send_message(message.chat.id, "Введите название издания игры в Steam:")
            bot.register_next_step_handler(msg, process_edition_name_step, edition_id)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def process_edition_name_step(message: Message, edition_id):
        try:
            edition_name = message.text
            edition_prices = get_game_prices(game_name = edition_id, edition_id = edition_name)
            bot.send_message(message.chat.id, f"Игра: {edition_prices['name_ua']}\nЦена в гривнах: {edition_prices['price_rub_ua']} руб.\nЦена в тенге: {edition_prices['price_rub_kz']} руб.\nЦена в рублях: {edition_prices['price_ru']} руб.")
            msg = bot.send_message(message.chat.id, "Введите название лота:")
            bot.register_next_step_handler(msg, process_edition_lot_name_step, edition_id, edition_name=edition_prices["name_ua"], price_rub_ua=edition_prices["price_rub_ua"], price_rub_kz=edition_prices["price_rub_kz"], price_ru=edition_prices["price_ru"])
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def process_edition_lot_name_step(message: Message, edition_id, edition_name, price_rub_ua, price_rub_kz, price_ru):
        try:
            lot_name = message.text
            msg = bot.send_message(message.chat.id, "Введите название издания в Funpay:")
            bot.register_next_step_handler(msg, process_edition_russia_step, edition_id, edition_name, price_rub_ua, price_rub_kz, price_ru, lot_name)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def process_edition_russia_step(message: Message, edition_id, edition_name, price_rub_ua, price_rub_kz, price_ru, lot_name):
        try:
            funpay_game_name = message.text
            msg = bot.send_message(message.chat.id, "Добавлять ли лот для России(да/нет):")
            bot.register_next_step_handler(msg, ask_russia_currency_edition, game_id=edition_id, funpay_game_name=funpay_game_name, game_name=edition_name, price_rub_ua=price_rub_ua, price_rub_kz=price_rub_kz, price_ru=price_ru, lot_name=lot_name)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def ask_russia_currency_edition(message: Message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name):
        try:
            is_russia = message.text.lower() == "да"
            if is_russia:
                msg = bot.send_message(message.chat.id, "Выберите валюту для России (UAH/RUB/KZT):")
                bot.register_next_step_handler(msg, ask_for_kazakhstan_edition, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, is_russia)
            else:
                ask_for_kazakhstan_edition(message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, is_russia)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def ask_for_kazakhstan_edition(message: Message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, is_russia):
        try:
            ru_uah = False
            ru_kz = False
            if is_russia:
                ru_currency = message.text.upper()
                ru_uah = ru_currency == "UAH"
                ru_kz = ru_currency == "KZT"

            msg = bot.send_message(message.chat.id, "Добавлять ли лот для Казахстана(да/нет):")
            bot.register_next_step_handler(msg, ask_kazakhstan_currency_edition, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def ask_kazakhstan_currency_edition(message: Message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia):
        try:
            is_kazakhstan = message.text.lower() == "да"
            if is_kazakhstan:
                msg = bot.send_message(message.chat.id, "Выберите валюту для Казахстана (UAH/RUB/KZT):")
                bot.register_next_step_handler(msg, process_description_step, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia, is_kazakhstan, is_edition = True)
            else:
                process_description_step(message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia, is_kazakhstan, is_edition = True)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def handle_add_lot(message: Message):
        try:
            if settings["rub_uah_rate"] is None or settings["income"] is None:
                bot.send_message(message.chat.id, "Пожалуйста сначала запустите комманду /set_config_price для установки курса валют(курс брать из https://steam-currency.ru/?ref=dtf.ru) и желаемое прибыли в процентах")
                return 
            msg = bot.send_message(message.chat.id, "Введите id игры в Steam:")
            bot.register_next_step_handler(msg, process_game_name_step)
        except Exception as e:  
            print(e)

    def process_game_name_step(message: Message):
        try:
            game_id = message.text
            game_prices = get_game_prices(game_name = game_id)
            bot.send_message(message.chat.id, f"Игра: {game_prices['name_ua']}\nЦена в гривнах: {game_prices['price_rub_ua']} руб.\nЦена в тенге: {game_prices['price_rub_kz']} руб.\nЦена в рублях: {game_prices['price_ru']} руб.")
            msg = bot.send_message(message.chat.id, "Введите название лота:")
            bot.register_next_step_handler(msg, process_lot_name_steap, game_id, game_prices["name_ua"], game_prices["price_rub_ua"], game_prices["price_rub_kz"], game_prices["price_ru"])
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def process_lot_name_steap(message: Message, game_id, game_name, price_rub_ua, price_rub_kz, price_ru):
        try:
            lot_name = message.text
            msg = bot.send_message(message.chat.id, "Введите название игры в FunPay:")
            bot.register_next_step_handler(msg, ask_for_russia, game_id, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def ask_for_russia(message: Message, game_id, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name):
        try:
            funpay_game_name = message.text
            msg = bot.send_message(message.chat.id, "Добавлять ли лот для России(да/нет):")
            bot.register_next_step_handler(msg, ask_russia_currency, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def ask_russia_currency(message: Message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name):
        try:
            is_russia = message.text.lower() == "да"
            if is_russia:
                msg = bot.send_message(message.chat.id, "Выберите валюту для России (UAH/RUB/KZT):")
                bot.register_next_step_handler(msg, ask_for_kazakhstan, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, is_russia)
            else:
                ask_for_kazakhstan(message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, is_russia)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def ask_for_kazakhstan(message: Message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, is_russia):
        try:
            ru_uah = False
            ru_kz = False
            if is_russia:
                ru_currency = message.text.upper()
                ru_uah = ru_currency == "UAH"
                ru_kz = ru_currency == "KZT"

            msg = bot.send_message(message.chat.id, "Добавлять ли лот для Казахстана(да/нет):")
            bot.register_next_step_handler(msg, ask_kazakhstan_currency, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def ask_kazakhstan_currency(message: Message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia):
        try:
            is_kazakhstan = message.text.lower() == "да"
            if is_kazakhstan:
                msg = bot.send_message(message.chat.id, "Выберите валюту для Казахстана (UAH/RUB/KZT):")
                bot.register_next_step_handler(msg, process_description_step, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia, is_kazakhstan)
            else:
                process_description_step(message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia, is_kazakhstan)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка: {str(e)}")
            print(f"Error: {str(e)}")

    def process_description_step(message: Message, game_id, funpay_game_name, game_name, price_rub_ua, price_rub_kz, price_ru, lot_name, ru_uah, ru_kz, is_russia, is_kazakhstan, is_edition = False):
        try:
            kz_uah = False
            kz_rub = False
            if is_kazakhstan:
                kz_currency = message.text.upper()
                kz_uah = kz_currency == "UAH"
                kz_rub = kz_currency == "RUB"

            node_id = get_promo_game_link(lot_name)
            if is_edition:
                lot_fields = cardinal.account.get_lots_variants(node_id, edition_name = funpay_game_name)
            else:
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

            regions = []
            prices = []

            if is_russia:
                regions.append("Россия")
                temp_price_ru = price_ru
                if ru_uah:
                    temp_price_ru = price_rub_ua
                elif ru_kz:
                    temp_price_ru = price_rub_kz
                prices.append(temp_price_ru)

            if is_kazakhstan:
                regions.append("Казахстан")
                temp_price_kz = price_rub_kz
                if kz_rub:
                    temp_price_kz = price_ru
                elif kz_uah:
                    temp_price_kz = price_rub_ua
                prices.append(temp_price_kz)

            regions.append("Украина")
            prices.append(price_rub_ua)

            for region, price in zip(regions, prices):
                if region == 'СНГ':
                    game_title = " ".join(game_name.split(" ")[1:]) if game_name.startswith(("Buy ", "Pre-Purchase ", "Купить ", "Предзаказ ")) else game_name
                    summary = generate_summary_text(region, game_title)
                    summary_en = generate_summary_text("CIS countries", game_title)
                else:
                    game_title = " ".join(game_name.split(" ")[1:]) if game_name.startswith(("Buy ", "Pre-Purchase ", "Купить ", "Предзаказ ")) else game_name
                    summary = generate_summary_text(region, game_title)
                    text_en = translate_text(region, "en")
                    summary_en = generate_summary_text(text_en, game_title)
                description = generate_description_text(region, game_title)
                payment_region = "UAH"
                if region == "Казахстан":
                    if not kz_uah and not kz_rub:
                        payment_region = "KZT"
                    elif not kz_uah and kz_rub:
                        payment_region = "RUB"
                elif region == "Россия":
                    if not ru_uah and not ru_kz:
                        payment_region = "RUB"
                    if not ru_uah and ru_kz:
                        payment_region = "KZT"
                payment_msg = (
    "Валюта отправки – " + payment_region + " (информация для продавца)\n"
    "Отправьте ссылку на быстрое приглашение в друзья."
)   
                descr_en = translate_text(description, "en")
                payment_en = translate_text(payment_msg, "en")
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
                    "fields[game]": funpay_game_name,
                    "fields[platform]": suitable_platform_option["value"],
                    "fields[method]": "Подарком",
                    "fields[desc][ru]": description,
                    "fields[desc][en]": descr_en,
                    "fields[region]": region,
                    "fields[region2]": region,
                    "fields[payment_msg][ru]": payment_msg,
                    "fields[payment_msg][en]": payment_en,
                    "secrets": "",
                    "fields[type]": type_of_lot["value"] if type_of_lot else '',
                    "fields[type1]": type_of_lot["value"] if type_of_lot else '',
                    "fields[type2]": type_of_lot["value"] if type_of_lot else '',
                }
                if launcher_s is not None:
                    lot_fields["fields[launcher]"] = launcher_s
                if price is not None:
                    save_game_and_lot_names(game_id, funpay_game_name, lot_name, node_id, region, price, kz_uah, kz_rub, ru_uah, ru_kz, suitable_game_option["value"])
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
    
    def handle_config_background_task(message: Message):
        if settings["background_task"]:
            bot.send_message(message.chat.id, "Ежедневное обновление цен в 9 вечера *включено*", parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, "Ежедневное обновление цен в 9 вечера *выключено*", parse_mode="Markdown")

    def handle_config(message: Message):
        rub_uah_rate = settings.get("rub_uah_rate")
        if rub_uah_rate is None: 
            rub_uah_rate = "не установлено"
        uah_kzt_rate = settings.get("uah_kzt_rate")
        if uah_kzt_rate is None: 
            uah_kzt_rate = "не установлено" 
        
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
            f"*Курс грв/кзт:* {uah_kzt_rate} 💵\n"
            f"*Желаемая прибыль:*\n"
            f"*1 до 100руб:* {income_1_100} 💰\n"
            f"*101 до 500руб:* {income_101_500} 💰\n"
            f"*501 до 2000руб:* {income_501_2000} 💰\n"
            f"*2001 до 5000руб:* {income_2001_5000} 💰\n"
            f"*5001руб+:* {income_5001_plus} 💰",
            parse_mode="Markdown"
        )

    def handle_config_price(message: Message):
        msg = bot.send_message(message.chat.id, "Введите курс грв/руб:")
        bot.register_next_step_handler(msg, process_rub_uah_rate_step)

    def process_rub_uah_rate_step(message: Message):
        try:
            settings["rub_uah_rate"] = float(message.text)
            PARSER_CFG["rates"]["rub_uah_rate"] = message.text
            msg = bot.send_message(message.chat.id, "Введите курс грв/кзт:")
            bot.register_next_step_handler(msg, process_uah_kzt_rate_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            handle_config_price(message)

    def process_uah_kzt_rate_step(message: Message):
        try:
            settings["uah_kzt_rate"] = float(message.text)
            PARSER_CFG["rates"]["uah_kzt_rate"] = message.text
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 1 до 100руб:")
            bot.register_next_step_handler(msg, process_income_1_100_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            handle_config_price(message)

    def process_income_1_100_step(message: Message):
        try:
            settings["income"] = {}
            settings["income"]["1_100"] = float(message.text)
            PARSER_CFG["income"]["1_100"] = message.text
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 101 до 500руб:")
            bot.register_next_step_handler(msg, process_income_101_500_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_101_500_step(message: Message):
        try:
            settings["income"]["101_500"] = float(message.text)
            PARSER_CFG["income"]["101_500"] = message.text
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 501 до 2000руб:")
            bot.register_next_step_handler(msg, process_income_501_2000_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_501_2000_step(message: Message):
        try:
            settings["income"]["501_2000"] = float(message.text)
            PARSER_CFG["income"]["501_2000"] = message.text
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 2001 до 5000руб:")
            bot.register_next_step_handler(msg, process_income_2001_5000_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_2001_5000_step(message: Message):
        try:
            settings["income"]["2001_5000"] = float(message.text)
            PARSER_CFG["income"]["2001_5000"] = message.text
            msg = bot.send_message(message.chat.id, "Выберите прибыль в рублях на цены от 5001руб+:")
            bot.register_next_step_handler(msg, process_income_5001_plus_step)
        except ValueError:
            bot.send_message(message.chat.id, "Неверный ввод. Пожалуйста, введите число.")
            process_income_step(message)

    def process_income_5001_plus_step(message: Message):
        try:
            settings["income"]["5001_plus"] = float(message.text)
            PARSER_CFG["income"]["5001_plus"] = message.text
            cfg_loader.save_parser_config(PARSER_CFG)
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
        ("add_edition", "создает лот на специальное издание игры, которую вы ввели", True),
        ("set_config_price", "конфигурирует курс валюты и желаемую прибыль", True),
        ("get_config_price", "получить информацию об актуальной конфигурации курсов валют", True),
        ("check_background_task", "получить информацию о статусе ежедневной проверки цен", True),
        ("start_forced_check", "начать форсированное обновление цен", True),
        ("start_background_task", "начать ежедневное обновление цен в 9 вечера(ВАЖНО: вызывайте эту комманду только один раз за использование бота)", True),
        ("get_last_email", "получить последний код из почты", True),
    ])

    tg.msg_handler(handle_add_lot, commands=["add_lot"])
    tg.msg_handler(handle_start_forced_check, commands=["start_forced_check"])
    tg.msg_handler(handle_add_edition, commands=["add_edition"])
    tg.msg_handler(handle_config_price, commands=["set_config_price"])
    tg.msg_handler(handle_config_background_task, commands=["check_background_task"])
    tg.msg_handler(handle_config, commands=["get_config_price"])
    tg.msg_handler(get_last_email, commands=["get_last_email"])
    tg.msg_handler(handle_start_check, commands=["start_background_task"])


BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
