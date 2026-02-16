import requests
from flask import Flask, render_template, redirect, url_for, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import time

app = Flask(__name__)
app.config["SECRET_KEY"] = "supersecretkey"
app.config["STREAMER_PIN"] = "2253"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


# -------------------- MODELS --------------------

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))

    twitch_name = db.Column(db.String(100), nullable=True)



class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200))
    status = db.Column(db.String(50))

    steam_appid = db.Column(db.Integer, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))


# -------------------- LOGIN --------------------

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# -------------------- ROUTES --------------------

@app.route("/")
def home():
    users = User.query.all()

    streamers = []

    for u in users:
        avatar = get_twitch_avatar(u.twitch_name)

        streamers.append({
            "username": u.username,
            "avatar": avatar
        })

    return render_template("home.html", streamers=streamers)


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        twitch_name = request.form["twitch_name"]
        pin = request.form["pin"]

        if pin != app.config["STREAMER_PIN"]:
            return "Неверный PIN-код!"

        if User.query.filter_by(username=username).first():
            return "Пользователь уже существует!"

        hashed_password = generate_password_hash(password)

        user = User(
            username=username,
            password=hashed_password,
            twitch_name=twitch_name
        )

        db.session.add(user)
        db.session.commit()

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("dashboard"))

        return "Неверный логин или пароль"

    return render_template("login.html")


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    steam_results = None

    if request.method == "POST":
        action = request.form.get("action")

        # 📦 Массовое добавление игр
        if action == "bulk_add":
            titles_text = request.form["titles"]
            status = request.form["status"]

            import re

            # ✅ Разделяем по запятым и переносам строк
            titles = re.split(r"[,\n]+", titles_text)
            titles = [t.strip() for t in titles if t.strip()]

            for title in titles:

                # 🔍 ищем игру в Steam
                results = search_steam_games(title)

                if results:
                    best = results[0]
                    name = best["name"]
                    appid = best["appid"]
                    image_url = best["image_url"]
                else:
                    name = title
                    appid = None
                    image_url = None

                # ➕ добавляем игру в базу
                game = Game(
                    title=name,
                    status=status,
                    steam_appid=appid,
                    image_url=image_url,
                    user_id=current_user.id
                )

                db.session.add(game)

            db.session.commit()

            return redirect(url_for("dashboard"))

        # 🔍 Поиск Steam
        if action == "search":
            title = request.form["title"]
            status = request.form["status"]

            steam_results = search_steam_games(title)

            return render_template(
                "dashboard.html",
                user=current_user,
                steam_results=steam_results,
                chosen_status=status,
                done_games=Game.query.filter_by(user_id=current_user.id, status="Пройдено").all(),
                drop_games=Game.query.filter_by(user_id=current_user.id, status="Дроп").all(),
                queue_games=Game.query.filter_by(user_id=current_user.id, status="В очереди").all()
            )

        # ✅ Добавление выбранной игры
        if action == "add":
            name = request.form["name"]
            appid = request.form["appid"]
            image_url = request.form["image_url"]
            status = request.form["status"]

            game = Game(
                title=name,
                status=status,
                steam_appid=appid,
                image_url=image_url,
                user_id=current_user.id
            )

            db.session.add(game)
            db.session.commit()

            return redirect(url_for("dashboard"))

    done_games = Game.query.filter_by(user_id=current_user.id, status="Пройдено").all()
    drop_games = Game.query.filter_by(user_id=current_user.id, status="Дроп").all()
    queue_games = Game.query.filter_by(user_id=current_user.id, status="В очереди").all()

    return render_template(
        "dashboard.html",
        user=current_user,
        steam_results=None,
        done_games=done_games,
        drop_games=drop_games,
        queue_games=queue_games
    )


@app.route("/delete/<int:game_id>")
@login_required
def delete_game(game_id):
    game = Game.query.get_or_404(game_id)

    if game.user_id != current_user.id:
        return "Нет доступа!"

    db.session.delete(game)
    db.session.commit()
    return redirect(url_for("dashboard"))


@app.route("/profile/<username>")
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()

    avatar_url = get_twitch_avatar(user.twitch_name)
    twitch_status = get_twitch_status(user.twitch_name)

    done_games = Game.query.filter_by(
        user_id=user.id,
        status="Пройдено"
    ).order_by(Game.title.asc()).all()

    drop_games = Game.query.filter_by(
        user_id=user.id,
        status="Дроп"
    ).order_by(Game.title.asc()).all()

    queue_games = Game.query.filter_by(
        user_id=user.id,
        status="В очереди"
    ).order_by(Game.title.asc()).all()

    return render_template(
        "profile.html",
        profile_user=user,
        avatar_url=avatar_url,
        twitch_status=twitch_status,
        done_games=done_games,
        drop_games=drop_games,
        queue_games=queue_games
    )




@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

def search_steam_games(title):
    url = "https://store.steampowered.com/api/storesearch"
    params = {
        "term": title,
        "l": "english",
        "cc": "US"
    }

    r = requests.get(url, params=params, timeout=3)
    data = r.json()

    results = []

    if "items" in data:
        for item in data["items"][:5]:
            appid = item["id"]
            name = item["name"]

            image_url = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"

            results.append({
                "appid": appid,
                "name": name,
                "image_url": image_url
            })

    return results

# -------------------- TWITCH API --------------------

import os

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

twitch_token = None
twitch_token_expires = 0


def get_twitch_app_token():
    """
    Получает App Access Token автоматически через Client Credentials Flow.
    """

    global twitch_token, twitch_token_expires

    url = "https://id.twitch.tv/oauth2/token"

    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }

    r = requests.post(url, params=params)

    if r.status_code != 200:
        print("❌ Ошибка получения Twitch Token")
        print("STATUS:", r.status_code)
        print("RESPONSE:", r.text)
        return None

    data = r.json()

    twitch_token = data["access_token"]

    # expires_in приходит в секундах
    twitch_token_expires = time.time() + data["expires_in"]

    print("✅ Twitch Token обновлён успешно!")

    return twitch_token


def get_valid_twitch_token():
    """
    Проверяет токен и обновляет если он истёк.
    """

    global twitch_token, twitch_token_expires

    if twitch_token is None or time.time() > twitch_token_expires:
        return get_twitch_app_token()

    return twitch_token


def get_twitch_avatar(username):

    if not username:
        return None

    token = get_valid_twitch_token()

    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}"
    }

    url = f"https://api.twitch.tv/helix/users?login={username.lower()}"

    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        print("❌ Ошибка Twitch API")
        print("STATUS:", r.status_code)
        print("RESPONSE:", r.text)
        return None

    data = r.json()

    if "data" in data and len(data["data"]) > 0:
        return data["data"][0]["profile_image_url"]

    return None

def get_twitch_status(username):
    """
    Проверяет, стример онлайн или оффлайн.
    """

    if not username:
        return "Offline"

    token = get_valid_twitch_token()

    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}"
    }

    url = f"https://api.twitch.tv/helix/streams?user_login={username.lower()}"

    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        print("❌ Ошибка Twitch Stream API")
        return "Offline"

    data = r.json()

    if "data" in data and len(data["data"]) > 0:
        return "Online"

    return "Offline"


# -------------------- START --------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(host="0.0.0.0", port=5000)

