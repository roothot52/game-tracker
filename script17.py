from twitchio.ext import commands
import requests
import asyncio
import random
import json
import os
from openpyxl import Workbook
from urllib.parse import quote
from datetime import datetime

# ==========================
# TWITCH НАСТРОЙКИ
# ==========================

TWITCH_TOKEN = "oauth:jk7zwc46w6xdhn1nuk8kf9jjn2gbl1"
TWITCH_CHANNEL = "lisadess"
GAME_LINK = "https://gamazavr.ru/alphabet/A/"
FACEIT_API_KEY = "86e9c806-d404-49d5-83c2-36ec037c5e66"

# ==========================
# TWITCH API (категория игры)
# ==========================

TWITCH_CLIENT_ID = "athspix8ma9naxj0ci10tunglixaot"
TWITCH_CLIENT_SECRET = "ia3vlwwj8pg6flkd6bb7reccn1xxzf"
BROADCASTER_LOGIN = "lisadess"

# ==========================
# ФАЙЛЫ СОХРАНЕНИЯ
# ==========================

LOGS_FOLDER = "logs"
TIMER_SAVE_FILE = "timer_state.json"

os.makedirs(LOGS_FOLDER, exist_ok=True)

# ==========================
# ВРЕМЯ В ФОРМАТ
# ==========================

def format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:02}"


def time_to_seconds(t: str) -> int:
    h, m, s = map(int, t.split(":"))
    return h * 3600 + m * 60 + s


def seconds_to_time(total: int) -> str:
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02}:{m:02}:{s:02}"

# ==========================
# ЛОГИ ПО КАНАЛАМ (НЕ СМЕШИВАЮТСЯ)
# ==========================

def get_log_folder(channel: str) -> str:
    folder = f"logs/{channel.lower()}"
    os.makedirs(folder, exist_ok=True)
    return folder


def global_log_file(channel: str):
    folder = get_log_folder(channel)
    return os.path.join(folder, "all_games.json")

def load_global_log(channel: str):
    file = global_log_file(channel)

    if not os.path.exists(file):
        return {"games": {}}

    with open(file, "r", encoding="utf-8") as f:
        return json.load(f)

def save_global_log(channel: str, data):
    file = global_log_file(channel)

    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==========================
# СОХРАНЕНИЕ ТАЙМЕРА
# ==========================

def save_timer_state(state):
    with open(TIMER_SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_timer_state():
    if not os.path.exists(TIMER_SAVE_FILE):
        return None

    with open(TIMER_SAVE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ==========================
# ПОИСК ИГРЫ ВО ВСЕХ ЛОГАХ
# ==========================

def find_game_global(channel: str, game_name: str):
    data = load_global_log(channel)
    games = data.get("games", {})

    game_name = game_name.lower()

    if game_name not in games:
        return []

    sessions = games[game_name]

    if isinstance(sessions, str):
        sessions = [sessions]

    return sessions



# ==========================
# TWITCH API TOKEN
# ==========================

def get_app_token():
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }

    r = requests.post(url, params=params)
    data = r.json()

    if "access_token" not in data:
        raise Exception("❌ не удалось получить App Token")

    return data["access_token"]


def get_broadcaster_id(token):
    url = f"https://api.twitch.tv/helix/users?login={BROADCASTER_LOGIN}"
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}"
    }

    r = requests.get(url, headers=headers)
    return r.json()["data"][0]["id"]


def get_current_game(token, broadcaster_id):
    url = f"https://api.twitch.tv/helix/channels?broadcaster_id={broadcaster_id}"
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}"
    }

    r = requests.get(url, headers=headers)
    return r.json()["data"][0]["game_name"]


def is_stream_live(token, broadcaster_id) -> bool:
    url = f"https://api.twitch.tv/helix/streams?user_id={broadcaster_id}"

    r = requests.get(url, headers={
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}"
    })

    data = r.json()
    return len(data.get("data", [])) > 0


# ==========================
# ДОСТУП: МОДЫ + ВЛАДЕЛЕЦ
# ==========================

def can_control(ctx):
    name = ctx.author.name.lower()

    return (
        ctx.author.is_mod
        or name == TWITCH_CHANNEL
        or name == "roothot"
    )



# ==========================
# BOT
# ==========================

class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix="!",
            initial_channels=[TWITCH_CHANNEL, "synzchill"]
        )

        self.channel_name = TWITCH_CHANNEL.lower()
        self.today_log = load_global_log(self.channel_name)

        saved = load_timer_state()
        if saved:
            self.timer_running = saved["running"]
            self.timer_paused = saved["paused"]
            self.timer_extra = saved["extra"]
            self.timer_game = saved["game"]
            self.timer_start = asyncio.get_event_loop().time()
        else:
            self.timer_running = False
            self.timer_paused = False
            self.timer_extra = 0
            self.timer_game = "игра"
            self.timer_start = 0

    def save_timer(self):
        save_timer_state({
            "running": self.timer_running,
            "paused": self.timer_paused,
            "extra": self.timer_extra,
            "game": self.timer_game
        })

    # ==========================
    # READY
    # ==========================

    async def event_ready(self):
        print(f"✅ бот подключился как {self.nick}")

        asyncio.create_task(self.auto_game_timer())
        asyncio.create_task(self.timer_auto_status())

    # ==========================
    # СОХРАНЕНИЕ СЕССИИ В ЛОГ
    # ==========================

    def log_session(self, game_name: str, elapsed: int):

        game_name = game_name.lower()

        if game_name not in self.today_log["games"]:
            self.today_log["games"][game_name] = []

        # старый формат → строка
        if isinstance(self.today_log["games"][game_name], str):
            old_value = self.today_log["games"][game_name]
            self.today_log["games"][game_name] = [old_value]

        self.today_log["games"][game_name].append(format_time(elapsed))
        save_global_log(self.channel_name, self.today_log)

    # ==========================
    # АВТО ТАЙМЕР ПО ИГРЕ
    # ==========================

    async def auto_game_timer(self):

        await asyncio.sleep(5)

        token = get_app_token()
        broadcaster_id = get_broadcaster_id(token)

        last_game = None

        while True:
            await asyncio.sleep(30)

            try:
                game = get_current_game(token, broadcaster_id)
                live = is_stream_live(token, broadcaster_id)

                if not live:
                    if self.timer_running:
                        await self.finish_game()
                        print("📴 стрим оффлайн — таймер завершён автоматически")

                    await asyncio.sleep(60)
                    continue

                # Just Chatting → стоп
                if game.lower() in ["just chatting", "общение"]:

                    if self.timer_running:
                        elapsed = int(asyncio.get_event_loop().time() - self.timer_start)
                        elapsed += self.timer_extra

                        await self.get_channel(TWITCH_CHANNEL).send(
                            f"⏹️ {self.timer_game} закончено: {format_time(elapsed)}"
                        )

                        self.log_session(self.timer_game, elapsed)

                        self.timer_running = False
                        self.timer_paused = False
                        self.save_timer()

                        await self.get_channel(TWITCH_CHANNEL).send(
                            "💤 теперь не игра — таймер остановлен"
                        )

                    last_game = None
                    continue

                # игра сменилась
                if game != last_game:

                    if self.timer_running:
                        elapsed = int(asyncio.get_event_loop().time() - self.timer_start)
                        elapsed += self.timer_extra

                        await self.get_channel(TWITCH_CHANNEL).send(
                            f"⏹️ {self.timer_game} закончено: {format_time(elapsed)}"
                        )

                        self.log_session(self.timer_game, elapsed)

                    # старт новой игры
                    self.timer_running = True
                    self.timer_paused = False
                    self.timer_start = asyncio.get_event_loop().time()
                    self.timer_extra = 0
                    self.timer_game = game
                    self.save_timer()

                    await self.get_channel(TWITCH_CHANNEL).send(
                        f"🎮 началась: {game} — таймер пошёл!"
                    )

                    last_game = game

            except Exception as e:
                print("❌ ошибка авто-таймера:", e)

    # ==========================
    # АВТО СООБЩЕНИЕ РАЗ В 15 МИН
    # ==========================

    async def timer_auto_status(self):

        while True:
            await asyncio.sleep(900)

            if not self.timer_running or self.timer_paused:
                continue

            elapsed = int(asyncio.get_event_loop().time() - self.timer_start)
            elapsed += self.timer_extra

            await self.get_channel(TWITCH_CHANNEL).send(
                f"⏱️ прошло: {format_time(elapsed)} — {self.timer_game}"
            )

    # ==========================
    # !СКОЛЬКО
    # ==========================

    @commands.command(name="сколько")
    async def how_much(self, ctx):

        if not self.timer_running:
            await ctx.send("таймер не запущен 😅")
            return

        elapsed = int(asyncio.get_event_loop().time() - self.timer_start)
        elapsed += self.timer_extra

        await ctx.send(f"⏱️ {self.timer_game}: {format_time(elapsed)}")

    # ==========================
    # !ПАУЗА
    # ==========================

    @commands.command(name="пауза")
    async def pause_timer(self, ctx):

        if not can_control(ctx):
            return

        if not self.timer_running or self.timer_paused:
            return

        elapsed = int(asyncio.get_event_loop().time() - self.timer_start)
        self.timer_extra += elapsed
        self.timer_paused = True
        self.save_timer()

        await ctx.send("⏸️ таймер на паузе")

    # ==========================
    # !АНПАУЗ
    # ==========================

    @commands.command(name="анпауз")
    async def unpause_timer(self, ctx):

        if not can_control(ctx):
            return

        if not self.timer_running or not self.timer_paused:
            return

        self.timer_start = asyncio.get_event_loop().time()
        self.timer_paused = False
        self.save_timer()

        await ctx.send("▶️ таймер продолжен")

    # ==========================
    # !ПЛЮС 60
    # ==========================

    @commands.command(name="плюс")
    async def add_seconds(self, ctx, seconds: int):

        if not can_control(ctx):
            return

        self.timer_extra += seconds
        self.save_timer()

        await ctx.send(f"➕ добавлено {seconds} сек")

    # ==========================
    # !СТОП
    # ==========================

    @commands.command(name="стоп")
    async def stop_timer(self, ctx):

        if not can_control(ctx):
            return

        if not self.timer_running:
            await ctx.send("таймер сейчас не запущен 😅")
            return

        if self.timer_paused:
            elapsed = self.timer_extra
        else:
            elapsed = int(asyncio.get_event_loop().time() - self.timer_start)
            elapsed += self.timer_extra

        self.log_session(self.timer_game, elapsed)

        results = find_game_in_all_logs(self.timer_game.lower(), self.channel_name)

        total_seconds = sum(time_to_seconds(t) for _, t in results)
        total_time = seconds_to_time(total_seconds)

        await ctx.send(f"⏹️ {self.timer_game} завершена: {format_time(elapsed)}")
        await ctx.send(f"🎮 всего по игре: {total_time} ({len(results)} сессии)")

        self.timer_running = False
        self.timer_paused = False
        self.timer_extra = 0
        self.timer_game = "игра"
        self.save_timer()

    # ==========================
    # !ЭКСПОРТ
    # ==========================

    @commands.command(name="экспорт")
    async def export_one_game(self, ctx):

        if not can_control(ctx):
            return

        game_name = ctx.message.content.replace("!экспорт", "").strip().lower()

        if not game_name:
            await ctx.send("напиши игру 😄 пример: !экспорт apex legends")
            return

        folder = get_log_folder(self.channel_name)

        total_seconds = 0
        sessions = 0

        for file in os.listdir(folder):
            if not file.endswith(".json"):
                continue

            path = f"{folder}/{file}"

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            games = data.get("games", {})

            # ищем совпадение по lower()
            found_key = None
            for key in games.keys():
                if key.strip().lower() == game_name.strip().lower():
                    found_key = key
                    break

            if not found_key:
                continue

            # суммируем все сессии
            for session_time in games[found_key]:
                total_seconds += time_to_seconds(session_time)
                sessions += 1

        if sessions == 0:
            await ctx.send(f"🎮 данных по игре {game_name} нет")
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Экспорт игры"

        ws.append(["Игра", "Общее время", "Сессий"])
        ws.append([
            game_name,
            seconds_to_time(total_seconds),
            sessions
        ])

        filename = f"export_{game_name.replace(' ', '_')}.xlsx"
        wb.save(filename)

        await ctx.send(f"📊 экспорт игры {game_name} готов: {filename}")

    # ==========================
    # !логи
    # ==========================

    @commands.command(name="логи")
    async def show_logs(self, ctx):

        if not can_control(ctx):
            return

        # загружаем единый лог
        data = load_global_log(self.channel_name)
        games = data.get("games", {})

        if not games:
            await ctx.send("📒 логов пока нет 😅")
            return

        stats = []

        # собираем сумму времени по каждой игре
        for game_name, sessions_list in games.items():

            # если вдруг старая запись строкой
            if isinstance(sessions_list, str):
                sessions_list = [sessions_list]

            total_seconds = sum(time_to_seconds(t) for t in sessions_list)
            sessions_count = len(sessions_list)

            stats.append((game_name, total_seconds, sessions_count))

        # сортируем по времени (самые долгие сверху)
        stats.sort(key=lambda x: x[1], reverse=True)

        msg = "📒 логи игр:\n"

        # показываем максимум 5 игр
        for game_name, total_sec, sess_count in stats[:5]:
            msg += f"🎮 {game_name} — {seconds_to_time(total_sec)} ({sess_count} сессии)\n"

        await ctx.send(msg.strip())

    # ==========================
    # !СБРОС
    # ==========================

    @commands.command(name="сброс")
    async def reset_timer(self, ctx):

        if not can_control(ctx):
            return

        # полный сброс
        self.timer_running = False
        self.timer_paused = False
        self.timer_extra = 0
        self.timer_game = "игра"
        self.timer_start = 0

        self.save_timer()

        await ctx.send("♻️ таймер полностью сброшен")

    # ==========================
    # !СТАРТ ИГРА
    # ==========================

    @commands.command(name="старт")
    async def start_timer(self, ctx, *args):

        if not can_control(ctx):
            return

        if not args:
            await ctx.send("напиши название игры 😅")
            return

        if self.timer_running:
            await ctx.send("таймер уже запущен 😄")
            return

        game_name = " ".join(args)

        self.timer_running = True
        self.timer_paused = False
        self.timer_extra = 0
        self.timer_start = asyncio.get_event_loop().time()
        self.timer_game = game_name

        self.save_timer()

        await ctx.send(f"🎮 таймер запущен вручную: {game_name}")

    # ==========================
    # !ПРОДОЛЖИТЬ
    # ==========================

    @commands.command(name="продолжить")
    async def continue_timer(self, ctx):

        if not can_control(ctx):
            return

        if self.timer_running:
            await ctx.send("таймер уже идёт 😄")
            return

        try:
            token = get_app_token()
            broadcaster_id = get_broadcaster_id(token)
            game = get_current_game(token, broadcaster_id)

            if game.lower() in ["just chatting", "общение"]:
                await ctx.send("сейчас не игра 😅")
                return

            self.timer_running = True
            self.timer_paused = False
            self.timer_extra = 0
            self.timer_start = asyncio.get_event_loop().time()
            self.timer_game = game

            self.save_timer()

            await ctx.send(f"🎮 таймер продолжен: {game}")

        except Exception as e:
            print("❌ ошибка continue:", e)
            await ctx.send("не получилось продолжить 😅")

    # ==========================
    # !ИГРА GTA V
    # ==========================

    @commands.command(name="игра")
    async def game_info(self, ctx, *args):

        if not args:
            await ctx.send("напиши игру 😅")
            return

        game_name = " ".join(args).lower()

        sessions = find_game_global(self.channel_name, game_name)

        if not sessions:
            await ctx.send("данных по игре нет 😅")
            return

        total_seconds = sum(time_to_seconds(t) for t in sessions)
        total_time = seconds_to_time(total_seconds)

        await ctx.send(
            f"🎮 Игра: {game_name} — всего: {total_time} ({len(sessions)} сессии)"
        )

    @commands.command(name="пиво")
    async def beer_command(self, ctx):

        beers = [
            "Leffe",
            "Stella Artois",
            "Hoegaarden",
            "Delirium Tremens",
            "Krombacher",
            "Paulaner",
            "Paulaner",
            "Warsteiner",
            "Beck's",
            "Pilsner Urquell",
            "Velkopopovický Kozel",
            "Staropramen",
            "Guinness",
            "Corona Extra",
            "Modelo Especial",
            "Балтика",
            "Жигулевское",
            "Хамовники",
            "Мочу",
            "Клинское"
        ]

        beer = random.choice(beers)
        drunk = random.randint(1, 100)

        await ctx.send(
            f"🍺 @{ctx.author.name} выпил {beer} и теперь пьян на {drunk}% 😵"
        )

    @commands.command(name="кнб")
    async def rock_paper_scissors(self, ctx, choice=None):

        if choice is None:
            await ctx.send("✋ Напиши так: !кнб камень / ножницы / бумага")
            return

        choice = choice.lower()

        options = ["камень", "ножницы", "бумага"]

        if choice not in options:
            await ctx.send("❌ Выбери: камень, ножницы или бумага")
            return

        bot_choice = random.choice(options)

        # логика победы
        if choice == bot_choice:
            result = "🤝 Ничья!"

        elif (
                (choice == "камень" and bot_choice == "ножницы") or
                (choice == "ножницы" and bot_choice == "бумага") or
                (choice == "бумага" and bot_choice == "камень")
        ):
            result = "🎉 Ты выиграл!"

        else:
            result = "💀 Ты проиграл!"

        await ctx.send(
            f"🕹️ @{ctx.author.name} выбрал: {choice}\n"
            f"🤖 Бот выбрал: {bot_choice}\n"
            f"{result}"
        )

    from urllib.parse import quote

    @commands.command(name="elo")
    async def elo_command(self, ctx, nickname: str = None):

        if not nickname:
            await ctx.send("пример: !elo s1mple 😅")
            return

        safe_nick = quote(nickname)

        url = f"https://open.faceit.com/data/v4/players?nickname={safe_nick}"

        headers = {
            "Authorization": f"Bearer {FACEIT_API_KEY}"
        }

        r = requests.get(url, headers=headers)

        if r.status_code == 404:
            await ctx.send("игрок не найден 😅")
            return

        if r.status_code == 401:
            await ctx.send("Faceit API ключ неверный 😅")
            return

        if r.status_code != 200:
            await ctx.send("Faceit сейчас недоступен 😅")
            print("FACEIT RESPONSE:", r.text)
            return

        data = r.json()

        if "cs2" not in data["games"]:
            await ctx.send("у игрока нет CS2 😅")
            return

        elo = data["games"]["cs2"]["faceit_elo"]
        level = data["games"]["cs2"]["skill_level"]

        await ctx.send(f"🎮 {nickname} — ELO: {elo} (Level {level})")

    @commands.command(name="selo")
    async def faceit_link(self, ctx):

        nickname = ctx.message.content.replace("!selo", "").strip()

        if not nickname:
            await ctx.send("напиши ник 😄 пример: !selo s1mple")
            return

        try:
            url = f"https://open.faceit.com/data/v4/players?nickname={nickname}"

            headers = {
                "Authorization": f"Bearer {FACEIT_API_KEY}"
            }

            r = requests.get(url, headers=headers)

            # ❌ если игрок не найден или ошибка
            if r.status_code != 200:
                await ctx.send("игрок не найден 😅")
                return

            data = r.json()

            player_id = data.get("player_id")

            if not player_id:
                await ctx.send("игрок не найден 😅")
                return

            profile_url = f"https://www.faceit.com/en/players/{nickname}"

            await ctx.send(f"🔗 FACEIT профиль {nickname}: {profile_url}")

        except Exception as e:
            print("❌ ошибка Faceit link:", e)
            await ctx.send("игрок не найден 😅")

    @commands.command(name="напомни")
    async def remind_me(self, ctx, minutes: int = None, *text):

        # только модеры и владелец
        if not can_control(ctx):
            return

        if minutes is None or not text:
            await ctx.send("пример: !напомни 10 выпить воды 😄")
            return

        reminder_text = " ".join(text)

        await ctx.send(f"⏳ окей! напомню через {minutes} мин: {reminder_text}")

        async def reminder_task():
            await asyncio.sleep(minutes * 60)
            await ctx.send(f"⏰ Напоминание: {reminder_text}")

        asyncio.create_task(reminder_task())

    @commands.command(name="играф")
    async def game_ref_link(self, ctx):
        await ctx.send(f"Ссылка на сайт с играми по алфавиту: {GAME_LINK}")


# ==========================
# RUN
# ==========================

if __name__ == "__main__":
    bot = Bot()
    bot.run()
