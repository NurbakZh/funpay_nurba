from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

from tg_bot.utils import NotificationTypes
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
from locales.localizer import Localizer
from threading import Thread
from logging import getLogger
import requests
import json
import os
import time

logger = getLogger("FPC.announcements")
localizer = Localizer()
_ = localizer.translate


def get_last_tag() -> str | None:
    """
    Загружает тег последнего объявления из кэша.

    :return: тег последнего объявления или None, если его нет.
    """
    if not os.path.exists("storage/cache/announcement_tag.txt"):
        return None
    with open("storage/cache/announcement_tag.txt", "r", encoding="UTF-8") as f:
        data = f.read()
    return data


REQUESTS_DELAY = 600
LAST_TAG = get_last_tag()


def save_last_tag():
    """
    Сохраняет тег последнего объявления в кэш.
    """
    global LAST_TAG
    if not os.path.exists("storage/cache"):
        os.makedirs("storage/cache")
    with open("storage/cache/announcement_tag.txt", "w", encoding="UTF-8") as f:
        f.write(LAST_TAG)


def get_announcement(ignore_last_tag: bool = False) -> dict | None:
    """
    Получает информацию об объявлении.
    Если тэг объявления совпадает с сохраненным тегом и ignore_last_tag ложь, возвращает None.
    Если произошла ошибка при получении объявлении, возвращает None.

    :return: словарь с данными объявления.
    """
    global LAST_TAG
    headers = {
        'X-GitHub-Api-Version': '2022-11-28',
        'accept': 'application/vnd.github+json'
    }
    try:
        response = requests.get("https://api.github.com/gists/cfd2177869feab9e64ab62918f708389", headers=headers)
        if not response.status_code == 200:
            return None

        content = json.loads(response.json().get("files").get("fpc.json").get("content"))
        if content.get("tag") == LAST_TAG and not ignore_last_tag:
            return None
        return content
    except:
        return None


def download_photo(url: str) -> bytes | None:
    """
    Загружает фото по URL.

    :param url: URL фотографии.

    :return: фотографию в виде массива байтов.
    """
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return None
    except:
        return None
    return response.content


def get_text(data: dict) -> str | None:
    """
    Находит данные о тексте объявления.

    :param data: данные объявления.

    :return: текст объявления или None, если он не найден.
    """
    if not (text := data.get("text")):
        return None
    return u"{}".format(text)


def get_keyboard(data: dict) -> K | None:
    """
    Получает информацию о клавиатуре и генерирует ее.
    Пример клавиатуры:

    :param data: данные объявления.

    :return: объект клавиатуры или None, если данные о ней не найдены.
    """
    if not (kb_data := data.get("kb")):
        return None

    kb = K()
    try:
        for row in kb_data:
            buttons = []
            for btn in row:
                btn_args = {u"{}".format(i): u"{}".format(btn[i]) for i in btn}
                buttons.append(B(**btn_args))
            kb.row(*buttons)
    except:
        return None
    return kb

def announcements_loop(crd: Cardinal):
    """
    Бесконечный цикл получения объявлений.
    """
    if not crd.telegram:
        return


def main(crd: Cardinal):
    Thread(target=announcements_loop, args=(crd,), daemon=True).start()


BIND_TO_POST_INIT = [main]
