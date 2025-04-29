import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import aiohttp
from configs.config import BASE_URL, NS_GIFT_LOGIN, NS_GIFT_PASS

# Настройка логирования для этого модуля
logger = logging.getLogger("FPC.Token")

# Функция чтения токена из файла
def read_token_from_file() -> Optional[dict]:
    """Считывает токен из файла token.json и проверяет его валидность по valid_thru."""
    file_path = Path("token.json")
    if not file_path.exists():
        logger.warning("Файл token.json не найден.")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        valid_thru = data.get("valid_thru")
        if valid_thru is None:
            logger.warning("Поле 'valid_thru' отсутствует в token.json.")
            return None

        current_time = datetime.now().timestamp()
        if current_time > valid_thru:
            logger.info("Токен просрочен.")
            return None
        return data
    except Exception as e:
        logger.error(f"Ошибка при чтении token.json: {e}")
        return None

# Функция сохранения токена в файл
def save_token_to_file(token_data: dict):
    """Сохраняет данные токена в файл token.json."""
    try:
        with open("token.json", 'w', encoding='utf-8') as file:
            json.dump(token_data, file, ensure_ascii=False, indent=2)
        logger.info("Токен успешно сохранен в token.json.")
    except Exception as e:
        logger.error(f"Ошибка при сохранении token.json: {e}")

# Модифицированная функция получения нового токена
async def get_new_token() -> Optional[str]:
    """Запрашивает новый токен, используя учетные данные из конфигурации, и сохраняет его в token.json."""
    url = f"{BASE_URL}/get_token"
    payload = {
        "email": NS_GIFT_LOGIN,
        "password": NS_GIFT_PASS
    }
    logger.info(f"Запрос нового токена: URL={url}, payload={payload}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    # Сохраняем полный объект токена в файл
                    save_token_to_file(data)
                    new_token = data.get("access_token")
                    logger.info(f"Новый токен получен: {new_token}")
                    return new_token
                else:
                    error_message = await response.text()
                    logger.error(f"Ошибка при запросе токена: статус {response.status}, сообщение: {error_message}")
                    return None
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка соединения при получении токена: {e}")
            return None