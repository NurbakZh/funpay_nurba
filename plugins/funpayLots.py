from __future__ import annotations

import json
import requests
from bs4 import BeautifulSoup
import schedule
import time
import threading
import pytz
from datetime import datetime
from logging import getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "Lots Data Plugin"
VERSION = "0.0.1"
DESCRIPTION = "Данный плагин позволяет отслеживать изменения в списке игр на FunPay"
CREDITS = "@nurba_zh"
SETTINGS_PAGE = False
UUID = "f5b3b3b4-0b3b-4b3b-8b3b-0b3b3b3b3b4b"

logger = getLogger("FPC.lots_data_plugin")
RUNNING = False

def fetch_filters_data(url, headers):
    """Fetches filter data from a specific page."""
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        filters_data = {}
        
        # Find showcase-filters div
        showcase_filters = soup.find('div', class_='showcase-filters')
        if showcase_filters:
            # Find all form-group lot-field divs
            form_groups = showcase_filters.find_all('div', class_='form-group lot-field')
            
            for group in form_groups:
                data_id = group.get('data-id')
                if not data_id:
                    continue
                
                # Get all options and buttons
                options = group.find_all('option')
                buttons = group.find_all('button')
                
                values = []
                # Extract text from options
                for option in options:
                    if option.text.strip():
                        values.append(option.text.strip())
                
                # Extract text from buttons
                for button in buttons:
                    if button.text.strip():
                        values.append(button.text.strip())
                
                if values:
                    filters_data[data_id] = values
                    
        return filters_data
    except requests.RequestException as e:
        logger.error(f"Error fetching filters data from {url}: {str(e)}")
        return None

def fetch_game_data(cardinal: Cardinal, chat_id=None, batch_size=50, delay_seconds=30):
    """Получает данные об играх с сайта FunPay с разбивкой на партии."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
    }
    url = "https://funpay.com/"
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        games_data = load_game_data()  # Load existing data at start
        
        # If it's first run and no data exists
        if not games_data:
            cardinal.telegram.bot.send_message(
                chat_id,
                "📝 Первый запуск: создание базы данных игр..."
            )

        # Find all promo-game-items
        promo_games = soup.find_all('div', class_='promo-game-item')
        total_batches = (len(promo_games) + batch_size - 1) // batch_size
        
        # Process games in batches
        for batch_num, i in enumerate(range(0, len(promo_games), batch_size), 1):
            batch = promo_games[i:i + batch_size]
            batch_data = {}
            
            if chat_id:
                cardinal.telegram.bot.send_message(
                    chat_id,
                    f"📊 Обработка партии {batch_num}/{total_batches} ({len(batch)} элементов)..."
                )
            
            for game in batch:
                game_title_elem = game.find('div', class_='game-title')
                if not game_title_elem:
                    continue
                    
                game_title = game_title_elem.text.strip()
                
                # Get all child items from the list
                child_items = []
                list_items = game.find_all('li')
                for li in list_items:
                    link = li.find('a')
                    if link:
                        full_link = f"https://funpay.com{link.get('href', '')}" if link.get('href', '').startswith('/') else link.get('href', '')
                        filters = fetch_filters_data(full_link, headers)
                        child_items.append({
                            'name': link.text.strip(),
                            'link': full_link,
                            'filters': filters or {}
                        })
                
                batch_data[game_title] = child_items
            
            if not games_data:
                # First run - just save the data without comparison
                games_data.update(batch_data)  # Changed from dict merge to update
                save_game_data(games_data)
                if chat_id:  # Add check for chat_id
                    cardinal.telegram.bot.send_message(
                        chat_id,
                        f"✅ Партия {batch_num}: данные сохранены"
                    )
            else:
                # Get only the relevant part of the old data for this batch
                batch_old_data = {k: games_data[k] for k in batch_data.keys() if k in games_data}
                
                # Compare only this batch's data
                changes = compare_and_get_changes(batch_old_data, batch_data)
                
                # Update games_data with new batch data
                games_data.update(batch_data)  # Changed from dict merge to update
                    
                # Save updated data after each batch
                save_game_data(games_data)
                
                # Send batch results to user
                if chat_id:  # Add check for chat_id
                    if changes:
                        message = f"🔄 Изменения в партии {batch_num}:\n\n" + "\n".join(changes)
                        cardinal.telegram.bot.send_message(
                            chat_id,
                            message,
                            parse_mode="Markdown"
                        )
                    else:
                        cardinal.telegram.bot.send_message(
                            chat_id,
                            f"✅ Партия {batch_num} обработана. Изменений не обнаружено."
                        )
            
            # If there are more items to process, wait before the next batch
            if i + batch_size < len(promo_games):
                wait_msg = f"⏳ Обработано {i + batch_size} элементов. Ожидание {delay_seconds} секунд перед следующей партией..."
                logger.info(wait_msg)
                if chat_id:
                    cardinal.telegram.bot.send_message(chat_id, wait_msg)
                time.sleep(delay_seconds)
            
        return games_data
        
    except requests.RequestException as e:
        error_msg = f"Error fetching game data: {str(e)}"
        logger.error(error_msg)
        if chat_id:
            cardinal.telegram.bot.send_message(chat_id, f"❌ Ошибка: {error_msg}")
        return None

def save_game_data(data, filename='storage/plugins/game_data.json'):
    """Сохраняет данные об играх в JSON файл."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info("Данные об играх успешно сохранены")
    except Exception as e:
        logger.error(f"Ошибка при сохранении данных об играх: {str(e)}")

def load_game_data(filename='storage/plugins/game_data.json'):
    """Загружает данные об играх из JSON файла."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info("Существующие данные об играх не найдены")
        return {}
    except Exception as e:
        logger.error(f"Ошибка при загрузке данных об играх: {str(e)}")
        return {}

def compare_and_get_changes(old_data, new_data):
    """Сравнивает старые и новые данные и возвращает изменения."""
    changes = []
    
    # Check for new games
    for game_title in new_data:
        if game_title not in old_data:
            changes.append(f"🆕 Новая игра: {game_title}")
            continue
            
        # Check for changes in existing games
        old_items = {item['name']: item for item in old_data[game_title]}
        new_items = {item['name']: item for item in new_data[game_title]}
        
        # Find added and removed sections
        added_sections = set(new_items.keys()) - set(old_items.keys())
        removed_sections = set(old_items.keys()) - set(new_items.keys())
        
        for name in added_sections:
            changes.append(f"➕ Добавлен раздел '{name}' в игре {game_title}")
            
        for name in removed_sections:
            changes.append(f"➖ Удален раздел '{name}' из игры {game_title}")
        
        # Check filter changes in existing sections
        common_sections = set(new_items.keys()) & set(old_items.keys())
        for section in common_sections:
            old_filters = old_items[section].get('filters', {})
            new_filters = new_items[section].get('filters', {})
            
            # Check for new and removed filter categories
            added_categories = set(new_filters.keys()) - set(old_filters.keys())
            removed_categories = set(old_filters.keys()) - set(new_filters.keys())
            
            for category in added_categories:
                changes.append(f"📋 Добавлена категория фильтров '{category}' в разделе '{section}' игры {game_title}")
            
            for category in removed_categories:
                changes.append(f"🗑️ Удалена категория фильтров '{category}' в разделе '{section}' игры {game_title}")
            
            # Check for changes in existing filter categories
            common_categories = set(new_filters.keys()) & set(old_filters.keys())
            for category in common_categories:
                old_values = set(old_filters[category])
                new_values = set(new_filters[category])
                
                added_values = new_values - old_values
                removed_values = old_values - new_values
                
                if added_values:
                    changes.append(f"➕ Добавлены опции '{', '.join(added_values)}' в категории '{category}' раздела '{section}' игры {game_title}")
                if removed_values:
                    changes.append(f"➖ Удалены опции '{', '.join(removed_values)}' в категории '{category}' раздела '{section}' игры {game_title}")
    
    # Check for removed games
    for game_title in old_data:
        if game_title not in new_data:
            changes.append(f"❌ Удалена игра: {game_title}")
            
    return changes

def check_for_updates(cardinal: Cardinal, chat_id=None):
    """Проверяет обновления данных об играх и уведомляет об изменениях."""
    cardinal.telegram.bot.send_message(
        chat_id,
        "🔍 проверка начата...\n"
        "⚠️ Обработка будет выполняться партиями по 100 элементов\n"
        "⏱️ Между партиями будет пауза в 30 секунд"
    )
    logger.info("Проверка обновлений данных игр...")

    try:
        new_data = fetch_game_data(cardinal, chat_id, batch_size=100, delay_seconds=30)
        if new_data is None:
            return
            
        cardinal.telegram.bot.send_message(
            chat_id,
            "✅ Проверка успешно завершена!"
        )
            
    except Exception as e:
        error_msg = f"Ошибка в check_for_updates: {str(e)}"
        logger.error(error_msg)
        if chat_id:
            cardinal.telegram.bot.send_message(chat_id, f"❌ Ошибка: {error_msg}")

def schedule_task(cardinal: Cardinal, chat_id: int):
    """Планирует задачу проверки обновлений."""
    moscow_tz = pytz.timezone('Europe/Moscow')
    
    def job():
        now = datetime.now(moscow_tz)
        if now.hour == 8 and now.minute == 00 or now.hour == 20 and now.minute == 0:
            check_for_updates(cardinal, chat_id)  # Pass the chat_id here

    schedule.every().minute.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

def start_scheduler(cardinal: Cardinal, chat_id=None):
    """Запускает планировщик в отдельном потоке."""
    global RUNNING
    if not RUNNING and chat_id:  # Only start if we have a chat_id
        RUNNING = True
        thread = threading.Thread(target=schedule_task, args=(cardinal, chat_id))  # Pass chat_id to schedule_task
        thread.daemon = True
        thread.start()
        logger.info(f"Scheduler started successfully for chat_id: {chat_id}")
        if cardinal.telegram:
            cardinal.telegram.bot.send_message(
                chat_id,
                "🚀 Мониторинг игр FunPay успешно запущен!\n\n"
                "⏰ Обновления будут проверяться ежедневно в 10:00 и в 20:00 по московскому времени\n"
                "🎮 Вы будете получать уведомления об изменениях"
            )
    elif not chat_id:
        logger.error("Cannot start scheduler: no chat_id provided")

def init_commands(cardinal: Cardinal):
    """Инициализирует команды бота."""
    if not cardinal.telegram:
        return
    def handle_check_now(message):
        """Обрабатывает команду проверки прямо сейчас."""
        thread = threading.Thread(target=check_for_updates, args=(cardinal, message.chat.id))
        thread.daemon = True
        thread.start()

    def handle_start_monitoring(message):
        """Обрабатывает команду запуска мониторинга."""
        start_scheduler(cardinal, message.chat.id)

    # Добавление команд в бот
    cardinal.add_telegram_commands(UUID, [
        #("check_games_now", "проверить обновления игр прямо сейчас", True),
        ("start_games_monitoring", "запустить ежедневный мониторинг игр", True),
    ])

    # Register command handlers
    #cardinal.telegram.msg_handler(handle_check_now, commands=["check_games_now"])
    cardinal.telegram.msg_handler(handle_start_monitoring, commands=["start_games_monitoring"])

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
