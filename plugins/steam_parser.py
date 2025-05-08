from __future__ import annotations

from os.path import exists
from typing import TYPE_CHECKING
import random
import telebot
import schedule
import threading
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
import cloudscraper
import os
import time
import json
from dotenv import load_dotenv
import re
from googletrans import Translator
import pytz
from telebot.types import Message, InlineKeyboardMarkup as K, InlineKeyboardButton as B, CallbackQuery
from tg_bot import static_keyboards as skb

if TYPE_CHECKING:
    from cardinal import Cardinal

import Utils.config_loader as cfg_loader
import FunPayAPI.types
from FunPayAPI.account import Account
from logging import getLogger
from telebot.types import Message, InlineKeyboardMarkup as K, InlineKeyboardButton as B
from tg_bot import static_keyboards as skb

load_dotenv()

NAME = "Steam Parser Plugin"
VERSION = "0.0.1"
DESCRIPTION = "Данный плагин позволяет добавлять лоты, в зависимости от цены на игры в стиме"
CREDITS = "@nurba_zh"
SETTINGS_PAGE = False
UUID = "f5b3b3b4-0b3b-4b3b-8b3b-0b3b5b3b3b3b"

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
    },
    "selected_regions": set(),
    "template": "Шаблон не установлен",
    "profit_ranges": {}
} 

def get_select_items(node_id):
    headers = {
        "Accept": "*/*",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
        "Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-requested-with": "XMLHttpRequest"
    }
    url = f"https://funpay.com/lots/offerEdit?node={node_id}"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch the webpage: {response.status_code}")

    soup = BeautifulSoup(response.content, 'html.parser')

    def get_select_options(label_text):
        label = soup.find('label', class_='control-label', string=label_text)
        if not label:
            raise Exception(f"Label with text '{label_text}' not found")
        
        select = label.find_next('select')
        if not select:
            raise Exception(f"Select element for label '{label_text}' not found")
        
        options = [
            {"value": option.get('value'), "text": option.text.strip()}
            for option in select.find_all('option') if option.text.strip()
        ]
        return options

    game_options = get_select_options("Игра")
    platform_options = get_select_options("Платформа")

    return {
        "game_options": game_options,
        "platform_options": platform_options
    }

def parse_steam_currency_page(url):
    response = requests.get(url)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        token_element = soup.find(attrs={"data-token": True})
        if token_element:
            token = token_element['data-token']
        else:
            return None
    else:
        return None

    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
        'Connection': 'keep-alive',
        'Cookie': '_ym_uid=1736742020097962631; _ym_d=1736740220; _ym_visorc=w; _ym_isad=2',
        'Host': 'api.steam-currency.ru',
        'Origin': 'https://steam-currency.ru',
        'Referer': 'https://steam-currency.ru/',
        'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'Sec-Ch-Ua-Mobile': '0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
        'Token': token,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    }
    
    response = requests.get("https://api.steam-currency.ru/currency", headers=headers)
    
    if response.status_code == 200:
        data = response.json().get("data", [])
        data_list = data if isinstance(data, list) else [data]
        return {
            "uah_kzt_rate": next((float(item.get('close_price')) for item in data_list if item.get('currency_pair') == 'UAH:KZT'), None),
            "uah_en_rate": next((float(item.get('close_price')) for item in data_list if item.get('currency_pair') == 'USD:UAH'), None),
        }
    else:
        return None

def get_promo_game_link(game_title):
    headers = {
        "Accept": "*/*",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
        "Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-requested-with": "XMLHttpRequest"
    }
    url = "https://funpay.com/"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch the webpage: {response.status_code}")

    soup = BeautifulSoup(response.content, 'html.parser')
    game_element = soup.find('div', class_='game-title', string=game_title)
    
    if not game_element:
        raise Exception(f"Game title '{game_title}' not found")

    promo_game_item = game_element.find_parent('div', class_='promo-game-item')
    if not promo_game_item:
        raise Exception(f"Promo game item for '{game_title}' not found")

    li_element = promo_game_item.find('li', string=lambda s: s and "Ключи" in s)
    if not li_element:
        raise Exception(f"List item with 'Ключи' not found")

    a_element = li_element.find('a', href=True)
    if not a_element:
        raise Exception(f"Link with 'Ключи' not found")

    href = a_element['href']
    id_after_lots = href.split('/lots/')[-1].split('/')[0]
    return id_after_lots

def get_account_game_link(game_title):
    headers = {
        "Accept": "*/*",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
        "Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-requested-with": "XMLHttpRequest"
    }
    url = "https://funpay.com/"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch the webpage: {response.status_code}")

    soup = BeautifulSoup(response.content, 'html.parser')
    game_element = soup.find('div', class_='game-title', string=game_title)
    if not game_element:
        raise Exception(f"Game title '{game_title}' not found")

    promo_game_item = game_element.find_parent('div', class_='promo-game-item')
    if not promo_game_item:
        raise Exception(f"Promo game item for '{game_title}' not found")

    li_element = promo_game_item.find('li', string=lambda s: s and "Аккаунты" in s)
    if not li_element:
        raise Exception(f"List item with 'Аккаунты' not found")

    a_element = li_element.find('a', href=True)
    if not a_element:
        raise Exception(f"Link with 'Аккаунты' not found")

    href = a_element['href']
    id_after_lots = href.split('/lots/')[-1].split('/')[0]
    return id_after_lots

def translate_text(text, dest_language):
    try:
        translator = Translator()
        result = translator.translate(text, dest=dest_language)
        return result.text
    except Exception as e:
        print(f"Translation error: {str(e)}")
        return text

def parse_steam_search(query, steamLoginSecure = None):
    headers = {
        "Accept": "*/*",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
        "Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-requested-with": "XMLHttpRequest",
    }
    if steamLoginSecure is not None:
        headers["Cookie"] = f'steamLoginSecure={steamLoginSecure}'

    url = f"https://store.steampowered.com/search/?term={query.replace(' ', '+')}"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"Failed to retrieve the page. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    search_result_container = soup.find('div', id='search_resultsRows')

    if search_result_container:
        first_a_tag = search_result_container.find('a')
        if first_a_tag:
            href = first_a_tag.get('href')
            appid = first_a_tag.get('data-ds-appid')
            href = href.split('/?')[0]
            return href
        else:
            print("No <a> tag found in the search result container.")
            return
    else:
        print("No search result container found.")
        return

def parse_steam_app_page(url, steamLoginSecure = None):
    headers = {
        "Accept": "*/*",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
        "Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-requested-with": "XMLHttpRequest",
    }
    if steamLoginSecure is not None and steamLoginSecure != "None":
        headers["Cookie"] = f'steamLoginSecure={steamLoginSecure}'

    cookies = {
        'Steam_Language': 'uk',
        'birthtime': '568022401',
        'lastagecheckage': '1-0-1990'
    }

    response = requests.get(url, headers=headers, cookies=cookies)
    if response.status_code != 200:
        print(f"Failed to retrieve the page. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    app_wrapper = soup.find('div', class_='apphub_AppName')
    app_name = None
    if app_wrapper:
        app_name = app_wrapper.text

    purchase_game_wrappers = soup.find_all('div', class_='game_area_purchase_game_wrapper')
    price = None
    for purchase_game_wrapper in purchase_game_wrappers:
        price = purchase_game_wrapper.find('div', class_='game_purchase_price')
        if not price:
            price = purchase_game_wrapper.find('div', class_='discount_final_price')
        if price:
            price = price.text.strip()
            break 
        
    return {
        'название': app_name,
        'цена в гривнах': price
    }

def parse_steam_edition_page(url, edition_id = None):
    headers = {
        "Accept": "*/*",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
        "Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-requested-with": "XMLHttpRequest",
    }

    cookies = {
        'Steam_Language': 'uk',
        'birthtime': '568022401',
        'lastagecheckage': '1-0-1990'
    }
    response = requests.get(url, headers=headers, cookies=cookies)
    if response.status_code != 200:
        print(f"Failed to retrieve the page. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    app_name = None
    price = None

    purchase_game_wrappers = soup.find_all('div', class_='game_area_purchase_game_wrapper')
    found_edition = False
        
    for wrapper in purchase_game_wrappers:
        edition_title = wrapper.find('h1')
        if edition_title and edition_id:
            # Get raw text content ignoring any child elements
            title_text = ''.join(edition_title.find_all(text=True, recursive=False)).strip()
            if edition_id.lower() in title_text.lower():
                # Found the correct edition wrapper
                app_name = title_text
                # Try to find price within this wrapper
                price_div = wrapper.find('div', class_='game_purchase_price')
                if not price_div:
                    price_div = wrapper.find('div', class_='discount_final_price')
                
                if price_div:
                    price = price_div.text.strip()
                found_edition = True
                break
    

    if not found_edition and purchase_game_wrappers:
        first_wrapper = purchase_game_wrappers[0]
        edition_title = first_wrapper.find('h1')
        if edition_title:
            # Get raw text content ignoring any child elements
            app_name = ''.join(edition_title.find_all(text=True, recursive=False)).strip()
            
        price_div = first_wrapper.find('div', class_='game_purchase_price')
        if not price_div:
            price_div = first_wrapper.find('div', class_='discount_final_price')
        
        if price_div:
            price = price_div.text.strip()
    
    if not app_name:
        app_wrapper = soup.find('div', class_='apphub_AppName')
        if app_wrapper:
            app_name = ''.join(app_wrapper.find_all(text=True, recursive=False)).strip()

    return {
        'название': app_name,
        'цена в гривнах': price
    }

def calculate_price_in_rubles(price_ua, rate=2.7, income={
        "1_100": 0,
        "101_500": 0,
        "501_2000": 0,
        "2001_5000": 0,
        "5001_plus": 0,
    }):
    if price_ua is None:
        return None
    try:
        if not isinstance(price_ua, float):
            price_ua = float(price_ua.replace('$', '').replace('руб.', '').replace('₸', '').replace('₴', '').replace(' ', '').replace(',', '.').replace('USD', '')) 
    except ValueError:
        return 'Invalid price format'

    price_rub = price_ua * rate * 1.03
    comission = 0

    if 1 <= price_rub <= 100:
        commission = income["1_100"]
    elif 101 <= price_rub <= 500:
        commission = income["101_500"]
    elif 501 <= price_rub <= 2000:
        commission = income["501_2000"]
    elif 2001 <= price_rub <= 5000:
        commission = income["2001_5000"]
    else:
        commission = income["5001_plus"]

    total_price_rub = (price_rub + commission)
    return round(total_price_rub, 2)

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
        file_path = os.path.join(storage_dir, 'steam_lots.json')

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
    file_path = os.path.join(storage_dir, 'steam_lots.json')

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

                    payment_region = "UAH"
                    if lot_fields['fields[region]'] == "Казахстан":
                        if not kz_uah and not kz_rub:
                            payment_region = "KZT"
                        elif not kz_uah and kz_rub:
                            payment_region = "RUB"
                    elif lot_fields['fields[region]'] == "Россия":
                        if not ru_uah and not ru_kz:
                            payment_region = "RUB"
                        elif not ru_uah and ru_kz:
                            payment_region = "KZT"

                    payment_msg = (
        "Валюта отправки – " + payment_region + " (информация для продавца)\n"
        "Отправьте ссылку на быстрое приглашение в друзья."
    )   
                    descr_en = translate_text(description, "en")
                    payment_en = translate_text(payment_msg, "en")

                    if str(new_price_rub) != lot_fields['price']:
                        lot_fields['price'] = str(new_price_rub)
                        lot_fields['active'] = 'on'
                        lot_fields['amount'] = '1000'
                        lot_fields["fields[payment_msg][ru]"] = payment_msg
                        lot_fields["fields[payment_msg][en]"] = payment_en
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
            time.sleep(20)

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

def create_region_keyboard(selected_regions=None):
    if selected_regions is None:
        selected_regions = set()
    
    regions = ["Россия", "Казахстан", "Украина", "Турция", "Аргентина", "СНГ"]
    keyboard = K(row_width=2)
    
    for region in regions:
        color = "🟢" if region in selected_regions else "🔴"
        keyboard.add(B(f"{color} {region}", callback_data=f"region_{region}"))
    
    keyboard.add(B("✅ Подтвердить", callback_data="confirm_regions"))
    keyboard.add(B("🔙 Назад", callback_data="back_to_main"))
    return keyboard

def create_template_keyboard():
    keyboard = K(row_width=2)
    keyboard.add(
        B("📝 Редактировать шаблон", callback_data="edit_template"),
        B("👁 Просмотреть шаблон", callback_data="view_template")
    )
    keyboard.add(B("🔙 Назад", callback_data="back_to_main"))
    return keyboard

def create_profit_keyboard():
    keyboard = K(row_width=2)
    keyboard.add(
        B("➕ Добавить диапазон", callback_data="add_profit_range"),
        B("👁 Просмотреть диапазоны", callback_data="view_profit_ranges")
    )
    keyboard.add(B("🔙 Назад", callback_data="back_to_main"))
    return keyboard

def create_currency_keyboard():
    keyboard = K(row_width=2)
    keyboard.add(
        B("📝 Установить вручную", callback_data="set_currency_manual"),
        B("🔄 Получить автоматически", callback_data="get_currency_auto")
    )
    keyboard.add(
        B("👁 Просмотреть курсы", callback_data="view_currency_rates")
    )
    keyboard.add(B("🔙 Назад", callback_data="back_to_main"))
    return keyboard

def create_main_keyboard():
    keyboard = K(row_width=2)
    
    # Configuration section
    keyboard.add(
        B("🌍 Выбрать регионы", callback_data="select_regions"),
        B("📝 Настроить шаблон", callback_data="setup_template")
    )
    
    # Price and profit section
    keyboard.add(
        B("💰 Настройка прибыли", callback_data="setup_profit"),
        B("💱 Курсы валют", callback_data="setup_currency")
    )
    
    # Lot management section
    keyboard.add(
        B("🎮 Выставить лот", callback_data="create_lot"),
        B("⏰ Обновление цен", callback_data="price_update")
    )
    
    # Configuration management
    keyboard.add(
        B("🗑 Удалить конфигурации", callback_data="delete_config")
    )
    
    return keyboard

def handle_region_selection(call: CallbackQuery):
    data = call.data
    if data.startswith("region_"):
        region = data.replace("region_", "")
        bot.answer_callback_query(call.id, f"Выбрана страна: {region}", show_alert=False)
        selected_regions = settings.get("selected_regions", set())
        if region in selected_regions:
            selected_regions.remove(region)
        else:
            selected_regions.add(region)
        settings["selected_regions"] = selected_regions
        bot.edit_message_text(
            "Выберите регионы для создания лотов:\n"
            "🟢 - регион выбран\n"
            "🔴 - регион не выбран",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_region_keyboard(selected_regions)
        )
    elif data == "confirm_regions":
        bot.answer_callback_query(call.id, "Подтвердить выбранные регионы", show_alert=False)
        selected_regions = settings.get("selected_regions", set())
        if not selected_regions:
            bot.answer_callback_query(
                call.id,
                "❌ Выберите хотя бы один регион!",
                show_alert=True
            )
            return
        bot.edit_message_text(
            f"✅ Выбраны регионы: {', '.join(selected_regions)}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )
    elif data == "back_to_main":
        bot.answer_callback_query(call.id, "Возврат в главное меню", show_alert=False)
        bot.edit_message_text(
            "Главное меню",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )

def handle_template_setup(call: CallbackQuery):
    data = call.data
    descriptions = {
        "edit_template": "Редактировать шаблон названия лота.",
        "view_template": "Просмотреть текущий шаблон.",
        "back_to_main": "Возврат в главное меню.",
    }
    if data in descriptions:
        bot.answer_callback_query(call.id, descriptions[data], show_alert=False)
    if data == "edit_template":
        msg = bot.send_message(
            call.message.chat.id,
            "Введите новый шаблон для названия лота.\n"
            "Используйте {game_add} для вставки названия игры.\n"
            "Например: 'Купить {game_add} Steam'"
        )
        bot.register_next_step_handler(msg, process_template)
    elif data == "view_template":
        template = settings.get("template", "Шаблон не установлен")
        bot.edit_message_text(
            f"Текущий шаблон:\n\n{template}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_template_keyboard()
        )
    elif data == "back_to_main":
        bot.edit_message_text(
            "Главное меню",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )

def process_template(message: Message):
    template = message.text.strip()
    if "{game_add}" not in template:
        bot.send_message(
            message.chat.id,
            "❌ Шаблон должен содержать {game_add} для вставки названия игры.\n"
            "Попробуйте еще раз:",
            reply_markup=create_template_keyboard()
        )
        return
    
    settings["template"] = template
    bot.send_message(
        message.chat.id,
        f"✅ Шаблон сохранен:\n\n{template}",
        reply_markup=create_main_keyboard()
    )

def handle_profit_setup(call: CallbackQuery):
    data = call.data
    descriptions = {
        "add_profit_range": "Добавить диапазон прибыли.",
        "view_profit_ranges": "Просмотреть диапазоны прибыли.",
        "back_to_main": "Возврат в главное меню.",
    }
    if data in descriptions:
        bot.answer_callback_query(call.id, descriptions[data], show_alert=False)
    if data == "add_profit_range":
        msg = bot.send_message(
            call.message.chat.id,
            "Введите диапазон цен и прибыль в формате:\n"
            "min-max:profit\n\n"
            "Например:\n"
            "100-500:15\n"
            "500-2000:25"
        )
        bot.register_next_step_handler(msg, process_profit_range)
    elif data == "view_profit_ranges":
        ranges = settings.get("profit_ranges", {})
        if not ranges:
            text = "❌ Диапазоны не установлены"
        else:
            text = "📊 Текущие диапазоны прибыли:\n\n"
            for range_str, profit in ranges.items():
                text += f"{range_str}: {profit} руб.\n"
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_profit_keyboard()
        )
    elif data == "back_to_main":
        bot.edit_message_text(
            "Главное меню",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )

def process_profit_range(message: Message):
    try:
        text = message.text.strip()
        if ":" not in text:
            raise ValueError("Неверный формат")
            
        range_str, profit_str = text.split(":")
        min_price, max_price = map(int, range_str.split("-"))
        profit = int(profit_str)
        
        if min_price >= max_price:
            raise ValueError("Минимальная цена должна быть меньше максимальной")
            
        if profit < 0:
            raise ValueError("Прибыль не может быть отрицательной")
            
        # Check for overlapping ranges
        ranges = settings.get("profit_ranges", {})
        for existing_range in ranges:
            existing_min, existing_max = map(int, existing_range.split("-"))
            if (min_price <= existing_max and max_price >= existing_min):
                raise ValueError(f"Диапазон пересекается с существующим: {existing_range}")
        
        ranges[range_str] = profit
        settings["profit_ranges"] = ranges
        
        bot.send_message(
            message.chat.id,
            f"✅ Диапазон {range_str} с прибылью {profit} руб. добавлен",
            reply_markup=create_main_keyboard()
        )
    except ValueError as e:
        bot.send_message(
            message.chat.id,
            f"❌ Ошибка: {str(e)}\nПопробуйте еще раз:",
            reply_markup=create_profit_keyboard()
        )

def handle_currency_setup(call: CallbackQuery):
    data = call.data
    descriptions = {
        "set_currency_manual": "Установить курсы вручную.",
        "get_currency_auto": "Получить курсы автоматически.",
        "view_currency_rates": "Просмотреть текущие курсы.",
        "back_to_main": "Возврат в главное меню.",
    }
    if data in descriptions:
        bot.answer_callback_query(call.id, descriptions[data], show_alert=False)
    if data == "set_currency_manual":
        msg = bot.send_message(
            call.message.chat.id,
            "Введите курсы валют в формате:\n"
            "USD/UAH:rate\n"
            "UAH/KZT:rate\n\n"
            "Например:\n"
            "USD/UAH:38.5\n"
            "UAH/KZT:12.6"
        )
        bot.register_next_step_handler(msg, process_currency_rates)
    elif data == "get_currency_auto":
        try:
            rates = parse_steam_currency_page("https://steam-currency.ru/")
            if rates:
                settings["uah_kzt_rate"] = rates["uah_kzt_rate"]
                settings["rub_uah_rate"] = rates["uah_en_rate"]
                bot.edit_message_text(
                    "✅ Курсы обновлены автоматически:\n\n"
                    f"USD/UAH: {rates['uah_en_rate']}\n"
                    f"UAH/KZT: {rates['uah_kzt_rate']}",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_currency_keyboard()
                )
            else:
                bot.edit_message_text(
                    "❌ Не удалось получить курсы автоматически",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_currency_keyboard()
                )
        except Exception as e:
            bot.edit_message_text(
                f"❌ Ошибка при получении курсов: {str(e)}",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_currency_keyboard()
            )
    elif data == "view_currency_rates":
        rates = {
            "USD/UAH": settings.get("rub_uah_rate", "Не установлен"),
            "UAH/KZT": settings.get("uah_kzt_rate", "Не установлен")
        }
        text = "📊 Текущие курсы валют:\n\n"
        for pair, rate in rates.items():
            text += f"{pair}: {rate}\n"
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_currency_keyboard()
        )
    elif data == "back_to_main":
        bot.edit_message_text(
            "Главное меню",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )

def process_currency_rates(message: Message):
    try:
        text = message.text.strip()
        rates = {}
        
        for line in text.split("\n"):
            if ":" not in line:
                continue
            pair, rate_str = line.split(":")
            pair = pair.strip()
            rate = float(rate_str.strip())
            
            if rate <= 0:
                raise ValueError(f"Курс для {pair} должен быть положительным")
                
            if pair == "USD/UAH":
                settings["rub_uah_rate"] = rate
            elif pair == "UAH/KZT":
                settings["uah_kzt_rate"] = rate
            else:
                raise ValueError(f"Неизвестная валютная пара: {pair}")
        
        bot.send_message(
            message.chat.id,
            "✅ Курсы валют обновлены:\n\n"
            f"USD/UAH: {settings.get('rub_uah_rate')}\n"
            f"UAH/KZT: {settings.get('uah_kzt_rate')}",
            reply_markup=create_main_keyboard()
        )
    except ValueError as e:
        bot.send_message(
            message.chat.id,
            f"❌ Ошибка: {str(e)}\nПопробуйте еще раз:",
            reply_markup=create_currency_keyboard()
        )

def create_price_update_keyboard():
    keyboard = K(row_width=2)
    keyboard.add(
        B("🔄 Обновить сейчас", callback_data="manual_update"),
        B("⏰ Автообновление", callback_data="auto_update")
    )
    keyboard.add(B("🔙 Назад", callback_data="back_to_main"))
    return keyboard

def handle_price_update(call: CallbackQuery):
    data = call.data
    descriptions = {
        "manual_update": "Обновить цены вручную.",
        "auto_update": "Включить/выключить автообновление цен.",
        "back_to_main": "Возврат в главное меню.",
    }
    if data in descriptions:
        bot.answer_callback_query(call.id, descriptions[data], show_alert=False)
    if data == "manual_update":
        bot.edit_message_text(
            "🔄 Начинаю обновление цен...",
            call.message.chat.id,
            call.message.message_id
        )
        update_lots(cardinal, bot, call.message)
    elif data == "auto_update":
        if settings.get("background_task", False):
            settings["background_task"] = False
            bot.edit_message_text(
                "⏹ Автообновление цен остановлено",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_price_update_keyboard()
            )
        else:
            settings["background_task"] = True
            bot.edit_message_text(
                "▶️ Автообновление цен запущено\n"
                "Цены будут обновляться автоматически каждые 30 минут",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_price_update_keyboard()
            )
            # Start background task
            threading.Thread(target=background_price_update, args=(cardinal, bot), daemon=True).start()
    elif data == "back_to_main":
        bot.edit_message_text(
            "Главное меню",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )

def background_price_update(cardinal, bot):
    while settings.get("background_task", False):
        try:
            # Get all active lots
            lots = get_active_lots()
            if not lots:
                time.sleep(1800)  # Sleep for 30 minutes
                continue
                
            # Update prices for each lot
            for lot in lots:
                try:
                    game_prices = get_game_prices(lot["game_name"])
                    if not game_prices:
                        continue
                        
                    # Calculate new price based on region and profit ranges
                    if lot["region"] == "Россия":
                        price = game_prices["price_ru"]
                    elif lot["region"] == "Казахстан":
                        price = game_prices["price_rub_kz"]
                    else:
                        price = game_prices["price_rub_ua"]
                        
                    # Apply profit
                    profit_ranges = settings.get("profit_ranges", {})
                    for range_str, profit in profit_ranges.items():
                        min_price, max_price = map(int, range_str.split("-"))
                        if min_price <= price <= max_price:
                            price += profit
                            break
                            
                    # Update lot price
                    update_lot_price(cardinal.account, lot["node_id"], str(price))
                    
                except Exception as e:
                    logger.error(f"Error updating lot {lot['node_id']}: {str(e)}")
                    
            time.sleep(1800)  # Sleep for 30 minutes
            
        except Exception as e:
            logger.error(f"Error in background price update: {str(e)}")
            time.sleep(300)  # Sleep for 5 minutes on error

def create_delete_config_keyboard():
    keyboard = K(row_width=2)
    keyboard.add(
        B("🗑 Удалить все", callback_data="delete_all"),
        B("❌ Отмена", callback_data="cancel_delete")
    )
    return keyboard

def handle_delete_config(call: CallbackQuery):
    data = call.data
    descriptions = {
        "delete_all": "Удалить все настройки.",
        "cancel_delete": "Отмена удаления.",
    }
    if data in descriptions:
        bot.answer_callback_query(call.id, descriptions[data], show_alert=False)
    if data == "delete_all":
        bot.edit_message_text(
            "⚠️ Вы уверены, что хотите удалить все настройки?\n"
            "Это действие нельзя отменить!",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_delete_config_keyboard()
        )
    elif data == "cancel_delete":
        bot.edit_message_text(
            "Главное меню",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )

def handle_delete_all(call: CallbackQuery):
    bot.answer_callback_query(call.id, "Все настройки удалены.", show_alert=False)
    # Clear all settings
    settings.clear()
    settings.update({
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
        },
        "selected_regions": set(),
        "template": "Шаблон не установлен",
        "profit_ranges": {}
    })
    
    # Delete steam_lots.json
    storage_dir = os.path.join(os.path.dirname(__file__), '../storage/plugins')
    file_path = os.path.join(storage_dir, 'steam_lots.json')
    if os.path.exists(file_path):
        os.remove(file_path)
    
    bot.edit_message_text(
        "✅ Все настройки удалены",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_main_keyboard()
    )

def handle_start(message: Message):
    bot.send_message(
        message.chat.id,
        "Добро пожаловать в бот управления лотами!\n\n"
        "Выберите действие из меню ниже:",
        reply_markup=create_main_keyboard()
    )

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
            update_lots(cardinal, bot, message)
        except Exception as e:
            print(e)

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

    def handle_start(message: Message):
        bot.send_message(
            message.chat.id,
            "Добро пожаловать в бот управления лотами!\n\n"
            "Выберите действие из меню ниже:",
            reply_markup=create_main_keyboard()
        )

    # Удаляем регистрацию всех команд, кроме /steam_parser_commands
    cardinal.add_telegram_commands(UUID, [
        ("steam_parser_commands", "показать все возможные кнопки и их описание", True),
    ])

    # Удаляем все msg_handler, кроме handle_steam_parser_commands
    def handle_steam_parser_commands(message: Message):
        text = (
            "\u2728 <b>Доступные кнопки и их назначение:</b>\n\n"
            "<b>🌍 Выбрать регионы</b> — выбор стран для создания лотов.\n"
            "<b>📝 Настроить шаблон</b> — настройка шаблона названия лота.\n"
            "<b>💰 Настройка прибыли</b> — установка диапазонов прибыли.\n"
            "<b>💱 Курсы валют</b> — настройка или просмотр курсов валют.\n"
            "<b>🎮 Выставить лот</b> — создание лота по выбранным параметрам.\n"
            "<b>⏰ Обновление цен</b> — ручное или автоматическое обновление цен.\n"
            "<b>🗑 Удалить конфигурации</b> — сброс всех настроек.\n"
            "\nВсе действия доступны только через кнопки в меню бота!"
        )
        bot.send_message(
            message.chat.id,
            text,
            parse_mode="HTML",
            reply_markup=create_main_keyboard()
        )

    tg.msg_handler(handle_steam_parser_commands, commands=["steam_parser_commands"])

    def handle_main_menu_callback(call: CallbackQuery):
        descriptions = {
            "select_regions": "Выбор стран для создания лотов.",
            "setup_template": "Настройка шаблона названия лота.",
            "setup_profit": "Установка диапазонов прибыли.",
            "setup_currency": "Настройка или просмотр курсов валют.",
            "create_lot": "Создание лота по выбранным параметрам.",
            "price_update": "Ручное или автоматическое обновление цен.",
            "delete_config": "Сброс всех настроек.",
        }
        if call.data in descriptions:
            bot.answer_callback_query(call.id, descriptions[call.data], show_alert=False)
        # Основное действие обязательно выполняется!
        if call.data == "select_regions":
            bot.edit_message_text(
                "Выберите регионы для создания лотов:\n"
                "🟢 - регион выбран\n"
                "🔴 - регион не выбран",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_region_keyboard(settings.get("selected_regions", set()))
            )
        elif call.data == "setup_template":
            bot.edit_message_text(
                "Настройка шаблона:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_template_keyboard()
            )
        elif call.data == "setup_profit":
            bot.edit_message_text(
                "Настройка прибыли:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_profit_keyboard()
            )
        elif call.data == "setup_currency":
            bot.edit_message_text(
                "Настройка курсов валют:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_currency_keyboard()
            )
        elif call.data == "create_lot":
            bot.edit_message_text(
                "Выберите тип лота:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_lot_keyboard()
            )
        elif call.data == "price_update":
            bot.edit_message_text(
                "Обновление цен:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_price_update_keyboard()
            )
        elif call.data == "delete_config":
            bot.edit_message_text(
                "Удаление конфигураций:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_delete_config_keyboard()
            )

    tg.cbq_handler(handle_main_menu_callback, func=lambda call: call.data in [
        "select_regions", "setup_template", "setup_profit", "setup_currency", "create_lot", "price_update", "delete_config"
    ])

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None 

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

def handle_add_lot(message: Message):
    msg = bot.send_message(
        message.chat.id,
        "Введите название игры в Steam:"
    )
    bot.register_next_step_handler(msg, process_game_name)

def handle_add_edition(message: Message):
    msg = bot.send_message(
        message.chat.id,
        "Введите название игры и издания в формате:\n"
        "game_name:edition_name\n\n"
        "Например:\n"
        "Cyberpunk 2077:Ultimate Edition"
    )
    bot.register_next_step_handler(msg, process_edition_name)

def handle_auto_update(call: CallbackQuery):
    if settings.get("background_task", False):
        settings["background_task"] = False
        bot.edit_message_text(
            "⏹ Автообновление цен остановлено",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_price_update_keyboard()
        )
    else:
        settings["background_task"] = True
        bot.edit_message_text(
            "▶️ Автообновление цен запущено\n"
            "Цены будут обновляться автоматически каждые 30 минут",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_price_update_keyboard()
        )
        # Start background task
        threading.Thread(target=background_price_update, args=(cardinal, bot), daemon=True).start()

def handle_manual_update(call: CallbackQuery):
    bot.edit_message_text(
        "🔄 Начинаю обновление цен...",
        call.message.chat.id,
        call.message.message_id
    )
    update_lots(cardinal, bot, call.message)

def handle_lot_creation(call: CallbackQuery):
    data = call.data
    descriptions = {
        "create_regular_lot": "Создать обычный лот.",
        "create_edition_lot": "Создать лот для издания.",
        "back_to_main": "Возврат в главное меню.",
    }
    if data in descriptions:
        bot.answer_callback_query(call.id, descriptions[data], show_alert=False)
    if data == "create_regular_lot":
        msg = bot.send_message(
            call.message.chat.id,
            "Введите название игры в Steam:"
        )
        bot.register_next_step_handler(msg, process_game_name)
    elif data == "create_edition_lot":
        msg = bot.send_message(
            call.message.chat.id,
            "Введите название игры и издания в формате:\n"
            "game_name:edition_name\n\n"
            "Например:\n"
            "Cyberpunk 2077:Ultimate Edition"
        )
        bot.register_next_step_handler(msg, process_edition_name)
    elif data == "back_to_main":
        bot.edit_message_text(
            "Главное меню",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_keyboard()
        )

def handle_cancel_delete(call: CallbackQuery):
    bot.answer_callback_query(call.id, "Возврат в главное меню.", show_alert=False)
    bot.edit_message_text(
        "Главное меню",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_main_keyboard()
    ) 


def create_region_keyboard(selected_regions: dict) -> K:
    """
    Creates a keyboard for region selection with color indicators
    """
    kb = K()
    regions = {
        "ru": "Россия",
        "kz": "Казахстан",
        "ua": "Украина",
        "tr": "Турция",
        "ar": "Аргентина",
        "cis": "СНГ"
    }
    
    for region_code, region_name in regions.items():
        color = "🟢" if selected_regions.get(region_code, False) else "🔴"
        kb.add(B(f"{color} {region_name}", callback_data=f"region:{region_code}"))
    
    kb.add(B("Назад", callback_data="main_menu"))
    return kb

def create_template_keyboard() -> K:
    """
    Creates a keyboard for template management
    """
    kb = K()
    kb.add(B("📝 Заголовок", callback_data="template:title"))
    kb.add(B("📄 Описание", callback_data="template:description"))
    kb.add(B("💬 Сообщение после покупки", callback_data="template:post_purchase"))
    kb.add(B("Назад", callback_data="main_menu"))
    return kb

def create_profit_keyboard() -> K:
    """
    Creates a keyboard for profit range management
    """
    kb = K()
    kb.add(B("➕ Добавить диапазон", callback_data="profit:add"))
    kb.add(B("📋 Просмотр диапазонов", callback_data="profit:view"))
    kb.add(B("Назад", callback_data="main_menu"))
    return kb

def create_currency_keyboard() -> K:
    """
    Creates a keyboard for currency rate management
    """
    kb = K()
    kb.add(B("💵 Курс доллара", callback_data="currency:usd"))
    kb.add(B("₴ Курс гривны", callback_data="currency:uah"))
    kb.add(B("₽ Курс рубля", callback_data="currency:rub"))
    kb.add(B("₸ Курс тенге", callback_data="currency:kzt"))
    kb.add(B("Назад", callback_data="main_menu"))
    return kb

def create_main_keyboard() -> K:
    """
    Creates the main menu keyboard
    """
    kb = K()
    kb.add(B("🌍 Выбор регионов", callback_data="menu:regions"))
    kb.add(B("📝 Шаблоны", callback_data="menu:templates"))
    kb.add(B("💰 Настройка прибыли", callback_data="menu:profit"))
    kb.add(B("💱 Курсы валют", callback_data="menu:currency"))
    kb.add(B("🎮 Создать лот", callback_data="menu:create_lot"))
    kb.add(B("🔄 Обновление цен", callback_data="menu:price_update"))
    kb.add(B("🗑 Удалить конфигурации", callback_data="menu:delete_config"))
    return kb 