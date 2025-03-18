import requests
from bs4 import BeautifulSoup
import cloudscraper
import os
import imaplib
import email
import time
import json
import base64
from dotenv import load_dotenv
import re

load_dotenv()

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
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": dest_language,
        "dt": "t",
        "q": text
    }
    
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            result = response.json()
            translated_text = ''.join([item[0] for item in result[0] if item[0]])
            return translated_text
        return text
    except:
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

def parse_steam_edition_page(url, steamLoginSecure = None, edition_id = None):
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

    total_price_rub = price_rub + commission
    return round(total_price_rub, 2)

def check_for_last():
    EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
    EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

    if EMAIL_ACCOUNT is None or EMAIL_PASSWORD is None:
        print("Environment variables EMAIL_ACCOUNT and/or EMAIL_PASSWORD are not set.")
        return

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("inbox")

    term = u"проверочный код Rockstar Games".encode('utf-8')
    term2 = u"Rockstar Games verification code".encode('utf-8')

    # Using UTF-8 encoded query for non-ASCII characters
    # query = '(FROM "noreply@github.com") (OR SUBJECT "%s" SUBJECT "%s")' % (
    #     u"verification code".encode('utf-8'),
    #     u"verify your device".encode('utf-8')
    # )
    mail.literal = term
    result, data = mail.search("utf-8", 'SUBJECT')
    mail.literal = term2
    result2, data2 = mail.search("utf-8", 'SUBJECT')

    if result != "OK" and result2 != "OK":
        print("No emails found matching the criteria.")
        return

    email_ids = data[0].split() if data[0] else []
    email_ids2 = data2[0].split() if data2[0] else []

    if not email_ids and not email_ids2:
        print("No emails found matching the criteria")
        mail.logout()
        return

    # Get the last email from each search result
    latest_emails = []
    
    if email_ids:
        last_email_id = email_ids[-1]
        result, msg_data = mail.fetch(last_email_id, "(RFC822)")
        email_msg = email.message_from_bytes(msg_data[0][1])
        latest_emails.append((email_msg, email.utils.parsedate_to_datetime(email_msg['Date'])))

    if email_ids2:
        last_email_id2 = email_ids2[-1]
        result2, msg_data2 = mail.fetch(last_email_id2, "(RFC822)")
        email_msg2 = email.message_from_bytes(msg_data2[0][1])
        latest_emails.append((email_msg2, email.utils.parsedate_to_datetime(email_msg2['Date'])))

    # Sort emails by date and get the most recent one
    latest_emails.sort(key=lambda x: x[1], reverse=True)
    if not latest_emails:
        mail.logout()
        return

    most_recent_msg = latest_emails[0][0]

    # Process the most recent email
    for part in most_recent_msg.walk():
        if part.get_content_type() == "text/plain":
            body = part.get_payload(decode=True)
            try:
                body = body.decode()
            except UnicodeDecodeError:
                body = base64.b64decode(body).decode()
            
            # Extract the verification code
            code_match = re.search(r'\b\d{6}\b', body)
            if code_match:
                verification_code = code_match.group(0)
                mail.logout()
                return verification_code
            
            mail.logout()
            return "noCodeFound"

    mail.logout()
    return "noCodeFound"

def check_for_last_with_account(account: str):
    # Load accounts from JSON file
    with open('storage/plugins/accounts.json', 'r') as f:
        accounts = json.load(f)
    
    # Find account details
    account_details = next((acc for acc in accounts if acc['login'] == account), None)
    if not account_details:
        print(f"Account {account} not found in accounts.json")
        return
        
    EMAIL_ACCOUNT = account_details.get('email')
    EMAIL_PASSWORD = account_details.get('email_password')

    if EMAIL_ACCOUNT is None or EMAIL_PASSWORD is None:
        print("Email credentials not found for account.")
        return

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("inbox")

    term = u"проверочный код Rockstar Games".encode('utf-8')
    term2 = u"Rockstar Games verification code".encode('utf-8')

    mail.literal = term
    result, data = mail.search("utf-8", 'SUBJECT')
    mail.literal = term2
    result2, data2 = mail.search("utf-8", 'SUBJECT')

    if result != "OK" and result2 != "OK":
        print("No emails found matching the criteria.")
        return

    email_ids = data[0].split() if data[0] else []
    email_ids2 = data2[0].split() if data2[0] else []

    if not email_ids and not email_ids2:
        print("No emails found matching the criteria")
        mail.logout()
        return

    # Get the last email from each search result
    latest_emails = []
    
    if email_ids:
        last_email_id = email_ids[-1]
        result, msg_data = mail.fetch(last_email_id, "(RFC822)")
        email_msg = email.message_from_bytes(msg_data[0][1])
        latest_emails.append((email_msg, email.utils.parsedate_to_datetime(email_msg['Date'])))

    if email_ids2:
        last_email_id2 = email_ids2[-1]
        result2, msg_data2 = mail.fetch(last_email_id2, "(RFC822)")
        email_msg2 = email.message_from_bytes(msg_data2[0][1])
        latest_emails.append((email_msg2, email.utils.parsedate_to_datetime(email_msg2['Date'])))

    # Sort emails by date and get the most recent one
    latest_emails.sort(key=lambda x: x[1], reverse=True)
    if not latest_emails:
        mail.logout()
        return

    most_recent_msg = latest_emails[0][0]

    # Process the most recent email
    for part in most_recent_msg.walk():
        if part.get_content_type() == "text/plain":
            body = part.get_payload(decode=True)
            try:
                body = body.decode()
            except UnicodeDecodeError:
                body = base64.b64decode(body).decode()
            
            # Extract the verification code
            code_match = re.search(r'\b\d{6}\b', body)
            if code_match:
                verification_code = code_match.group(0)
                mail.logout()
                return verification_code
            
            mail.logout()
            return "noCodeFound"

    mail.logout()
    return "noCodeFound"

#if __name__ == "__main__":
#   main()
