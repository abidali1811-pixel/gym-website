"""SQLite/Supabase-backed web server for the IronPulse website and admin page."""

from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time

try:
    import psycopg
except ImportError:
    psycopg = None


ROOT = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(ROOT, "visits.sqlite3")
GALLERY_UPLOAD_DIR = os.path.join(ROOT, "uploads", "gallery")


def load_local_env():
    env_path = os.path.join(ROOT, ".env")
    try:
        with open(env_path, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                if name.strip() and name.strip() not in os.environ:
                    os.environ[name.strip()] = value
    except FileNotFoundError:
        pass


load_local_env()
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-astra")
CHAT_PROVIDER = os.environ.get("CHAT_PROVIDER", "gemini").strip().lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
CHATBOT_KNOWLEDGE = os.path.join(ROOT, "chatbot_knowledge.json")
SESSION_LIFETIME = timedelta(hours=12)
CAPTCHA_LIFETIME = timedelta(minutes=15)
CAPTCHA_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_ERRORS = (sqlite3.Error,) + ((psycopg.Error,) if psycopg is not None else ())


class DatabaseRow(dict):
    def __init__(self, names, values):
        self._values = tuple(values)
        super().__init__(zip(names, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def postgres_row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []

    def make_row(values):
        return DatabaseRow(names, values)

    return make_row


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.connection.__exit__(exc_type, exc_value, traceback)

    @property
    def row_factory(self):
        return postgres_row_factory

    @row_factory.setter
    def row_factory(self, _value):
        pass

    def execute(self, sql, parameters=()):
        cursor = self.connection.cursor()
        cursor.execute(sql.replace("?", "%s"), parameters)
        return cursor

    def executemany(self, sql, parameters):
        cursor = self.connection.cursor()
        cursor.executemany(sql.replace("?", "%s"), parameters)
        return cursor


def connect_database():
    if not DATABASE_URL:
        return sqlite3.connect(DATABASE)
    if psycopg is None:
        raise RuntimeError("DATABASE_URL is set but psycopg is unavailable. Install dependencies from requirements.txt.")
    connection = psycopg.connect(
        DATABASE_URL,
        sslmode="require",
        connect_timeout=10,
        prepare_threshold=None,
        row_factory=postgres_row_factory,
    )
    return PostgresConnection(connection)


def initialize_database():
    id_column = "BIGSERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with connect_database() as db:
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS visits (
                id {id_column},
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        if DATABASE_URL:
            visit_columns = {row[0] for row in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'visits'"
            ).fetchall()}
        else:
            visit_columns = {row[1] for row in db.execute("PRAGMA table_info(visits)")}
        if "phone" not in visit_columns:
            db.execute("ALTER TABLE visits ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        db.execute("""
            CREATE TABLE IF NOT EXISTS membership_plans (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price TEXT NOT NULL,
                featured INTEGER NOT NULL DEFAULT 0,
                feature1 TEXT NOT NULL,
                feature2 TEXT NOT NULL,
                feature3 TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS membership_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                title TEXT NOT NULL,
                description TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS home_banner (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                eyebrow TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                button_label TEXT NOT NULL,
                button_url TEXT NOT NULL,
                image_url TEXT NOT NULL DEFAULT ''
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS site_theme (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                accent TEXT NOT NULL,
                accent_hover TEXT NOT NULL,
                background TEXT NOT NULL,
                background_elevated TEXT NOT NULL,
                surface TEXT NOT NULL,
                text_color TEXT NOT NULL,
                muted TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS gallery_images (
                slot INTEGER PRIMARY KEY,
                image_url TEXT NOT NULL DEFAULT '',
                alt_text TEXT NOT NULL,
                caption TEXT NOT NULL
            )
        """)
        db.execute(
            "INSERT INTO membership_settings (id, title, description) VALUES (1, ?, ?) ON CONFLICT (id) DO NOTHING",
            ("Memberships", "Simple plans. Cancel anytime on monthly options."),
        )
        defaults = [
            (1, "Basic", "29", 0, "Gym floor access", "Locker room", "1 guest pass / month"),
            (2, "Pro", "49", 1, "Everything in Basic", "Unlimited group classes", "Monthly body scan"),
            (3, "Elite", "89", 0, "Everything in Pro", "4 PT sessions / month", "Sauna & recovery lounge"),
        ]
        db.executemany(
            "INSERT INTO membership_plans "
            "(id, name, price, featured, feature1, feature2, feature3) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            defaults,
        )
        db.execute(
            "INSERT INTO home_banner "
            "(id, eyebrow, title, description, button_label, button_url, image_url) VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            ("Train harder. Live stronger.", "Your goals.\nOur floor.", "Morning gym hours: 6:00 AM–10:00 AM; evening hours: 6:00 PM–10:00 PM. Expert coaches and a community that keeps you accountable.", "View memberships", "#pricing", ""),
        )
        db.execute(
            "UPDATE home_banner SET description = ? WHERE id = 1 AND description = ?",
            (
                "Morning gym hours: 6:00 AM–10:00 AM; evening hours: 6:00 PM–10:00 PM. Expert coaches and a community that keeps you accountable.",
                "24/7 access, expert coaches, and a community that keeps you accountable.",
            ),
        )
        db.execute(
            "INSERT INTO site_theme "
            "(id, accent, accent_hover, background, background_elevated, surface, text_color, muted) "
            "VALUES (1, '#2583ff', '#4b9aff', '#0c0d0f', '#14161a', '#1a1d24', '#f2f4f8', '#9aa3b2') ON CONFLICT (id) DO NOTHING"
        )
        gallery_defaults = [
            (1, "https://images.unsplash.com/photo-1697490562519-3eb03dab6d5f?auto=format&fit=crop&w=1400&q=85", "Modern gym interior with strength equipment", "The training floor"),
            (2, "https://images.unsplash.com/photo-1718633561231-864a4c466991?auto=format&fit=crop&w=1200&q=85", "Athlete performing a barbell squat", "Strength, with purpose"),
            (3, "https://images.unsplash.com/photo-1704223523492-6279abd5261b?auto=format&fit=crop&w=1200&q=85", "Athlete training with dumbbells", "Built one rep at a time"),
            (4, "https://images.unsplash.com/photo-1574680088814-c9e8a10d8a4d?auto=format&fit=crop&w=1200&q=85", "Athlete lifting weights in the gym", "Find your next level"),
        ]
        db.executemany(
            "INSERT INTO gallery_images (slot, image_url, alt_text, caption) VALUES (?, ?, ?, ?) ON CONFLICT (slot) DO NOTHING",
            gallery_defaults,
        )


def signed_session(timestamp):
    signature = hmac.new(SESSION_SECRET.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}.{signature}"


class WebsiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/admin":
            if self.is_admin():
                self.show_admin()
            else:
                self.show_login()
            return
        if path == "/admin/logout":
            self.redirect("/admin")
            return
        if path == "/captcha":
            challenge, token = self.new_captcha()
            self.respond_json({"challenge": challenge, "token": token})
            return
        if path in ("/", "/index.html"):
            self.serve_home()
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/submit":
            self.save_visit()
        elif path == "/admin/login":
            self.login()
        elif path == "/admin/memberships":
            if self.is_admin():
                self.save_memberships()
            else:
                self.send_error(403, "Please sign in to edit memberships")
        elif path == "/admin/banner":
            if self.is_admin():
                self.save_banner()
            else:
                self.send_error(403, "Please sign in to edit the banner")
        elif path == "/admin/theme":
            if self.is_admin():
                self.save_theme()
            else:
                self.send_error(403, "Please sign in to edit the theme")
        elif path == "/admin/gallery":
            if self.is_admin():
                self.save_gallery()
            else:
                self.send_error(403, "Please sign in to edit the gallery")
        elif path == "/api/chat":
            self.chat_reply()
        elif path == "/admin/logout":
            self.send_response(303)
            self.send_header("Set-Cookie", "admin_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            self.send_header("Location", "/admin")
            self.end_headers()
        else:
            self.send_error(404)

    def read_form(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 0 or length > 16_384:
            return None
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(body, keep_blank_values=True)

    def read_gallery_form(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            fields = self.read_form()
            return (fields, {}) if fields is not None else (None, None)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, None
        if length <= 0 or length > 22 * 1024 * 1024:
            return None, None
        raw_body = self.rfile.read(length)
        message = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + raw_body
        )
        if not message.is_multipart():
            return None, None
        fields = {}
        files = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                if payload:
                    files[name] = payload
                continue
            charset = part.get_content_charset() or "utf-8"
            fields[name] = [payload.decode(charset, errors="replace")]
        return fields, files

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > 24_000:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def respond_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def chat_reply(self):
        if CHAT_PROVIDER == "gemini" and not GEMINI_API_KEY:
            self.respond_json({"error": "The chatbot is not configured yet. Add GEMINI_API_KEY to the .env file and restart the server."}, status=503)
            return
        if CHAT_PROVIDER == "openai" and not OPENAI_API_KEY:
            self.respond_json({"error": "The chatbot is not configured yet. Add OPENAI_API_KEY to the .env file and restart the server."}, status=503)
            return
        if CHAT_PROVIDER not in ("gemini", "openai"):
            self.respond_json({"error": "CHAT_PROVIDER must be set to gemini or openai in the .env file."}, status=503)
            return
        payload = self.read_json()
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
            self.respond_json({"error": "Send up to 12 chat messages."}, status=400)
            return
        history = []
        for item in messages:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                self.respond_json({"error": "Invalid chat message."}, status=400)
                return
            content = item.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 1200:
                self.respond_json({"error": "Each message must contain 1 to 1200 characters."}, status=400)
                return
            history.append({"role": item["role"], "content": content.strip()})
        if history[-1]["role"] != "user":
            self.respond_json({"error": "The latest message must be from the visitor."}, status=400)
            return
        try:
            with open(CHATBOT_KNOWLEDGE, encoding="utf-8") as knowledge_file:
                facts = json.load(knowledge_file)
            with connect_database() as db:
                db.row_factory = sqlite3.Row
                plans = db.execute("SELECT name, price, feature1, feature2, feature3 FROM membership_plans ORDER BY id").fetchall()
            facts["current_memberships"] = [
                {"name": plan["name"], "monthly_price_inr": plan["price"], "features": [plan[f"feature{number}"] for number in (1, 2, 3)]}
                for plan in plans
            ]
            facts["membership_currency"] = "INR (Indian rupees)"
        except (OSError, json.JSONDecodeError) + DATABASE_ERRORS:
            self.respond_json({"error": "The gym information file could not be loaded. Please contact the gym directly."}, status=500)
            return
        instructions = (
            "You are the IronPulse Fitness website assistant. Be friendly, concise, and answer only using the gym facts below plus general, non-medical fitness knowledge. "
            "Use the current membership data and class schedule when asked about plans or times. All membership prices are in Indian rupees (INR); show them with the ₹ symbol and never convert currencies. Never invent prices, schedules, age restrictions, or gym policies; if a policy is not listed, say it is not specified and suggest contacting staff. "
            "For diet questions, give broad, non-prescriptive healthy-eating information only. Do not give calorie targets, meal prescriptions, supplements, diagnosis, or treatment advice. "
            "For minors, pregnancy, injuries, chronic conditions, allergies, or other health concerns, recommend checking with a qualified healthcare professional and gym staff. "
            "Do not request sensitive health information. Gym reference data (JSON): " + json.dumps(facts, ensure_ascii=False)
        )
        if CHAT_PROVIDER == "gemini":
            contents = [
                {"role": "user" if message["role"] == "user" else "model", "parts": [{"text": message["content"]}]}
                for message in history
            ]
            api_request = Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{quote(GEMINI_MODEL, safe='')}:generateContent",
                data=json.dumps({
                    "system_instruction": {"parts": [{"text": instructions}]},
                    "contents": contents,
                    "generationConfig": {
                        "maxOutputTokens": 350,
                        "thinkingConfig": {"thinkingLevel": "low"},
                    },
                }).encode("utf-8"),
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                method="POST",
            )
        else:
            api_request = Request(
                "https://api.openai.com/v1/responses",
                data=json.dumps({"model": OPENAI_MODEL, "instructions": instructions, "input": history, "max_output_tokens": 350, "store": False}).encode("utf-8"),
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                method="POST",
            )
        try:
            for attempt in range(3):
                try:
                    request_timeout = 45 if CHAT_PROVIDER == "gemini" else 30
                    with urlopen(api_request, timeout=request_timeout) as response:
                        api_response = json.loads(response.read().decode("utf-8"))
                    break
                except HTTPError as error:
                    if CHAT_PROVIDER == "gemini" and error.code in (500, 502, 503, 504) and attempt < 2:
                        error.close()
                        time.sleep(2 ** attempt)
                        continue
                    raise
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                api_error = error_body.get("error", {}) if isinstance(error_body, dict) else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                api_error = {}
            api_error_code = api_error.get("code", "") if isinstance(api_error, dict) else ""
            api_error_type = api_error.get("type", "") if isinstance(api_error, dict) else ""
            api_error_status = api_error.get("status", "") if isinstance(api_error, dict) else ""
            print(f"{CHAT_PROVIDER.title()} chat request failed with HTTP {error.code} ({api_error_code or api_error_status or api_error_type or 'unknown error'})")
            if CHAT_PROVIDER == "gemini" and error.code == 429:
                message = "Gemini API quota or rate limit reached. Check your Google AI Studio project quota and retry when available."
            elif CHAT_PROVIDER == "gemini" and error.code in (401, 403):
                message = "Google Gemini rejected the API key. Check GEMINI_API_KEY in .env, then restart the server."
            elif CHAT_PROVIDER == "gemini" and error.code in (400, 404):
                message = "Gemini rejected the model or request. Check GEMINI_MODEL in .env."
            elif CHAT_PROVIDER == "gemini" and error.code in (500, 502, 503, 504):
                message = f"Gemini is temporarily unavailable (HTTP {error.code}) after retries. Please wait a little and try again."
            elif error.code == 401:
                message = "OpenAI rejected the API key. Check OPENAI_API_KEY in .env, then restart the server."
            elif error.code == 403:
                message = "This API key or project cannot use the configured model. Check model access and OPENAI_MODEL in .env."
            elif error.code == 404:
                message = "The configured model was not found. Check OPENAI_MODEL in .env."
            elif error.code == 429:
                if api_error_code in ("insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "billing_hard_limit_reached"):
                    message = "The OpenAI project has no available quota or has reached a usage/spend limit. Check API billing, credits, and project limits."
                elif api_error_code in ("rate_limit_exceeded", "slow_down") or api_error_type == "rate_limit_error":
                    message = "The chatbot is receiving requests too quickly. Wait a little and try again."
                else:
                    message = "OpenAI returned a 429 limit error. Check your API project’s usage, credits, and limits; wait before retrying if this is temporary."
            elif error.code == 400:
                message = "OpenAI rejected the chat request. Check OPENAI_MODEL in .env and restart the server."
            else:
                if CHAT_PROVIDER == "gemini":
                    provider_detail = api_error_status or api_error_code or "provider error"
                    message = f"Gemini could not answer (HTTP {error.code}, {provider_detail}). Check the server terminal for details and try again shortly."
                else:
                    message = f"OpenAI could not answer (HTTP {error.code}). Check the server terminal for details and try again shortly."
            self.respond_json({"error": message}, status=502)
            return
        except URLError as error:
            reason = error.reason
            print(f"{CHAT_PROVIDER.title()} connection error: {type(reason).__name__}: {reason}")
            if CHAT_PROVIDER == "gemini":
                message = "Could not connect to Gemini. Check the server's internet connection and terminal error, then try again."
            else:
                message = "Could not connect to OpenAI. Check the server's internet connection and terminal error, then try again."
            self.respond_json({"error": message}, status=502)
            return
        except TimeoutError:
            print(f"{CHAT_PROVIDER.title()} request timed out.")
            self.respond_json({"error": f"{CHAT_PROVIDER.title()} took too long to respond. Please try again shortly."}, status=502)
            return
        except json.JSONDecodeError:
            print(f"{CHAT_PROVIDER.title()} returned a response that was not valid JSON.")
            self.respond_json({"error": f"{CHAT_PROVIDER.title()} returned an unreadable response. Please try again shortly."}, status=502)
            return
        answer_parts = []
        if CHAT_PROVIDER == "gemini":
            candidates = api_response.get("candidates", [])
            if candidates:
                answer_parts = [part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])]
        else:
            for output_item in api_response.get("output", []):
                if output_item.get("type") == "message" and output_item.get("role") == "assistant":
                    answer_parts.extend(part.get("text", "") for part in output_item.get("content", []) if part.get("type") == "output_text")
        answer = "\n".join(part for part in answer_parts if part).strip()
        if not answer:
            self.respond_json({"error": "The chat service returned no answer. Please try again."}, status=502)
            return
        self.respond_json({"reply": answer})

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def respond_html(self, content, status=200, extra_headers=()):
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def is_admin(self):
        raw_cookie = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw_cookie)
            value = jar.get("admin_session")
            if value is None:
                return False
            timestamp, signature = value.value.split(".", 1)
            issued = datetime.fromtimestamp(int(timestamp), timezone.utc)
        except (ValueError, OverflowError):
            return False
        expected = signed_session(timestamp).split(".", 1)[1]
        age = datetime.now(timezone.utc) - issued
        return age >= timedelta(0) and age < SESSION_LIFETIME and hmac.compare_digest(signature, expected)

    def new_captcha(self):
        challenge = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(6))
        timestamp = str(int(time.time()))
        payload = f"{timestamp}.{challenge}"
        signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return challenge, f"{payload}.{signature}"

    def verify_captcha(self, token, answer):
        try:
            timestamp, challenge, signature = token.split(".", 2)
            issued = datetime.fromtimestamp(int(timestamp), timezone.utc)
        except (ValueError, OverflowError):
            return False
        payload = f"{timestamp}.{challenge}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        age = datetime.now(timezone.utc) - issued
        return (
            len(challenge) == 6 and all(char in CAPTCHA_ALPHABET for char in challenge)
            and age >= timedelta(0) and age < CAPTCHA_LIFETIME
            and hmac.compare_digest(signature, expected)
            and hmac.compare_digest(answer.upper(), challenge)
        )

    def save_visit(self):
        fields = self.read_form()
        if fields is None:
            self.send_error(400, "Invalid form submission")
            return
        captcha_answer = fields.get("captcha_answer", [""])[0].strip()
        captcha_token = fields.get("captcha_token", [""])[0]
        if not self.verify_captcha(captcha_token, captcha_answer):
            self.redirect("/?form_error=captcha#contact")
            return
        name = fields.get("name", [""])[0].strip()
        email = fields.get("email", [""])[0].strip()
        phone = fields.get("phone", [""])[0].strip()
        message = fields.get("message", [""])[0].strip()
        normalized_phone = phone
        if (
            not name or len(name) > 120 or not email or len(email) > 254 or "@" not in email
            or not re.fullmatch(r"[0-9]{10}", normalized_phone)
            or len(message) > 3000
        ):
            self.redirect("/?form_error=invalid#contact")
            return
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with connect_database() as db:
            db.execute(
                "INSERT INTO visits (name, email, phone, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, email, normalized_phone, message, created_at),
            )
        self.redirect("/?submitted=1#contact")

    def save_memberships(self):
        fields = self.read_form()
        if fields is None:
            self.send_error(400, "Invalid membership update")
            return
        title = fields.get("title", [""])[0].strip()
        description = fields.get("description", [""])[0].strip()
        plans = []
        try:
            if not title or len(title) > 80 or len(description) > 240:
                raise ValueError
            for plan_id in (1, 2, 3):
                name = fields.get(f"name_{plan_id}", [""])[0].strip()
                price = fields.get(f"price_{plan_id}", [""])[0].strip()
                features = [fields.get(f"feature{number}_{plan_id}", [""])[0].strip() for number in (1, 2, 3)]
                if not name or len(name) > 40 or not price or len(price) > 8 or not price.replace(".", "", 1).isdigit():
                    raise ValueError
                if any(not feature or len(feature) > 100 for feature in features) or float(price) > 99999:
                    raise ValueError
                plans.append((name, price, *features, plan_id))
        except ValueError:
            self.show_admin("Please enter a title, valid prices, plan names, and all three features for each plan.", status=400)
            return
        with connect_database() as db:
            db.execute("UPDATE membership_settings SET title = ?, description = ? WHERE id = 1", (title, description))
            db.executemany(
                "UPDATE membership_plans SET name = ?, price = ?, feature1 = ?, feature2 = ?, feature3 = ? WHERE id = ?",
                plans,
            )
        self.redirect("/admin?memberships_saved=1&tab=memberships")

    def save_banner(self):
        fields = self.read_form()
        if fields is None:
            self.send_error(400, "Invalid banner update")
            return
        eyebrow = fields.get("eyebrow", [""])[0].strip()
        title = fields.get("banner_title", [""])[0].strip()
        description = fields.get("banner_description", [""])[0].strip()
        button_label = fields.get("button_label", [""])[0].strip()
        button_url = fields.get("button_url", [""])[0].strip()
        image_url = fields.get("image_url", [""])[0].strip()
        valid_anchor = bool(re.fullmatch(r"#[A-Za-z0-9_-]+", button_url))
        try:
            parsed_button_url = urlparse(button_url)
            valid_external_button = parsed_button_url.scheme in ("http", "https") and bool(parsed_button_url.netloc)
            parsed_image_url = urlparse(image_url)
            valid_image = not image_url or (parsed_image_url.scheme == "https" and bool(parsed_image_url.netloc))
        except ValueError:
            valid_external_button = False
            valid_image = False
        valid_fields = (
            eyebrow and len(eyebrow) <= 100 and title and len(title) <= 160
            and description and len(description) <= 400 and button_label and len(button_label) <= 60
            and len(button_url) <= 500 and (valid_anchor or valid_external_button)
            and len(image_url) <= 2048 and valid_image
        )
        if not valid_fields:
            self.show_admin("Check the banner text and button link. Image URLs must use HTTPS.", status=400)
            return
        with connect_database() as db:
            db.execute(
                "UPDATE home_banner SET eyebrow = ?, title = ?, description = ?, button_label = ?, button_url = ?, image_url = ? WHERE id = 1",
                (eyebrow, title, description, button_label, button_url, image_url),
            )
        self.redirect("/admin?banner_saved=1&tab=banner")

    def save_theme(self):
        fields = self.read_form()
        if fields is None:
            self.send_error(400, "Invalid theme update")
            return
        names = ("accent", "accent_hover", "background", "background_elevated", "surface", "text_color", "muted")
        colors = {name: fields.get(name, [""])[0].strip() for name in names}
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in colors.values()):
            self.show_admin("Choose a valid color for every theme field.", status=400)
            return
        with connect_database() as db:
            db.execute(
                "UPDATE site_theme SET accent = ?, accent_hover = ?, background = ?, background_elevated = ?, "
                "surface = ?, text_color = ?, muted = ? WHERE id = 1",
                tuple(colors[name] for name in names),
            )
        self.redirect("/admin?theme_saved=1&tab=theme")

    def save_gallery(self):
        fields, files = self.read_gallery_form()
        if fields is None:
            self.send_error(400, "Invalid gallery update")
            return
        images = []
        pending_uploads = []
        for slot in range(1, 5):
            image_url = fields.get(f"image_url_{slot}", [""])[0].strip()
            alt_text = fields.get(f"alt_text_{slot}", [""])[0].strip()
            caption = fields.get(f"caption_{slot}", [""])[0].strip()
            upload = files.get(f"image_file_{slot}")
            extension = None
            if upload:
                if len(upload) > 5 * 1024 * 1024:
                    self.show_admin("Each gallery upload must be 5 MB or smaller.", status=400)
                    return
                if upload.startswith(b"\xff\xd8\xff"):
                    extension = ".jpg"
                elif upload.startswith(b"\x89PNG\r\n\x1a\n"):
                    extension = ".png"
                elif upload.startswith(b"RIFF") and upload[8:12] == b"WEBP":
                    extension = ".webp"
                if not extension:
                    self.show_admin("Gallery uploads must be valid JPG, PNG, or WebP images.", status=400)
                    return
                image_url = "__upload__"
            try:
                parsed_url = urlparse(image_url)
                valid_url = (
                    not image_url
                    or image_url == "__upload__"
                    or bool(re.fullmatch(r"/uploads/gallery/[0-9a-f]{32}\.(?:jpg|png|webp)", image_url))
                    or (parsed_url.scheme == "https" and bool(parsed_url.netloc))
                )
            except ValueError:
                valid_url = False
            if len(image_url) > 2048 or not valid_url or len(alt_text) > 140 or len(caption) > 80:
                self.show_admin("Gallery images need a valid HTTPS URL; captions and descriptions must fit their limits.", status=400)
                return
            if extension:
                pending_uploads.append((len(images), upload, extension))
            images.append((image_url, alt_text, caption, slot))
        try:
            os.makedirs(GALLERY_UPLOAD_DIR, exist_ok=True)
            for image_index, upload, extension in pending_uploads:
                filename = f"{secrets.token_hex(16)}{extension}"
                with open(os.path.join(GALLERY_UPLOAD_DIR, filename), "xb") as image_file:
                    image_file.write(upload)
                image_url, alt_text, caption, slot = images[image_index]
                images[image_index] = (f"/uploads/gallery/{filename}", alt_text, caption, slot)
        except OSError:
            self.show_admin("The image upload could not be saved. Check the server folder permissions and try again.", status=500)
            return
        with connect_database() as db:
            db.executemany(
                "UPDATE gallery_images SET image_url = ?, alt_text = ?, caption = ? WHERE slot = ?",
                images,
            )
        self.redirect("/admin?gallery_saved=1&tab=gallery-editor")

    def serve_home(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as page:
            content = page.read()
        with connect_database() as db:
            db.row_factory = sqlite3.Row
            plans = db.execute("SELECT id, name, price, featured, feature1, feature2, feature3 FROM membership_plans ORDER BY id").fetchall()
            settings = db.execute("SELECT title, description FROM membership_settings WHERE id = 1").fetchone()
            banner = db.execute("SELECT eyebrow, title, description, button_label, button_url, image_url FROM home_banner WHERE id = 1").fetchone()
            theme = db.execute("SELECT * FROM site_theme WHERE id = 1").fetchone()
            gallery = db.execute("SELECT slot, image_url, alt_text, caption FROM gallery_images ORDER BY slot").fetchall()
        cards = []
        for plan in plans:
            featured = bool(plan["featured"])
            features = "".join(f"<li>{html.escape(plan[f'feature{number}'])}</li>" for number in (1, 2, 3))
            badge = '<p class="badge">Most popular</p>' if featured else ""
            card_class = "price-card featured" if featured else "price-card"
            button_class = "btn btn-block" if featured else "btn btn-outline btn-block"
            cards.append(
                f'<article class="{card_class}">{badge}<h3>{html.escape(plan["name"])}</h3>'
                f'<p class="price"><span>₹</span>{html.escape(plan["price"])}<span class="price-period">/mo</span></p>'
                f'<ul>{features}</ul><a class="{button_class}" href="#contact">Choose {html.escape(plan["name"])}</a></article>'
            )
        section_header = (
            f'<h2>{html.escape(settings["title"])}</h2>'
            f'<p>{html.escape(settings["description"])}</p>'
        )
        content = content.replace("<!-- MEMBERSHIP_HEADER -->", section_header)
        content = content.replace("<!-- MEMBERSHIP_CARDS -->", "\n".join(cards))
        challenge, token = self.new_captcha()
        captcha_markup = (
            '<div class="captcha-row"><span class="captcha-prompt">Security check</span>'
            f'<span class="captcha-code" id="captcha-code" aria-label="Captcha code" aria-live="polite">{challenge}</span>'
            '<button class="captcha-refresh" id="captcha-refresh" type="button" aria-label="Refresh captcha">Refresh</button></div>'
            '<label>Enter the letters and numbers shown'
            '<input type="text" name="captcha_answer" minlength="6" maxlength="6" '
            'pattern="[A-Za-z0-9]{6}" inputmode="text" autocapitalize="characters" '
            'required autocomplete="off" aria-describedby="captcha-help"></label>'
            f'<input type="hidden" name="captcha_token" value="{html.escape(token, quote=True)}">'
            '<small class="captcha-help" id="captcha-help">Enter all 6 characters. You can refresh the code if needed.</small>'
        )
        content = content.replace("<!-- CONTACT_CAPTCHA -->", captcha_markup)
        gallery_cards = []
        for image in gallery:
            if not image["image_url"]:
                continue
            loading = "eager" if image["slot"] == 1 else "lazy"
            gallery_cards.append(
                f'<figure class="gallery-card tilt-card reveal"><div class="gallery-image-wrap">'
                f'<img src="{html.escape(image["image_url"], quote=True)}" alt="{html.escape(image["alt_text"], quote=True)}" loading="{loading}" decoding="async">'
                f'</div><figcaption>{html.escape(image["caption"])}</figcaption></figure>'
            )
        content = content.replace("<!-- GYM_GALLERY -->", "\n".join(gallery_cards))
        theme_variables = {
            "--accent": theme["accent"], "--accent-hover": theme["accent_hover"], "--bg": theme["background"],
            "--bg-elevated": theme["background_elevated"], "--surface": theme["surface"],
            "--text": theme["text_color"], "--muted": theme["muted"],
        }
        theme_css = "<style id=\"site-theme\">:root{" + "".join(
            f"{name}:{html.escape(value)};" for name, value in theme_variables.items()
        ) + "}</style>"
        content = content.replace("<!-- SITE_THEME -->", theme_css)
        banner_image = f'<img class="hero-image" src="{html.escape(banner["image_url"], quote=True)}" alt="" aria-hidden="true">' if banner["image_url"] else ""
        banner_title = "<br>".join(html.escape(line) for line in banner["title"].splitlines())
        replacements = {
            "<!-- HOME_BANNER_EYEBROW -->": html.escape(banner["eyebrow"]),
            "<!-- HOME_BANNER_TITLE -->": banner_title,
            "<!-- HOME_BANNER_DESCRIPTION -->": html.escape(banner["description"]),
            "<!-- HOME_BANNER_BUTTON_LABEL -->": html.escape(banner["button_label"]),
            "<!-- HOME_BANNER_BUTTON_URL -->": html.escape(banner["button_url"], quote=True),
            "<!-- HOME_BANNER_IMAGE -->": banner_image,
        }
        for marker, value in replacements.items():
            content = content.replace(marker, value)
        self.respond_html(content)

    def login(self):
        fields = self.read_form()
        username = fields.get("username", [""])[0] if fields else ""
        password = fields.get("password", [""])[0] if fields else ""
        if hmac.compare_digest(username, ADMIN_USERNAME) and hmac.compare_digest(password, ADMIN_PASSWORD):
            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            cookie_value = signed_session(timestamp)
            self.send_response(303)
            self.send_header("Location", "/admin")
            self.send_header("Set-Cookie", f"admin_session={cookie_value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200")
            self.end_headers()
            return
        self.show_login("Login failed. Check your username and password.", status=401)

    def show_login(self, error="", status=200):
        alert = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        self.respond_html(f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin login | IronPulse</title><style>{ADMIN_CSS}</style><body><main class="panel"><a href="/" class="muted">← IronPulse website</a>
<h1>Admin login</h1><p class="muted">Sign in to view visit requests.</p>{alert}<form action="/admin/login" method="post">
<label>Username<input name="username" autocomplete="username" required></label><label>Password<input type="password" name="password" autocomplete="current-password" required></label>
<button type="submit">Sign in</button></form></main></body></html>""", status)

    def show_admin(self, error="", status=200):
        query = parse_qs(urlparse(self.path).query)
        search_term = query.get("q", [""])[0].strip()[:120]
        from_value = query.get("date_from", [""])[0][:16]
        to_value = query.get("date_to", [""])[0][:16]
        date_filters = []
        filter_params = []

        def utc_filter_value(value, include_selected_minute=False):
            if not value:
                return ""
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return ""
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=INDIA_TIMEZONE)
            if include_selected_minute:
                parsed += timedelta(minutes=1) - timedelta(seconds=1)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")

        from_utc = utc_filter_value(from_value)
        to_utc = utc_filter_value(to_value, include_selected_minute=True)
        if from_utc:
            date_filters.append("created_at >= ?")
            filter_params.append(from_utc)
        if to_utc:
            date_filters.append("created_at <= ?")
            filter_params.append(to_utc)
        if search_term:
            like_operator = "ILIKE" if DATABASE_URL else "LIKE"
            date_filters.append(
                f"(name {like_operator} ? OR email {like_operator} ? OR phone {like_operator} ? OR message {like_operator} ?)"
            )
            search_pattern = f"%{search_term}%"
            filter_params.extend((search_pattern, search_pattern, search_pattern, search_pattern))
        where_clause = " WHERE " + " AND ".join(date_filters) if date_filters else ""
        try:
            requested_page = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            requested_page = 1
        page_size = 10
        with connect_database() as db:
            db.row_factory = sqlite3.Row
            total_contacts = db.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            filtered_contacts = db.execute(
                "SELECT COUNT(*) FROM visits" + where_clause, filter_params
            ).fetchone()[0]
            page_count = max(1, (filtered_contacts + page_size - 1) // page_size)
            current_page = min(requested_page, page_count)
            rows = db.execute(
                "SELECT id, name, email, phone, message, created_at FROM visits" + where_clause
                + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*filter_params, page_size, (current_page - 1) * page_size),
            ).fetchall()
            plans = db.execute("SELECT * FROM membership_plans ORDER BY id").fetchall()
            settings = db.execute("SELECT title, description FROM membership_settings WHERE id = 1").fetchone()
            banner = db.execute("SELECT eyebrow, title, description, button_label, button_url, image_url FROM home_banner WHERE id = 1").fetchone()
            theme = db.execute("SELECT * FROM site_theme WHERE id = 1").fetchone()
            gallery = db.execute("SELECT slot, image_url, alt_text, caption FROM gallery_images ORDER BY slot").fetchall()
        if rows:
            def format_received(value):
                try:
                    received = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if received.tzinfo is None:
                        received = received.replace(tzinfo=timezone.utc)
                    return received.astimezone(INDIA_TIMEZONE).strftime("%d %b %Y, %I:%M %p IST")
                except ValueError:
                    return value

            entries = "".join(
                "<tr>"
                f"<td>{html.escape(format_received(row['created_at']))}</td>"
                f"<td>{html.escape(row['name'])}</td>"
                f"<td>{html.escape(row['phone'])}</td>"
                f"<td><a href=\"mailto:{html.escape(row['email'], quote=True)}\">{html.escape(row['email'])}</a></td>"
                f"<td class=\"message\">{html.escape(row['message']) or '<span class=\"muted\">—</span>'}</td>"
                "</tr>"
                for row in rows
            )
        else:
            empty_message = "No requests match these filters." if search_term or from_utc or to_utc else "No visit requests yet."
            entries = f'<tr><td colspan="5" class="empty">{html.escape(empty_message)}</td></tr>'
        result_start = (current_page - 1) * page_size + 1 if filtered_contacts else 0
        result_end = min(current_page * page_size, filtered_contacts)
        range_label = f"{result_start}–{result_end} of {filtered_contacts} requests"
        filter_query = {
            "tab": "contacts",
            "q": search_term,
            "date_from": from_value,
            "date_to": to_value,
        }
        previous_link = (
            f'<a class="page-link" href="/admin?{html.escape(urlencode({**filter_query, "page": current_page - 1}), quote=True)}">Previous</a>'
            if current_page > 1 else '<span class="page-link disabled" aria-disabled="true">Previous</span>'
        )
        next_link = (
            f'<a class="page-link" href="/admin?{html.escape(urlencode({**filter_query, "page": current_page + 1}), quote=True)}">Next</a>'
            if current_page < page_count else '<span class="page-link disabled" aria-disabled="true">Next</span>'
        )
        contact_controls = (
            '<form class="contact-filters" action="/admin" method="get">'
            '<input type="hidden" name="tab" value="contacts">'
            f'<label>Search name, mobile, email, or message<input type="search" name="q" maxlength="120" value="{html.escape(search_term, quote=True)}" placeholder="Type to search"></label>'
            f'<label>From (India time)<input type="datetime-local" name="date_from" value="{html.escape(from_value, quote=True)}"></label>'
            f'<label>To (India time)<input type="datetime-local" name="date_to" value="{html.escape(to_value, quote=True)}"></label>'
            '<div class="filter-actions"><button type="submit">Search</button><a href="/admin?tab=contacts">Clear</a></div></form>'
            f'<div class="pagination" aria-label="Contact request pages">{previous_link}<span>Page {current_page} of {page_count} · {range_label}</span>{next_link}</div>'
        )
        plan_editors = "".join(
            f'<fieldset><legend>{html.escape(plan["name"])}</legend>'
            f'<div class="plan-fields"><label>Plan name<input name="name_{plan["id"]}" maxlength="40" value="{html.escape(plan["name"], quote=True)}" required></label>'
            f'<label>Monthly price (INR ₹)<input type="number" name="price_{plan["id"]}" min="0" max="99999" step="0.01" value="{html.escape(plan["price"], quote=True)}" required></label></div>'
            + "".join(
                f'<label>Feature {number}<input name="feature{number}_{plan["id"]}" maxlength="100" value="{html.escape(plan[f"feature{number}"], quote=True)}" required></label>'
                for number in (1, 2, 3)
            )
            + "</fieldset>"
            for plan in plans
        )
        theme_fields = (
            ("accent", "Accent color", theme["accent"]),
            ("accent_hover", "Accent hover", theme["accent_hover"]),
            ("background", "Page background", theme["background"]),
            ("background_elevated", "Raised background", theme["background_elevated"]),
            ("surface", "Cards and panels", theme["surface"]),
            ("text_color", "Main text", theme["text_color"]),
            ("muted", "Muted text", theme["muted"]),
        )
        theme_editors = "".join(
            f'<label class="color-field"><span>{label}</span><input type="color" name="{name}" value="{html.escape(value, quote=True)}" aria-label="{label}"></label>'
            for name, label, value in theme_fields
        )
        gallery_editors = "".join(
            f'<fieldset class="gallery-editor-card"><legend>Photo {image["slot"]}</legend>'
            + (f'<img class="gallery-preview" src="{html.escape(image["image_url"], quote=True)}" alt="">' if image["image_url"] else '<div class="gallery-preview gallery-empty">No image selected</div>')
            + f'<label>Photo URL (optional, HTTPS)<input type="text" name="image_url_{image["slot"]}" maxlength="2048" value="{html.escape(image["image_url"], quote=True)}" placeholder="https://images.example.com/gym.jpg"></label>'
            + f'<label>Upload photo (JPG, PNG, WebP; max 5 MB)<input type="file" name="image_file_{image["slot"]}" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"></label>'
            + f'<label>Image description<input name="alt_text_{image["slot"]}" maxlength="140" value="{html.escape(image["alt_text"], quote=True)}" required></label>'
            + f'<label>Caption<input name="caption_{image["slot"]}" maxlength="80" value="{html.escape(image["caption"], quote=True)}" required></label>'
            + "</fieldset>"
            for image in gallery
        )
        allowed_tabs = {"overview", "contacts", "banner", "theme", "gallery-editor", "memberships"}
        active_tab = query.get("tab", [""])[0]
        if active_tab not in allowed_tabs and error:
            active_tab = {
                "/admin/memberships": "memberships",
                "/admin/banner": "banner",
                "/admin/theme": "theme",
                "/admin/gallery": "gallery-editor",
            }.get(urlparse(self.path).path, "overview")
        if active_tab not in allowed_tabs:
            active_tab = "overview"
        panel_visibility = {
            tab: "" if tab == active_tab else "hidden"
            for tab in allowed_tabs
        }
        saved_notice = '<p class="success" role="status">Memberships updated.</p>' if query.get("memberships_saved") == ["1"] else ""
        banner_saved_notice = '<p class="success" role="status">Homepage banner updated.</p>' if query.get("banner_saved") == ["1"] else ""
        theme_saved_notice = '<p class="success" role="status">Theme colors updated.</p>' if query.get("theme_saved") == ["1"] else ""
        gallery_saved_notice = '<p class="success" role="status">Gym gallery updated.</p>' if query.get("gallery_saved") == ["1"] else ""
        error_notice = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        self.respond_html(f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin dashboard | IronPulse</title><style>{ADMIN_CSS}</style><body><div class="admin-shell">
<aside class="sidebar"><a class="admin-brand" href="/admin"><span class="brand-mark">IP</span><span>IronPulse<small>ADMIN PANEL</small></span></a>
<p class="nav-label">WORKSPACE</p><nav class="admin-nav" aria-label="Admin sections">
<a href="/admin?tab=overview">Overview</a><a href="/admin?tab=contacts">Contact requests</a><a href="/admin?tab=banner">Homepage banner</a><a href="/admin?tab=theme">Color theme</a><a href="/admin?tab=gallery-editor">Gym gallery</a><a href="/admin?tab=memberships">Memberships</a></nav>
<div class="sidebar-bottom"><a href="/" target="_blank" rel="noopener">View website ↗</a>
<form action="/admin/logout" method="post"><button class="secondary" type="submit">Sign out</button></form></div></aside>
<main class="admin-main"><section id="overview" class="admin-panel" {panel_visibility['overview']}><header><div><p class="muted breadcrumb">ADMIN / OVERVIEW</p><h1>Dashboard</h1><p class="muted">Manage your website content and visit requests.</p></div><a class="theme-shortcut" href="/admin?tab=theme">Edit frontend colors</a></header>
<div class="summary-cards"><article><span class="muted">Contact requests</span><strong>{total_contacts}</strong></article><article><span class="muted">Membership plans</span><strong>{len(plans)}</strong></article><article><span class="muted">Gallery photos</span><strong>{sum(bool(image['image_url']) for image in gallery)}</strong></article></div></section>
<section id="theme" class="admin-panel editor theme-editor" {panel_visibility['theme']}><h2>Frontend color theme</h2><p class="muted">Adjust the main colors used across the homepage.</p>{theme_saved_notice}{error_notice}
<form action="/admin/theme" method="post"><div class="theme-color-grid">{theme_editors}</div><button type="submit">Save theme</button></form></section>
<section id="contacts" class="admin-panel admin-section" {panel_visibility['contacts']}><div class="section-title"><div><p class="muted breadcrumb">INBOX</p><h2>Contact requests</h2><p class="muted">Search by name, mobile, email, or message; filter by India date and time.</p></div><span class="count-pill">{filtered_contacts} matching</span></div>
{contact_controls}<div class="table-wrap"><table><thead><tr><th>Received (IST)</th><th>Name</th><th>Mobile</th><th>Email</th><th>Message</th></tr></thead><tbody>{entries}</tbody></table></div></section>
<section id="banner" class="admin-panel editor" {panel_visibility['banner']}><h2>Edit homepage banner</h2><p class="muted">Update the large hero banner shown at the top of the homepage.</p>{banner_saved_notice}{error_notice}
<form action="/admin/banner" method="post"><label>Small heading<input name="eyebrow" maxlength="100" value="{html.escape(banner['eyebrow'], quote=True)}" required></label>
<label>Banner title<textarea name="banner_title" rows="2" maxlength="160" required>{html.escape(banner['title'])}</textarea></label>
<label>Description<textarea name="banner_description" rows="3" maxlength="400" required>{html.escape(banner['description'])}</textarea></label>
<div class="plan-fields"><label>Button text<input name="button_label" maxlength="60" value="{html.escape(banner['button_label'], quote=True)}" required></label>
<label>Button link<input name="button_url" maxlength="500" value="{html.escape(banner['button_url'], quote=True)}" placeholder="#pricing or https://example.com" required></label></div>
<label>Background image URL (optional, HTTPS)<input type="url" name="image_url" maxlength="2048" value="{html.escape(banner['image_url'], quote=True)}" placeholder="https://example.com/banner.jpg"></label>
<button type="submit">Save banner</button></form></section>
<section id="gallery-editor" class="admin-panel editor" {panel_visibility['gallery-editor']}><h2>Gym photo gallery</h2><p class="muted">Replace the four homepage photos with an HTTPS URL or upload a JPG, PNG, or WebP image. An uploaded photo replaces its URL; clear both to remove a photo.</p>{gallery_saved_notice}{error_notice}
<form action="/admin/gallery" method="post" enctype="multipart/form-data"><div class="gallery-admin-grid">{gallery_editors}</div><button type="submit">Save gallery</button></form></section>
<section id="memberships" class="admin-panel editor" {panel_visibility['memberships']}><h2>Edit memberships</h2><p class="muted">Changes are saved and shown on the website pricing section.</p>{saved_notice}{error_notice}
<form action="/admin/memberships" method="post"><label>Section title<input name="title" maxlength="80" value="{html.escape(settings['title'], quote=True)}" required></label>
<label>Section description<input name="description" maxlength="240" value="{html.escape(settings['description'], quote=True)}"></label>{plan_editors}
<button type="submit">Save memberships</button></form></section></main></div></body></html>""", status)

ADMIN_CSS = """
:root{color-scheme:dark;--bg:#0c0d0f;--panel:#14161a;--line:rgba(255,255,255,.1);--text:#f2f4f8;--muted:#9aa3b2;--blue:#2583ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,sans-serif;padding:32px}
.panel{max-width:440px;margin:8vh auto;background:var(--panel);padding:32px;border:1px solid var(--line);border-radius:14px}
.wide{max-width:1200px;margin:5vh auto}h1{margin:12px 0 4px}p{margin:4px 0 20px}.muted{color:var(--muted)}a{color:#75b2ff}label{display:block;margin:18px 0}input{display:block;width:100%;margin-top:6px;padding:12px;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;font:inherit}
button{padding:11px 18px;border:0;border-radius:8px;background:var(--blue);color:white;font:600 15px system-ui;cursor:pointer}.secondary{background:#252a33}header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:24px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:14px 16px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}.message{white-space:pre-wrap;min-width:220px;max-width:480px;overflow-wrap:anywhere}.empty{text-align:center;color:var(--muted);padding:40px}.error{color:#ff9292}
.editor{margin-top:40px;padding:24px;background:var(--panel);border:1px solid var(--line);border-radius:12px}.editor h2{margin:0}.editor fieldset{margin:24px 0;padding:4px 20px 16px;border:1px solid var(--line);border-radius:10px}.editor legend{padding:0 8px;font-weight:700}.plan-fields{display:grid;grid-template-columns:1fr 1fr;gap:20px}textarea{display:block;width:100%;margin-top:6px;padding:12px;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;font:inherit;resize:vertical}.success{color:#8dd7a2}
@media(max-width:650px){body{padding:16px}.panel{padding:24px}.wide{margin-top:20px}header{align-items:flex-start}th,td{padding:10px}.plan-fields{grid-template-columns:1fr;gap:0}.editor{padding:18px}.editor fieldset{padding-inline:14px}}
body{padding:0}
.admin-shell{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:100vh}
.sidebar{position:sticky;top:0;height:100vh;padding:26px 18px;background:#111318;border-right:1px solid var(--line);display:flex;flex-direction:column}
.admin-brand{display:flex;align-items:center;gap:12px;color:var(--text);font-weight:700;text-decoration:none;padding:4px 8px 28px}
.admin-brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.14em;margin-top:2px}
.brand-mark{display:grid;place-items:center;width:40px;height:40px;border-radius:11px;background:var(--blue);color:white;font-weight:800}
.nav-label,.breadcrumb{font-size:11px;letter-spacing:.12em;font-weight:700;color:var(--muted)}.nav-label{padding:0 12px;margin:4px 0 8px}
.admin-nav{display:grid;gap:5px}.admin-nav a{display:block;padding:11px 12px;border-radius:8px;color:var(--muted);text-decoration:none;font-size:14px;font-weight:500}
.admin-nav a:hover,.admin-nav a:focus{background:#20242c;color:var(--text)}
.sidebar-bottom{margin-top:auto;display:grid;gap:16px;padding:12px 8px 4px}.sidebar-bottom>a{text-decoration:none;font-size:14px}.sidebar-bottom form{margin:0}.sidebar-bottom button{width:100%}
.admin-main{min-width:0;max-width:1500px;width:100%;padding:38px clamp(22px,4vw,64px) 64px;margin:0 auto}
.admin-main>header{margin:0 0 26px}.admin-main h1{font-size:30px}.admin-main h2{margin:0;font-size:22px}
.admin-panel[hidden]{display:none!important}
.admin-panel-active>.section-title{margin-top:0}
.theme-shortcut{display:inline-flex;align-items:center;justify-content:center;padding:11px 16px;border-radius:8px;background:var(--blue);color:#fff;text-decoration:none;font-weight:650;white-space:nowrap}
.theme-editor{margin:0 0 28px!important}
.admin-main .breadcrumb{margin:0 0 6px}.admin-main header p:last-child,.section-title p:last-child{margin-bottom:0}
.summary-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:0 0 38px}
.summary-cards article{padding:20px;background:var(--panel);border:1px solid var(--line);border-radius:12px;display:grid;gap:10px}
.summary-cards strong{font-size:28px}.admin-section,.editor{scroll-margin-top:24px}
.admin-section{margin:0 0 34px}.section-title{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}
.count-pill{padding:5px 10px;border-radius:999px;background:#20242c;color:var(--muted);font-size:12px;white-space:nowrap}
.admin-main .editor{margin-top:28px}.admin-main .editor h2{margin:0}
.theme-color-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:20px 0 24px}
.color-field{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0;padding:12px 14px;background:#0c0d0f;border:1px solid var(--line);border-radius:10px;color:var(--muted);font-size:14px}
.color-field input[type=color]{width:54px;height:40px;margin:0;padding:3px;border:1px solid var(--line);border-radius:8px;background:#0c0d0f;cursor:pointer}
.gallery-admin-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:20px 0 24px}
.gallery-editor-card input[type=file]{padding:8px;font-size:13px}
.gallery-editor-card{min-width:0;margin:0!important;padding:14px!important;background:#0c0d0f}
.gallery-editor-card legend{color:var(--text)}
.gallery-preview{width:100%;height:150px;object-fit:cover;border-radius:8px;border:1px solid var(--line);background:#08090b}
.gallery-empty{display:grid;place-items:center;color:var(--muted);font-size:13px}
.contact-filters{display:grid;grid-template-columns:minmax(220px,2fr) repeat(2,minmax(190px,1fr)) auto;align-items:end;gap:12px;margin:0 0 18px;padding:16px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.contact-filters label{margin:0;color:var(--muted);font-size:13px}.contact-filters input{font-size:14px}.filter-actions{display:flex;align-items:center;gap:14px;padding-bottom:18px}.filter-actions a{white-space:nowrap;text-decoration:none}
.pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:16px 0;color:var(--muted);font-size:13px}.page-link{display:inline-flex;align-items:center;justify-content:center;min-width:82px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;color:var(--text);text-decoration:none}.page-link:hover{background:#20242c}.page-link.disabled{color:#667080}
@media(max-width:760px){body{padding:0}.admin-shell{grid-template-columns:1fr}.sidebar{position:relative;height:auto;padding:16px;border-right:0;border-bottom:1px solid var(--line)}.admin-brand{padding:0 4px 16px}.nav-label{padding:0 4px}.admin-nav{grid-template-columns:repeat(2,minmax(0,1fr))}.sidebar-bottom{grid-template-columns:1fr 1fr;align-items:center;margin-top:14px;padding:8px 4px 0}.admin-main{padding:26px 16px 48px}.summary-cards{gap:10px}.summary-cards article{padding:14px}.summary-cards strong{font-size:23px}}
@media(max-width:900px){.contact-filters{grid-template-columns:repeat(2,minmax(0,1fr))}.contact-filters label:first-of-type{grid-column:1/-1}.filter-actions{padding-bottom:0}}
@media(max-width:460px){.summary-cards{grid-template-columns:1fr}.summary-cards article{display:flex;justify-content:space-between;align-items:center}.section-title{align-items:flex-start}.admin-nav{grid-template-columns:1fr 1fr}.contact-filters{grid-template-columns:1fr}.contact-filters label:first-of-type{grid-column:auto}.pagination{flex-wrap:wrap}.pagination span{order:-1;width:100%;text-align:center}}
"""


def main():
    global SESSION_SECRET
    if not ADMIN_PASSWORD:
        raise SystemExit("Set ADMIN_PASSWORD before starting the server. See README.md for instructions.")
    if len(ADMIN_PASSWORD) < 12:
        raise SystemExit("ADMIN_PASSWORD must be at least 12 characters long.")
    if not SESSION_SECRET:
        SESSION_SECRET = secrets.token_urlsafe(48)
        print("Generated a temporary session secret for this run.")
    initialize_database()
    print("Database: Supabase PostgreSQL" if DATABASE_URL else f"Database: local SQLite ({DATABASE})")
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    server = ThreadingHTTPServer((host, port), WebsiteHandler)
    print(f"IronPulse is running on {host}:{port} (admin: /admin). Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
