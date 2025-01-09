import requests
from bs4 import BeautifulSoup
import cloudscraper
from googletrans import Translator

def get_select_items(node_id):
    url = f"https://funpay.com/lots/offerEdit?node={node_id}"
    response = requests.get(url)
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

def get_promo_game_link(game_title):
    url = "https://funpay.com/"
    response = requests.get(url)
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

def translate_text(text, dest_language):
    translator = Translator()
    translation = translator.translate(text, dest=dest_language)
    return translation.text
    
def parse_steam_search(query, countryCode):
    url = f"https://store.steampowered.com/search/?term={query.replace(' ', '+')}"
    response = requests.get(url)
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
            # Modify the URL to append ?cc=ua
            href = href.split('/?')[0] + f"?cc={countryCode}"
            return href
        else:
            print("No <a> tag found in the search result container.")
            return
    else:
        print("No search result container found.")
        return

def parse_steam_app_page(url):
    headers = {
        'Accept-Language': 'uk-UA,uk;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
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
    app_name = soup.find('div', class_='apphub_AppName').text

    purchase_game_wrapper = soup.find('div', class_='game_area_purchase_game_wrapper')
    price = 'N/A'
    if purchase_game_wrapper:
        price = purchase_game_wrapper.find('div', class_='game_purchase_price')
        if not price:
            price = purchase_game_wrapper.find('div', class_='discount_final_price')
        price = price.text.strip() if price else 'N/A'

    return {
        'название': app_name,
        'цена в гривнах': price
    }

def calculate_price_in_rubles(price_ua, rate=2.7, income_percentage=10):
    try:
        price_ua = float(price_ua.replace('$', '').replace('₴', '').replace(' ', '').replace(',', '.').replace('USD', '')) 
    except ValueError:
        return 'Invalid price format'

    price_rub = price_ua * rate
    income = price_rub * (income_percentage / 100)

    if 40 <= price_ua <= 100:
        commission = 50
    elif 101 <= price_ua <= 500:
        commission = 100
    elif 501 <= price_ua <= 2000:
        commission = 150
    elif 2001 <= price_ua <= 10000:
        commission = 200
    else:
        commission = max(30, price_rub * 0.03)

    total_price_rub = price_rub + income + commission
    return round(total_price_rub, 2)