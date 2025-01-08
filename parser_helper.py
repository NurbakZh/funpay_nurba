import requests
from bs4 import BeautifulSoup
import cloudscraper
from googletrans import Translator

def translate_text(text, dest_language):
    translator = Translator()
    translation = translator.translate(text, dest=dest_language)
    return translation.text
    
def parse_steam_search(query):
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
            href = href.split('/?')[0] + '?cc=ua'
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
    app_description = soup.find('div', class_='game_description_snippet').text.strip()
    developer = soup.find('div', id='developers_list').find('a').text

    purchase_game_wrapper = soup.find('div', class_='game_area_purchase_game_wrapper')
    price = 'N/A'
    if purchase_game_wrapper:
        price = purchase_game_wrapper.find('div', class_='game_purchase_price')
        if not price:
            price = purchase_game_wrapper.find('div', class_='discount_final_price')
        price = price.text.strip() if price else 'N/A'

    return {
        'название': app_name,
        'описание': app_description,
        'цена в гривнах': price
    }

def calculate_price_in_rubles(price_ua, rate=2.7, income_percentage=10):
    try:
        price_ua = float(price_ua.replace('₴', '').replace(' ', '').replace(',', '.'))
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

if __name__ == "__main__":
    query = input("Введите название игры: ")
    rate = float(input("Введите курс грн/руб: "))
    income_percentage = float(input("Введите желаемый доход в %: "))
    
    app_url = parse_steam_search(query)
    if app_url:
        app_details = parse_steam_app_page(app_url)
        if app_details:
            name = app_details.get('название', 'Название не найдено')
            price = app_details.get('цена в гривнах', 'Цена не найдена')
            print(f"\nНазвание: {name}")
            print(f"Цена: {price}")
            price_rub = calculate_price_in_rubles(price, rate, income_percentage)
            print(f"Цена в рублях: {price_rub}\n")
            description = app_details.get('описание', '')
            if description:
                description_en = translate_text(description, 'en')
                description_ru = translate_text(description, 'ru')
                print(f"Описание на английском: {description_en}\n")
                print(f"Описание на русском: {description_ru}\n")