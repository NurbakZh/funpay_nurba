from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, Optional
import aiohttp
from configs.config import BASE_URL
from Utils.get_token import get_new_token, read_token_from_file


async def get_exchange_rate(from_currency: str, to_currency: str, modifier: float = 1.0) -> Optional[float]:
    """
    Получает курс обмена с учетом модификатора.
    Поддерживает прямые и обратные курсы валют.
    """
    print(f"Запрос курса: {from_currency.upper()} → {to_currency.upper()}, модификатор: {modifier}")
    if from_currency.upper() == to_currency.upper():
        print("Курс равен 1.0 (одинаковые валюты).")
        return 1.0

    file_path = Path("currency_rate.json")
    current_date = datetime.now().strftime("%Y-%m-%d")

    try:
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
            if data.get("date") != current_date:
                print("Дата курсов устарела. Обновление курсов...")
                data = await update_currency_rates()
        else:
            print("Файл currency_rate.json не найден. Запрос новых курсов...")
            data = await update_currency_rates()

        if not data:
            print("Не удалось получить данные о курсах валют.")
            return None

    except Exception as e:
        print(f"Ошибка при работе с файлом currency_rate.json: {e}")
        return None

    try:
        from_currency = from_currency.lower()
        to_currency = to_currency.lower()

        # Проверка прямого курса (from/to)
        direct_key = f"{from_currency}/{to_currency}"
        if direct_key in data:
            rate = float(data[direct_key]) * modifier
            print(f"Прямой курс найден: {direct_key} = {rate}")
            return rate

        # Проверка обратного курса (to/from)
        reverse_key = f"{to_currency}/{from_currency}"
        if reverse_key in data:
            rate = (1 / float(data[reverse_key])) * modifier
            print(f"Обратный курс найден: {reverse_key} = {rate}")
            return rate

        # Если курс через USD доступен, используем его
        from_usd_key = f"{from_currency}/usd"
        to_usd_key = f"{to_currency}/usd"

        from_usd = data.get(from_usd_key)
        to_usd = data.get(to_usd_key)

        if from_usd is not None and to_usd is not None:
            rate = (float(to_usd) / float(from_usd)) * modifier
            print(f"Курс через USD рассчитан: {from_currency} → USD → {to_currency} = {rate}")
            return rate

        print(f"Невозможно вычислить курс для {from_currency.upper()}/{to_currency.upper()}")
        return None

    except Exception as e:
        print(f"Ошибка при обработке курсов: {e}")
        return None


async def update_currency_rates() -> Optional[Dict[str, Any]]:
    """Обновляет курсы валют через API и записывает их в currency_rate.json."""
    token_data = read_token_from_file()
    if token_data is None:
        print("Токен не найден или просрочен. Запрашиваем новый...")
        new_token = await get_new_token()
        if new_token is None:
            return {"status": "error", "message": "Не удалось получить токен."}
        token = new_token
    else:
        token = token_data["access_token"]
    
    url = f"{BASE_URL}/steam/get_currency_rate"
    headers = {"Authorization": f"Bearer {token}"}
    print(f"Запрос новых курсов валют через API: {url}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"Получены новые курсы валют: {data}")
                    with open("currency_rate.json", 'w', encoding='utf-8') as file:
                        json.dump({
                            "date": datetime.now().strftime("%Y-%m-%d"),
                            **data
                        }, file, ensure_ascii=False, indent=2)
                    return data
                else:
                    print(f"Ошибка API {response.status}: {await response.text()}")
                    return None
        except aiohttp.ClientError as e:
            print(f"Ошибка запроса к API: {e}")
            return None


class NoFoundLogin(Exception):
    def __init__(self, login):
        self.login = login
        super().__init__(f"⚠️ Логин не найден, либо регион аккаунта - не СНГ. Пожалуйста, перепроверьте логин и регион.\n\n"+

"Если ваш регион не Россия, Украина, Казахстан - отправьте команду «!возврат» без кавычек\n"+
"Если вы ошиблись логином то отправьте верный логин Steam (Не ник)\n"+

"∟ Узнать логин можно по этой ссылке:\n"+
"https://telegra.ph/Gde-poluchit-Login-Steam-02-01")

async def create_topup_order(amount: float, steam_login: str, custom_id: str) -> dict:
    """Создаёт заказ на пополнение Steam с актуальным токеном."""
    token_data = read_token_from_file()
    if token_data is None:
        print("Токен не найден или просрочен. Запрашиваем новый...")
        new_token = await get_new_token()
        if new_token is None:
            return {"status": "error", "message": "Не удалось получить токен."}
        token = new_token
    else:
        token = token_data["access_token"]
    
    url = f"{BASE_URL}/create_order"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "service_id": 1,  # Пополнение Steam
        "quantity": amount,
        "custom_id": custom_id,
        "data": steam_login
    }
    print(f"Создание заказа: URL={url}, payload={payload}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    order_data = await response.json()
                    print(f"Заказ успешно создан: {order_data}")
                    return {"status": "success", "order_data": order_data}
                elif response.status == 400:
                    error_data = await response.json()
                    if "detail" in error_data and "there is no such login" in error_data["detail"].lower():
                        raise NoFoundLogin(steam_login)
                    return {"status": "error", "message": f"Ошибка 400: {error_data}"}
                elif response.status == 403:
                    print("Токен недействителен. Запрашиваем новый...")
                    new_token = await get_new_token()
                    if new_token:
                        headers["Authorization"] = f"Bearer {new_token}"
                        async with session.post(url, json=payload, headers=headers) as retry_response:
                            if retry_response.status == 200:
                                order_data = await retry_response.json()
                                print(f"Заказ успешно создан с новым токеном: {order_data}")
                                return {"status": "success", "order_data": order_data}
                            else:
                                error_message = await retry_response.text()
                                print(f"Ошибка при повторном создании заказа: {error_message}")
                                return {"status": "error", "message": f"Ошибка {retry_response.status}: {error_message}"}
                    else:
                        return {"status": "error", "message": "Не удалось обновить токен."}
                else:
                    error_message = await response.text()
                    print(f"Ошибка создания заказа: статус {response.status}, сообщение: {error_message}")
                    return {"status": "error", "message": f"Ошибка {response.status}: {error_message}"}
        except NoFoundLogin:
            raise
        except aiohttp.ClientError as e:
            print(f"Ошибка запроса при создании заказа: {e}")
            return {"status": "error", "message": f"Ошибка запроса: {e}"}


async def pay_topup_order(custom_id: str) -> dict:
    """Оплачивает заказ по custom_id с актуальным токеном."""
    token_data = read_token_from_file()
    if token_data is None:
        print("Токен не найден или просрочен. Запрашиваем новый...")
        new_token = await get_new_token()
        if new_token is None:
            return {"status": "error", "message": "Не удалось получить токен."}
        token = new_token
    else:
        token = token_data["access_token"]
    
    url = f"{BASE_URL}/pay_order"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"custom_id": custom_id}
    print(f"Оплата заказа: URL={url}, payload={payload}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    payment_data = await response.json()
                    print(f"Заказ успешно оплачен: {payment_data}")
                    return {"status": "success", "payment_data": payment_data}
                elif response.status == 400:
                    error_data = await response.json()
                    if "detail" in error_data and "there is no such login" in error_data["detail"].lower():
                        return {
                            "status": "error", 
                            "message": f"""
⚠️ Логин не найден, либо регион аккаунта - не СНГ. Пожалуйста, перепроверьте логин и регион.

Если ваш регион не Россия, Украина, Казахстан - отправьте команду «!возврат» без кавычек
Если вы ошиблись логином то отправьте верный логин Steam (Не ник)

∟ Узнать логин можно по этой ссылке:
https://telegra.ph/Gde-poluchit-Login-Steam-02-01""",
                            "allow_refund": True
                        }
                    return {"status": "error", "message": f"Ошибка 400: {error_data}"}
                elif response.status == 403:
                    print("Токен недействителен. Запрашиваем новый...")
                    new_token = await get_new_token()
                    if new_token:
                        headers["Authorization"] = f"Bearer {new_token}"
                        async with session.post(url, json=payload, headers=headers) as retry_response:
                            if retry_response.status == 200:
                                payment_data = await retry_response.json()
                                print(f"Заказ успешно оплачен с новым токеном: {payment_data}")
                                return {"status": "success", "payment_data": payment_data}
                            else:
                                error_message = await retry_response.text()
                                print(f"Ошибка при повторной оплате заказа: {error_message}")
                                return {"status": "error", "message": f"Ошибка {retry_response.status}: {error_message}"}
                    else:
                        return {"status": "error", "message": "Не удалось обновить токен."}
                else:
                    error_message = await response.text()
                    print(f"Ошибка оплаты заказа: статус {response.status}, сообщение: {error_message}")
                    return {"status": "error", "message": f"Ошибка {response.status}: {error_message}"}
        except aiohttp.ClientError as e:
            print(f"Ошибка запроса при оплате заказа: {e}")
            return {"status": "error", "message": f"Ошибка запроса: {e}"}