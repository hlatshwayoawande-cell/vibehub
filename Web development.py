from flask import Flask, request, redirect, url_for, jsonify, flash, get_flashed_messages
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import uuid
import re

app = Flask(__name__)
app.secret_key = 'super-secret-key-change-this-later'

# --- Upload configuration ---
UPLOAD_FOLDER = 'static/uploads'
AVATAR_FOLDER = 'static/avatars'
VIDEO_FOLDER = 'static/videos'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}
MAX_FILE_SIZE = 20 * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'mp4', 'webm', 'mov'}

def extract_hashtags(text):
    return re.findall(r'#(\w+)', text)

# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    profile_pic = db.Column(db.String(300), nullable=True, default=None)
    is_premium = db.Column(db.Boolean, default=False)
    premium_since = db.Column(db.DateTime, nullable=True)
    has_used_karma_unlock = db.Column(db.Boolean, default=False)
    signup_order = db.Column(db.Integer, nullable=True)
    premium_expiry = db.Column(db.DateTime, nullable=True)
    is_owner = db.Column(db.Boolean, default=False)
    
    posts = db.relationship('Post', backref='author', lazy=True, cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='author', lazy=True, cascade="all, delete-orphan")
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True, cascade="all, delete-orphan")
    received_messages = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True, cascade="all, delete-orphan")

    def get_karma(self):
        if self.is_owner:
            return 999999999
        return sum(post.score for post in self.posts)

    def is_founding_member(self):
        if not self.signup_order or self.signup_order > 100:
            return False
        if self.premium_expiry:
            return self.premium_expiry > datetime.utcnow()
        return self.is_premium

    def get_premium_status(self):
        if self.is_founding_member():
            days_left = (self.premium_expiry - datetime.utcnow()).days
            return f"Founding Member (Trial: {days_left} days left)"
        elif self.is_premium:
            return "Premium Member"
        else:
            return "Free Member"

class Waitlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Hashtag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    posts = db.relationship('PostHashtag', backref='hashtag', lazy=True, cascade="all, delete-orphan")
    
    def get_count(self):
        return len(self.posts)
    
    def get_score(self):
        week_ago = datetime.utcnow() - timedelta(days=7)
        recent = 0
        for ph in self.posts:
            if ph.post.timestamp > week_ago:
                recent += 1
        return recent

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer, default=0)
    image_url = db.Column(db.String(300), nullable=True, default=None)
    video_url = db.Column(db.String(300), nullable=True, default=None)
    votes = db.relationship('Vote', backref='post', lazy=True, cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='post', lazy=True, cascade="all, delete-orphan")
    hashtags = db.relationship('PostHashtag', backref='post', lazy=True, cascade="all, delete-orphan")

class PostHashtag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    hashtag_id = db.Column(db.Integer, db.ForeignKey('hashtag.id'), nullable=False)

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    value = db.Column(db.Integer, nullable=False)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_read = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_user_vote(post_id):
    vote = Vote.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    return vote.value if vote else 0

def get_avatar_url(user, size=40):
    if user.profile_pic and user.profile_pic.strip():
        return user.profile_pic.strip()
    else:
        return f"https://ui-avatars.com/api/?name={user.username}&background=6366f1&color=fff&size={size}"

def render_badge(user):
    if user.is_founding_member():
        return '⭐ '
    elif user.is_premium:
        return '👑 '
    else:
        return ''

def is_premium_user(user):
    if user.is_founding_member():
        return True
    return user.is_premium

def check_and_expire_trials(user):
    if user.signup_order and user.signup_order <= 100 and user.premium_expiry:
        if user.premium_expiry < datetime.utcnow():
            user.is_premium = False
            db.session.commit()
            return True
    return False

def linkify_hashtags(text):
    def replacer(match):
        tag = match.group(1)
        return f'<a href="/hashtag/{tag}" class="text-decoration-none fw-semibold" style="color:var(--accent);">#{tag}</a>'
    return re.sub(r'#(\w+)', replacer, text)

def get_unread_count():
    if current_user.is_authenticated:
        return Message.query.filter_by(receiver_id=current_user.id, is_read=False).count()
    return 0

# --- HELPER: Base HTML ---
def get_base_head(title):
    unread = get_unread_count() if current_user.is_authenticated else 0
    unread_badge = f'<span class="badge bg-danger rounded-pill" style="font-size:0.5rem;position:absolute;top:-4px;right:-8px;">{unread}</span>' if unread > 0 else ''
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{title}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            :root {{
                --bg-body: #f8f9fa;
                --bg-card: #ffffff;
                --bg-navbar: #ffffff;
                --text-color: #0f1419;
                --text-muted: #536471;
                --border-color: #eff3f4;
                --shadow: 0 1px 3px rgba(0,0,0,0.05);
                --accent: #6366f1;
                --accent-hover: #4f46e5;
                --gold: #f59e0b;
                --toast-bg: #1e293b;
                --toast-color: #f8fafc;
            }}
            html.dark-mode {{
                --bg-body: #000000;
                --bg-card: #16181c;
                --bg-navbar: #16181c;
                --text-color: #e7e9ea;
                --text-muted: #71767b;
                --border-color: #2f3336;
                --shadow: 0 1px 3px rgba(0,0,0,0.5);
                --accent: #818cf8;
                --accent-hover: #6366f1;
                --gold: #fbbf24;
                --toast-bg: #e7e9ea;
                --toast-color: #0f1419;
            }}
            
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            
            body {{ 
                background: var(--bg-body); 
                color: var(--text-color); 
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                transition: background 0.2s ease, color 0.2s ease;
                padding-top: 55px;
                padding-bottom: 75px;
                -webkit-font-smoothing: antialiased;
            }}
            
            .app-top-nav {{
                background: var(--bg-navbar) !important;
                border-bottom: 1px solid var(--border-color);
                padding: 8px 16px;
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
                box-shadow: var(--shadow);
                height: 55px;
                display: flex;
                align-items: center;
            }}
            .app-top-nav .brand {{
                font-weight: 800;
                font-size: 1.3rem;
                color: var(--text-color);
                text-decoration: none;
                letter-spacing: -0.5px;
            }}
            
            .app-bottom-nav {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                background: var(--bg-navbar);
                border-top: 1px solid var(--border-color);
                display: flex;
                justify-content: space-around;
                align-items: center;
                padding: 6px 0 env(safe-area-inset-bottom);
                height: 70px;
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
                z-index: 999;
                box-shadow: 0 -2px 10px rgba(0,0,0,0.03);
            }}
            .bottom-nav-item {{
                display: flex;
                flex-direction: column;
                align-items: center;
                text-decoration: none;
                color: var(--text-muted);
                font-size: 0.65rem;
                font-weight: 500;
                transition: 0.2s;
                gap: 2px;
                padding: 4px 12px;
                border-radius: 20px;
                min-width: 50px;
                position: relative;
            }}
            .bottom-nav-item i {{ font-size: 1.5rem; line-height: 1; }}
            .bottom-nav-item.active {{ color: var(--accent); }}
            .bottom-nav-item.active i {{ color: var(--accent); }}
            .bottom-nav-item:hover {{ color: var(--accent); }}
            
            .feed-post {{
                background: var(--bg-card);
                padding: 12px 16px;
                border-bottom: 1px solid var(--border-color);
                transition: background 0.1s;
            }}
            .feed-post:hover {{ background: var(--bg-body); }}
            .post-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }}
            .post-username {{ font-weight: 700; font-size: 0.95rem; color: var(--text-color); }}
            .post-timestamp {{ font-size: 0.8rem; color: var(--text-muted); }}
            .post-content {{ font-size: 0.95rem; line-height: 1.5; margin: 4px 0 8px 0; white-space: pre-wrap; word-break: break-word; }}
            .post-content a {{ color: var(--accent); }}
            .post-actions {{ display: flex; align-items: center; gap: 20px; margin-top: 6px; }}
            .action-btn-group {{ display: flex; align-items: center; gap: 4px; color: var(--text-muted); text-decoration: none; font-size: 0.9rem; transition: 0.2s; padding: 4px 6px; border-radius: 20px; }}
            .action-btn-group:hover {{ background: rgba(99, 102, 241, 0.08); color: var(--accent); }}
            .action-btn-group .icon {{ font-size: 1.3rem; line-height: 1; }}
            .action-btn-group .count {{ font-size: 0.85rem; font-weight: 500; min-width: 18px; }}
            .vote-up-active {{ color: #22c55e !important; }}
            .vote-down-active {{ color: #ef4444 !important; }}
            
            .comment-thread {{ margin-top: 10px; padding-left: 50px; border-left: 2px solid var(--border-color); }}
            .comment-item {{ display: flex; gap: 8px; margin-bottom: 8px; }}
            .comment-avatar {{ width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0; background: var(--border-color); }}
            .comment-body {{ flex: 1; }}
            .comment-username {{ font-weight: 600; font-size: 0.8rem; }}
            .comment-time {{ font-size: 0.7rem; color: var(--text-muted); margin-left: 6px; }}
            .comment-text {{ font-size: 0.9rem; margin-bottom: 2px; }}
            
            .composer-wrapper {{
                background: var(--bg-card);
                padding: 12px 16px;
                border-bottom: 1px solid var(--border-color);
                position: sticky;
                top: 55px;
                z-index: 100;
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
            }}
            .composer-input {{
                border: none !important;
                background: var(--bg-body) !important;
                border-radius: 30px !important;
                padding: 10px 18px !important;
                font-size: 0.95rem !important;
                color: var(--text-color) !important;
                flex: 1;
            }}
            .composer-input:focus {{ box-shadow: 0 0 0 2px var(--accent) !important; }}
            
            .profile-header {{ background: var(--bg-card); padding: 20px; text-align: center; border-bottom: 1px solid var(--border-color); }}
            .profile-avatar {{ width: 90px; height: 90px; border-radius: 50%; border: 3px solid var(--accent); object-fit: cover; }}
            .lb-item {{ background: var(--bg-card); padding: 12px 16px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; gap: 12px; }}
            
            .toast-container {{ position: fixed; top: 70px; right: 16px; z-index: 9999; max-width: 320px; }}
            .toast-custom {{ background: var(--toast-bg); color: var(--toast-color); border-radius: 16px; padding: 12px 18px; margin-bottom: 8px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); transform: translateX(120%); opacity: 0; transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1); }}
            .toast-custom.show {{ transform: translateX(0); opacity: 1; }}
            .toast-custom.success {{ border-left: 4px solid #22c55e; }}
            .toast-custom.error {{ border-left: 4px solid #ef4444; }}
            .toast-custom.info {{ border-left: 4px solid #6366f1; }}
            
            .img-fluid-post {{ max-width: 100%; border-radius: 16px; margin-top: 8px; max-height: 400px; border: 1px solid var(--border-color); }}
            .video-fluid-post {{ width: 100%; border-radius: 16px; margin-top: 8px; border: 1px solid var(--border-color); background: #000; }}
            .theme-toggle-btn {{ background: none; border: none; color: var(--text-color); font-size: 1.2rem; padding: 4px 8px; border-radius: 20px; transition: 0.2s; }}
            .theme-toggle-btn:hover {{ background: var(--border-color); }}
            
            .app-container {{ max-width: 600px; margin: 0 auto; }}
            .feed-tabs .btn {{ font-weight: 600; font-size: 0.9rem; padding: 6px 20px; }}
            
            .founding-banner {{
                background: linear-gradient(135deg, #fbbf24, #f59e0b);
                color: #0f1419;
                padding: 12px 16px;
                border-radius: 12px;
                margin-bottom: 12px;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: 10px;
                border: 1px solid #f59e0b;
                box-shadow: 0 2px 12px rgba(245, 158, 11, 0.2);
            }}
            .founding-banner .icon {{ font-size: 1.8rem; }}
            .founding-banner .text {{ flex: 1; }}
            .founding-banner .badge {{ background: #0f1419; color: #fbbf24; padding: 2px 12px; border-radius: 50px; font-size: 0.7rem; font-weight: 700; }}
            
            .hashtag-tag {{
                display: inline-block;
                background: var(--bg-body);
                padding: 1px 10px;
                border-radius: 50px;
                font-size: 0.75rem;
                font-weight: 600;
                color: var(--accent);
                border: 1px solid var(--border-color);
                margin-right: 4px;
                margin-top: 4px;
            }}
            .hashtag-tag:hover {{
                background: var(--accent);
                color: white;
                border-color: var(--accent);
            }}
            
            .trending-item {{
                display: flex;
                justify-content: space-between;
                padding: 8px 12px;
                border-bottom: 1px solid var(--border-color);
            }}
            .trending-item .rank {{ font-weight: 700; color: var(--text-muted); margin-right: 12px; }}
            .trending-item .tag {{ font-weight: 600; }}
            .trending-item .count {{ color: var(--text-muted); font-size: 0.8rem; }}
            
            .settings-card {{
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 16px;
                padding: 24px;
                margin-bottom: 12px;
            }}
            
            .chat-message {{
                max-width: 80%;
                padding: 8px 14px;
                border-radius: 18px;
                margin-bottom: 6px;
                word-break: break-word;
            }}
            .chat-message.sent {{
                background: var(--accent);
                color: white;
                align-self: flex-end;
                border-bottom-right-radius: 4px;
            }}
            .chat-message.received {{
                background: var(--bg-body);
                color: var(--text-color);
                align-self: flex-start;
                border-bottom-left-radius: 4px;
            }}
            .chat-message .time {{
                font-size: 0.6rem;
                opacity: 0.7;
                margin-left: 8px;
            }}
            .inbox-item {{
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 12px 16px;
                border-bottom: 1px solid var(--border-color);
                text-decoration: none;
                color: var(--text-color);
                transition: 0.2s;
            }}
            .inbox-item:hover {{
                background: var(--bg-body);
            }}
            .inbox-item .unread {{
                font-weight: 700;
            }}
            
            /* Landing Page Styles */
            .landing-hero {{
                padding: 40px 20px 20px 20px;
                text-align: center;
            }}
            .landing-hero .logo {{
                font-size: 3rem;
                font-weight: 800;
            }}
            .landing-hero .sub {{
                font-size: 1.2rem;
                color: var(--text-muted);
                margin-top: 8px;
            }}
            .feature-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
                padding: 20px;
            }}
            .feature-card {{
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 16px;
                padding: 16px;
                text-align: center;
                box-shadow: var(--shadow);
            }}
            .feature-card i {{ font-size: 2rem; color: var(--accent); }}
            .feature-card h6 {{ margin-top: 6px; font-weight: 600; }}
            @media (max-width: 500px) {{ .feature-grid {{ grid-template-columns: 1fr 1fr; }} }}
            
            .waitlist-box {{
                max-width: 400px;
                margin: 0 auto;
                padding: 20px;
                background: var(--bg-card);
                border-radius: 16px;
                border: 1px solid var(--border-color);
            }}
            .counter {{
                font-size: 1.5rem;
                font-weight: 700;
                color: var(--accent);
            }}
            .hidden-login-link {{
                font-size: 0.7rem;
                color: var(--text-muted);
                opacity: 0.5;
                margin-top: 12px;
            }}
            .hidden-login-link a {{
                color: var(--text-muted);
                text-decoration: none;
            }}
            .hidden-login-link a:hover {{
                text-decoration: underline;
            }}
        </style>
    </head>
    <body>
        <nav class="app-top-nav fixed-top">
            <div class="d-flex justify-content-between align-items-center w-100">
                <a class="brand" href="/"><i class="bi bi-vibes"></i> VibeHub</a>
                <div class="d-flex align-items-center">
                    <a href="/settings" class="text-decoration-none text-muted me-2" style="font-size:1.2rem;" title="Settings">
                        <i class="bi bi-gear"></i>
                    </a>
                    <button class="theme-toggle-btn me-2" onclick="toggleTheme()" id="themeToggle">
                        <i id="theme-icon" class="bi bi-moon-fill"></i>
                    </button>
                    <span class="text-muted" style="font-weight:500;font-size:0.9rem;">@{current_user.username if current_user.is_authenticated else ''}</span>
                </div>
            </div>
        </nav>
        <div class="toast-container" id="toastContainer"></div>
        <div class="app-container">
    """
    return html

def get_base_foot():
    unread = get_unread_count() if current_user.is_authenticated else 0
    unread_badge = f'<span class="badge bg-danger rounded-pill" style="font-size:0.5rem;position:absolute;top:-4px;right:-8px;">{unread}</span>' if unread > 0 else ''
    
    return f"""
        </div>
        <nav class="app-bottom-nav">
            <a href="/" class="bottom-nav-item active" id="nav-home"><i class="bi bi-house-fill"></i><span>Home</span></a>
            <a href="/leaderboard" class="bottom-nav-item" id="nav-leaderboard"><i class="bi bi-trophy"></i><span>Top</span></a>
            <a href="/premium" class="bottom-nav-item" id="nav-premium"><i class="bi bi-gem"></i><span>Premium</span></a>
            <a href="/inbox" class="bottom-nav-item" id="nav-inbox" style="position:relative;">
                <i class="bi bi-envelope"></i>
                {unread_badge}
                <span>Messages</span>
            </a>
            <a href="/profile" class="bottom-nav-item" id="nav-profile"><i class="bi bi-person"></i><span>Profile</span></a>
        </nav>
        <script>
            document.addEventListener('DOMContentLoaded', function() {{
                const path = window.location.pathname;
                const navMap = {{'/':'nav-home','/leaderboard':'nav-leaderboard','/premium':'nav-premium','/profile':'nav-profile','/inbox':'nav-inbox'}};
                const activeId = navMap[path] || 'nav-home';
                document.querySelectorAll('.bottom-nav-item').forEach(el => el.classList.remove('active'));
                const activeEl = document.getElementById(activeId);
                if (activeEl) activeEl.classList.add('active');
                const theme = localStorage.getItem('theme');
                const icon = document.getElementById('theme-icon');
                if (theme === 'dark') {{ document.documentElement.classList.add('dark-mode'); if(icon) icon.className = 'bi bi-sun-fill'; }} else {{ if(icon) icon.className = 'bi bi-moon-fill'; }}
                document.querySelectorAll('.flash-message').forEach(f => showToast(f.dataset.message, f.dataset.category));
            }});
            function toggleTheme() {{
                const html = document.documentElement; html.classList.toggle('dark-mode');
                const icon = document.getElementById('theme-icon');
                if (html.classList.contains('dark-mode')) {{ icon.className = 'bi bi-sun-fill'; localStorage.setItem('theme', 'dark'); }} else {{ icon.className = 'bi bi-moon-fill'; localStorage.setItem('theme', 'light'); }}
            }}
            function showToast(message, category = 'info') {{
                const container = document.getElementById('toastContainer'); if (!container) return;
                const toast = document.createElement('div'); toast.className = `toast-custom ${{category}}`; toast.textContent = message;
                container.appendChild(toast);
                setTimeout(() => toast.classList.add('show'), 10);
                setTimeout(() => {{ toast.classList.remove('show'); setTimeout(() => toast.remove(), 500); }}, 3500);
            }}
            // Auto-refresh inbox count
            setInterval(function() {{
                fetch('/unread_count')
                    .then(res => res.json())
                    .then(data => {{
                        const badge = document.querySelector('#nav-inbox .badge');
                        if (data.count > 0) {{
                            if (badge) {{
                                badge.textContent = data.count;
                            }} else {{
                                const newBadge = document.createElement('span');
                                newBadge.className = 'badge bg-danger rounded-pill';
                                newBadge.style.cssText = 'font-size:0.5rem;position:absolute;top:-4px;right:-8px;';
                                newBadge.textContent = data.count;
                                document.getElementById('nav-inbox').appendChild(newBadge);
                            }}
                        }} else {{
                            if (badge) badge.remove();
                        }}
                    }})
                    .catch(() => {{}});
            }}, 10000);
        </script>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return html

# --- HOMEPAGE (UPDATED: Removed Login/Register links from the public landing page) ---
@app.route('/', methods=['GET', 'POST'])
def home():
    # --- NEW: If user is NOT logged in, show the Advertising/Hype Landing Page ---
    if not current_user.is_authenticated:
        waitlist_count = Waitlist.query.count()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>VibeHub - Coming Soon</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
            <style>
                :root { --bg-body: #f8f9fa; --bg-card: #ffffff; --text-color: #0f1419; --text-muted: #536471; --border-color: #eff3f4; --accent: #6366f1; --gold: #f59e0b; }
                body { background: var(--bg-body); color: var(--text-color); font-family: 'Inter', sans-serif; min-height: 100vh; display: flex; flex-direction: column; }
                .container { max-width: 600px; margin: 0 auto; padding: 20px; flex: 1; display: flex; flex-direction: column; justify-content: center; }
                .hero { text-align: center; padding: 20px 0; }
                .hero .logo { font-size: 3.5rem; font-weight: 800; }
                .hero .logo i { color: var(--accent); }
                .hero h1 { font-size: 2.2rem; font-weight: 800; margin-top: 10px; }
                .hero p { color: var(--text-muted); font-size: 1.1rem; max-width: 400px; margin: 8px auto 0 auto; }
                .feature-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 24px 0; }
                .feature-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 16px; text-align: center; }
                .feature-card i { font-size: 1.8rem; color: var(--accent); }
                .feature-card h6 { margin-top: 4px; font-weight: 600; font-size: 0.9rem; }
                .waitlist-box { background: var(--bg-card); border-radius: 16px; border: 1px solid var(--border-color); padding: 24px; text-align: center; margin-top: 10px; }
                .waitlist-box .counter { font-size: 1.2rem; font-weight: 700; color: var(--accent); }
                .btn-primary { background: var(--accent); border: none; border-radius: 50px; padding: 10px 24px; font-weight: 600; }
                .btn-primary:hover { background: #4f46e5; }
                .form-control { border-radius: 50px; padding: 10px 20px; border: 1px solid var(--border-color); background: var(--bg-body); color: var(--text-color); }
                .footer { text-align: center; padding: 20px 0 10px 0; font-size: 0.8rem; color: var(--text-muted); }
                .hidden-login { font-size: 0.6rem; opacity: 0.3; margin-top: 12px; }
                .hidden-login a { color: var(--text-muted); text-decoration: none; }
                .hidden-login a:hover { text-decoration: underline; }
                @media (max-width: 500px) { .feature-grid { grid-template-columns: 1fr 1fr; } .hero .logo { font-size: 2.5rem; } .hero h1 { font-size: 1.6rem; } }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="hero">
                    <div class="logo"><i class="bi bi-vibes"></i> VibeHub</div>
                    <h1>The Future of Social.<br><span style="color:var(--accent);">Coming Soon.</span></h1>
                    <p>Join the waitlist for early access. First 100 members get <strong>30 days of Premium for free</strong>! ⭐</p>
                </div>

                <div class="feature-grid">
                    <div class="feature-card"><i class="bi bi-chat-dots"></i><h6>Direct Messages</h6></div>
                    <div class="feature-card"><i class="bi bi-camera-video"></i><h6>Video Uploads</h6></div>
                    <div class="feature-card"><i class="bi bi-trophy"></i><h6>Karma & Leaderboard</h6></div>
                    <div class="feature-card"><i class="bi bi-hash"></i><h6>Hashtags & Trending</h6></div>
                    <div class="feature-card"><i class="bi bi-gem"></i><h6>Premium Memberships</h6></div>
                    <div class="feature-card"><i class="bi bi-moon"></i><h6>Dark Mode</h6></div>
                </div>

                <div class="waitlist-box">
                    <h5>🚀 Get Early Access</h5>
                    <p class="text-muted small">Be the first to experience VibeHub.</p>
                    <form method="POST" action="/waitlist_signup">
                        <div class="d-flex gap-2 flex-wrap" style="justify-content:center;">
                            <input type="email" class="form-control" name="email" placeholder="Enter your email..." required style="flex:1;min-width:200px;">
                            <button class="btn btn-primary" type="submit">Join Waitlist</button>
                        </div>
                    </form>
                    <div class="mt-3">
                        <span class="counter">👥 <span id="counterNum">""" + str(waitlist_count) + """</span> people have joined the waitlist!</span>
                    </div>
                    <!-- LOGIN/REGISTER BUTTONS REMOVED -->
                    <div class="hidden-login">
                        <span>🔒 Waitlist is public. Owner login is at <a href="/login">/login</a></span>
                    </div>
                </div>
                <div class="footer">© 2026 VibeHub. Built by a 13-year-old visionary. 🔥</div>
            </div>
        </body>
        </html>
        """
        return html

    # --- If user IS logged in, show the full feed ---
    check_and_expire_trials(current_user)
    tab = request.args.get('tab', 'new')
    
    if request.method == 'POST':
        content = request.form.get('content')
        if content and content.strip():
            image_url = None
            video_url = None
            
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename:
                    if not is_premium_user(current_user):
                        flash('⭐ You need Premium to upload images!', 'error')
                        return redirect(url_for('home', tab=tab))
                    if not allowed_file(file.filename) or is_video_file(file.filename):
                        flash('❌ Invalid file type for image.', 'error')
                        return redirect(url_for('home', tab=tab))
                    try:
                        ext = file.filename.rsplit('.', 1)[1].lower()
                        filename = f"{uuid.uuid4().hex}.{ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        file.save(filepath)
                        image_url = f"/static/uploads/{filename}"
                        flash('📸 Image uploaded!', 'success')
                    except Exception as e:
                        flash(f'⚠️ Upload failed: {str(e)}', 'error')
                        return redirect(url_for('home', tab=tab))
            
            if 'video' in request.files:
                file = request.files['video']
                if file and file.filename:
                    if current_user.get_karma() < 100 and not current_user.is_owner:
                        flash('🎥 You need 100 Karma to upload videos!', 'error')
                        return redirect(url_for('home', tab=tab))
                    if not allowed_file(file.filename) or not is_video_file(file.filename):
                        flash('❌ Invalid video format.', 'error')
                        return redirect(url_for('home', tab=tab))
                    try:
                        ext = file.filename.rsplit('.', 1)[1].lower()
                        filename = f"{uuid.uuid4().hex}.{ext}"
                        filepath = os.path.join(VIDEO_FOLDER, filename)
                        file.save(filepath)
                        video_url = f"/static/videos/{filename}"
                        flash('🎥 Video uploaded!', 'success')
                    except Exception as e:
                        flash(f'⚠️ Video upload failed: {str(e)}', 'error')
                        return redirect(url_for('home', tab=tab))
            
            new_post = Post(content=content, author=current_user, image_url=image_url, video_url=video_url)
            db.session.add(new_post)
            db.session.commit()
            
            tag_names = extract_hashtags(content)
            for tag_name in tag_names:
                tag_name = tag_name.lower()
                hashtag = Hashtag.query.filter_by(name=tag_name).first()
                if not hashtag:
                    hashtag = Hashtag(name=tag_name)
                    db.session.add(hashtag)
                    db.session.commit()
                post_tag = PostHashtag(post_id=new_post.id, hashtag_id=hashtag.id)
                db.session.add(post_tag)
            
            db.session.commit()
            flash('Post published! 🚀', 'success')
        else:
            flash('Cannot post empty text!', 'error')
        return redirect(url_for('home', tab=tab))

    if tab == 'trending':
        all_hashtags = Hashtag.query.all()
        sorted_hashtags = sorted(all_hashtags, key=lambda h: h.get_score(), reverse=True)
        trending_tags = [h.name for h in sorted_hashtags[:5]]
        trending_posts = []
        if trending_tags:
            tag_objs = Hashtag.query.filter(Hashtag.name.in_(trending_tags)).all()
            post_ids = set()
            for tag in tag_objs:
                for ph in tag.posts:
                    post_ids.add(ph.post_id)
            trending_posts = Post.query.filter(Post.id.in_(post_ids)).order_by(Post.timestamp.desc()).all()
        all_posts = trending_posts
    elif tab == 'for_you':
        all_posts = Post.query.all()
        now = datetime.utcnow()
        for post in all_posts:
            hours_ago = (now - post.timestamp).total_seconds() / 3600
            post.hot_score = post.score / (hours_ago + 2)
        all_posts.sort(key=lambda p: p.hot_score, reverse=True)
    else:
        all_posts = Post.query.order_by(Post.timestamp.desc()).all()

    html = get_base_head("VibeHub")
    for message, category in get_flashed_messages(with_categories=True):
        html += f'<div class="flash-message" data-message="{message}" data-category="{category}"></div>'
    
    if current_user.is_founding_member():
        days_left = (current_user.premium_expiry - datetime.utcnow()).days
        html += f"""
        <div class="founding-banner">
            <span class="icon">⭐</span>
            <span class="text">
                You're a Founding Member! 🎉
                <br><span style="font-size:0.8rem;font-weight:400;">You have <strong>{days_left}</strong> days of free Premium left.</span>
            </span>
            <span class="badge">EXPIRES IN {days_left}D</span>
        </div>
        """
    elif current_user.is_premium:
        html += """
        <div class="founding-banner" style="background:var(--bg-card);border-color:var(--accent);color:var(--text-color);">
            <span class="icon">👑</span>
            <span class="text">You're a Premium Member! Thanks for supporting VibeHub. ❤️</span>
        </div>
        """

    karma = current_user.get_karma()
    can_post_video = karma >= 100 or current_user.is_owner
    user_has_premium = is_premium_user(current_user)
    
    html += f"""
    <div class="composer-wrapper">
        <form method="POST" enctype="multipart/form-data">
            <div class="d-flex gap-2 align-items-center">
                <img src="{get_avatar_url(current_user)}" width="40" height="40" class="rounded-circle" style="flex-shrink:0;">
                <input type="text" class="form-control composer-input" name="content" placeholder="What's happening? Use #tags!" required style="flex:1;">
                <button class="btn btn-primary rounded-pill px-4 fw-bold" type="submit" style="background: var(--accent); border: none; white-space: nowrap;">Post</button>
            </div>
            <div class="d-flex align-items-center gap-2 mt-1 ps-2 flex-wrap">
    """
    if user_has_premium:
        html += """
                <button class="btn btn-outline-secondary btn-sm rounded-pill" type="button" onclick="document.getElementById('imageUpload').click();">
                    <i class="bi bi-image"></i> Media
                </button>
                <input type="file" name="image" id="imageUpload" accept="image/*" style="display:none;">
        """
    else:
        html += '<small class="text-muted">⭐ <a href="/premium" class="text-decoration-none">Upgrade</a> for images.</small>'
    
    if can_post_video:
        html += """
                <button class="btn btn-outline-danger btn-sm rounded-pill" type="button" onclick="document.getElementById('videoUpload').click();">
                    <i class="bi bi-film"></i> Video
                </button>
                <input type="file" name="video" id="videoUpload" accept="video/*" style="display:none;">
                <small class="text-muted">Max 20MB</small>
        """
    else:
        needed = 100 - karma
        html += f'<small class="text-muted">🎥 <strong>{needed}</strong> more Karma for videos.</small>'
    
    html += """
            </div>
        </form>
    </div>
    """

    html += f"""
    <div class="d-flex gap-2 px-3 py-2 feed-tabs" style="background: var(--bg-card); border-bottom: 1px solid var(--border-color);">
        <a href="/?tab=new" class="btn btn-sm rounded-pill px-4 {'btn-primary' if tab == 'new' else 'btn-outline-secondary'}">
            <i class="bi bi-clock"></i> Following
        </a>
        <a href="/?tab=for_you" class="btn btn-sm rounded-pill px-4 {'btn-primary' if tab == 'for_you' else 'btn-outline-secondary'}">
            <i class="bi bi-stars"></i> For You
        </a>
        <a href="/?tab=trending" class="btn btn-sm rounded-pill px-4 {'btn-primary' if tab == 'trending' else 'btn-outline-secondary'}">
            <i class="bi bi-fire"></i> Trending
        </a>
    </div>
    """

    if tab == 'trending':
        html += """
        <div class="p-3" style="background: var(--bg-card); border-bottom: 1px solid var(--border-color);">
            <h6 class="mb-2">🔥 Trending Hashtags</h6>
        """
        all_tags = Hashtag.query.all()
        sorted_tags = sorted(all_tags, key=lambda h: h.get_score(), reverse=True)
        if sorted_tags:
            for idx, tag in enumerate(sorted_tags[:8], 1):
                html += f"""
                <div class="trending-item">
                    <span><span class="rank">#{idx}</span><span class="tag"><a href="/hashtag/{tag.name}" class="text-decoration-none" style="color:var(--text-color);">#{tag.name}</a></span></span>
                    <span class="count">{tag.get_score()} posts</span>
                </div>
                """
        else:
            html += '<p class="text-muted small">No hashtags yet. Start using #tags in your posts!</p>'
        html += "</div>"

    if all_posts:
        for post in all_posts:
            user_vote = get_user_vote(post.id)
            avatar_url = get_avatar_url(post.author)
            badge = render_badge(post.author)
            up_class = "vote-up-active" if user_vote == 1 else "text-muted"
            down_class = "vote-down-active" if user_vote == -1 else "text-muted"
            is_author = (post.author.id == current_user.id)

            hot_badge = ""
            if tab == 'for_you' and post.score > 5:
                hot_badge = '<span class="badge bg-danger ms-2" style="font-size:0.6rem;">🔥 Hot</span>'

            linked_content = linkify_hashtags(post.content)
            tag_badges = ""
            for ph in post.hashtags:
                tag_badges += f'<a href="/hashtag/{ph.hashtag.name}" class="hashtag-tag text-decoration-none">#{ph.hashtag.name}</a>'

            html += f"""
            <div class="feed-post" id="post-{post.id}">
                <div class="post-header">
                    <img src="{avatar_url}" width="40" height="40" class="rounded-circle">
                    <div>
                        <span class="post-username">{badge}{post.author.username}</span>
                        <span class="post-timestamp">· {post.timestamp.strftime('%b %d')}</span>
                        {hot_badge}
                        {f'<span class="badge bg-warning text-dark ms-1" style="font-size:0.6rem;">🎥 Video</span>' if post.video_url else ''}
                        {f'<span class="badge bg-amber ms-1" style="font-size:0.6rem;background:var(--gold);color:#000;">⭐</span>' if post.author.is_founding_member() else ''}
                    </div>
                    <div class="ms-auto">
                        {"<a href='#' class='text-muted me-1' onclick='editPost("+str(post.id)+")'><i class='bi bi-pencil'></i></a>" if is_author else ""}
                        {"<a href='#' class='text-danger' onclick='deletePost("+str(post.id)+")'><i class='bi bi-trash'></i></a>" if is_author else ""}
                    </div>
                </div>
                
                <div id="content-display-{post.id}">
                    <div class="post-content" id="post-text-{post.id}">{linked_content}</div>
                    {f'<img src="{post.image_url}" class="img-fluid-post">' if post.image_url else ''}
                    {f'<video src="{post.video_url}" class="video-fluid-post" controls playsinline></video>' if post.video_url else ''}
                    <div class="mt-1">{tag_badges}</div>
                </div>
                <div id="edit-form-{post.id}" style="display:none;" class="mt-2">
                    <div class="input-group input-group-sm">
                        <input type="text" class="form-control" id="edit-input-{post.id}" value="{post.content}">
                        <button class="btn btn-success btn-sm" onclick="saveEdit({post.id})">Save</button>
                        <button class="btn btn-secondary btn-sm" onclick="cancelEdit({post.id})">Cancel</button>
                    </div>
                </div>

                <div class="post-actions">
                    <a class="action-btn-group vote-btn" data-postid="{post.id}" data-direction="up">
                        <i class="bi bi-arrow-up-circle icon {up_class}"></i>
                    </a>
                    <span class="count fw-bold" id="score-{post.id}">{post.score}</span>
                    <a class="action-btn-group vote-btn" data-postid="{post.id}" data-direction="down">
                        <i class="bi bi-arrow-down-circle icon {down_class}"></i>
                    </a>
                    <a class="action-btn-group" data-bs-toggle="collapse" href="#comments-{post.id}" role="button" aria-expanded="false">
                        <i class="bi bi-chat icon"></i>
                        <span class="count">{len(post.comments)}</span>
                    </a>
                </div>

                <div class="collapse mt-2" id="comments-{post.id}">
                    <div class="comment-thread">
            """
            comments = Comment.query.filter_by(post_id=post.id).order_by(Comment.timestamp.asc()).all()
            if comments:
                for comment in comments:
                    c_avatar = get_avatar_url(comment.author, 28)
                    c_badge = render_badge(comment.author)
                    is_c_author = (comment.author.id == current_user.id)
                    html += f"""
                        <div class="comment-item" id="comment-{comment.id}">
                            <img src="{c_avatar}" class="comment-avatar">
                            <div class="comment-body">
                                <div>
                                    <span class="comment-username">{c_badge}{comment.author.username}</span>
                                    <span class="comment-time">{comment.timestamp.strftime('%I:%M %p')}</span>
                                    {"<a href='#' class='text-muted ms-1' onclick='editComment("+str(comment.id)+")'><i class='bi bi-pencil'></i></a>" if is_c_author else ""}
                                    {"<a href='#' class='text-danger ms-1' onclick='deleteComment("+str(comment.id)+")'><i class='bi bi-trash'></i></a>" if is_c_author else ""}
                                </div>
                                <div id="comment-content-display-{comment.id}">
                                    <p class="comment-text" id="comment-text-{comment.id}">{comment.content}</p>
                                </div>
                                <div id="comment-edit-form-{comment.id}" style="display:none;">
                                    <div class="input-group input-group-sm">
                                        <input type="text" class="form-control" id="comment-edit-input-{comment.id}" value="{comment.content}">
                                        <button class="btn btn-success btn-sm" onclick="saveCommentEdit({comment.id})">Save</button>
                                        <button class="btn btn-secondary btn-sm" onclick="cancelCommentEdit({comment.id})">Cancel</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    """
            else:
                html += '<p class="text-muted small mb-2">No comments yet.</p>'
            
            html += f"""
                        <form method="POST" action="/comment/{post.id}" class="mt-1 d-flex gap-1">
                            <input type="text" class="form-control form-control-sm" name="comment" placeholder="Write a reply..." required>
                            <button class="btn btn-primary btn-sm rounded-pill px-3" type="submit">Reply</button>
                        </form>
                    </div>
                </div>
            </div>
            """
    else:
        html += '<div class="p-4 text-center text-muted"><i class="bi bi-inbox" style="font-size:2rem;"></i><p class="mt-2">No posts yet. Use #hashtags to organize content!</p></div>'

    html += """
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('.vote-btn').forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                const postId = this.dataset.postid;
                const direction = this.dataset.direction;
                const url = '/vote/' + postId + '/' + direction;
                const scoreSpan = document.getElementById('score-' + postId);
                const upBtn = document.querySelector(`.vote-btn[data-postid="${postId}"][data-direction="up"] i`);
                const downBtn = document.querySelector(`.vote-btn[data-postid="${postId}"][data-direction="down"] i`);
                this.style.opacity = '0.5';
                fetch(url)
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            scoreSpan.textContent = data.score;
                            if (data.user_vote === 1) {
                                upBtn.className = 'bi bi-arrow-up-circle icon vote-up-active';
                                downBtn.className = 'bi bi-arrow-down-circle icon text-muted';
                            } else if (data.user_vote === -1) {
                                upBtn.className = 'bi bi-arrow-up-circle icon text-muted';
                                downBtn.className = 'bi bi-arrow-down-circle icon vote-down-active';
                            } else {
                                upBtn.className = 'bi bi-arrow-up-circle icon text-muted';
                                downBtn.className = 'bi bi-arrow-down-circle icon text-muted';
                            }
                        } else if (data.error) { showToast(data.error, 'error'); }
                        this.style.opacity = '1';
                    }).catch(() => { this.style.opacity = '1'; });
            });
        });
    });
    
    function editPost(id) { document.getElementById('content-display-'+id).style.display='none'; document.getElementById('edit-form-'+id).style.display='block'; }
    function cancelEdit(id) { document.getElementById('content-display-'+id).style.display='block'; document.getElementById('edit-form-'+id).style.display='none'; }
    function saveEdit(id) {
        const val = document.getElementById('edit-input-'+id).value;
        if(!val.trim()){ showToast('Cannot be empty!','error'); return; }
        fetch('/edit_post/'+id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:val})})
        .then(r=>r.json()).then(d=>{ if(d.success){ document.getElementById('post-text-'+id).textContent=val; cancelEdit(id); showToast('Updated!','success'); } else showToast(d.error,'error'); });
    }
    function deletePost(id) {
        if(!confirm('Delete this post?')) return;
        fetch('/delete_post/'+id, {method:'POST'}).then(r=>r.json()).then(d=>{ if(d.success){ document.getElementById('post-'+id).remove(); showToast('Deleted.','info'); } else showToast(d.error,'error'); });
    }
    function editComment(id) { document.getElementById('comment-content-display-'+id).style.display='none'; document.getElementById('comment-edit-form-'+id).style.display='block'; }
    function cancelCommentEdit(id) { document.getElementById('comment-content-display-'+id).style.display='block'; document.getElementById('comment-edit-form-'+id).style.display='none'; }
    function saveCommentEdit(id) {
        const val = document.getElementById('comment-edit-input-'+id).value;
        if(!val.trim()){ showToast('Cannot be empty!','error'); return; }
        fetch('/edit_comment/'+id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:val})})
        .then(r=>r.json()).then(d=>{ if(d.success){ document.getElementById('comment-text-'+id).textContent=val; cancelCommentEdit(id); showToast('Updated!','success'); } else showToast(d.error,'error'); });
    }
    function deleteComment(id) {
        if(!confirm('Delete comment?')) return;
        fetch('/delete_comment/'+id, {method:'POST'}).then(r=>r.json()).then(d=>{ if(d.success){ document.getElementById('comment-'+id).remove(); showToast('Deleted.','info'); } else showToast(d.error,'error'); });
    }
    </script>
    """
    html += get_base_foot()
    return html

# --- HASHTAG PAGE (Unchanged) ---
@app.route('/hashtag/<tag_name>')
@login_required
def hashtag_page(tag_name):
    tag = Hashtag.query.filter_by(name=tag_name.lower()).first_or_404()
    posts = [ph.post for ph in tag.posts]
    posts.sort(key=lambda p: p.timestamp, reverse=True)
    
    html = get_base_head(f"#{tag_name} - VibeHub")
    html += f"""
    <div style="padding: 12px 16px; background: var(--bg-card); border-bottom: 1px solid var(--border-color);">
        <div class="d-flex align-items-center gap-3">
            <a href="/" class="text-decoration-none text-muted"><i class="bi bi-arrow-left fs-5"></i></a>
            <div>
                <h5 class="mb-0">#{tag_name}</h5>
                <span class="text-muted" style="font-size:0.8rem;">{len(posts)} posts</span>
            </div>
        </div>
    </div>
    """
    
    if posts:
        for post in posts:
            user_vote = get_user_vote(post.id)
            avatar_url = get_avatar_url(post.author)
            badge = render_badge(post.author)
            up_class = "vote-up-active" if user_vote == 1 else "text-muted"
            down_class = "vote-down-active" if user_vote == -1 else "text-muted"
            is_author = (post.author.id == current_user.id)
            linked_content = linkify_hashtags(post.content)
            
            tag_badges = ""
            for ph in post.hashtags:
                tag_badges += f'<a href="/hashtag/{ph.hashtag.name}" class="hashtag-tag text-decoration-none">#{ph.hashtag.name}</a>'

            html += f"""
            <div class="feed-post" id="post-{post.id}">
                <div class="post-header">
                    <img src="{avatar_url}" width="40" height="40" class="rounded-circle">
                    <div>
                        <span class="post-username">{badge}{post.author.username}</span>
                        <span class="post-timestamp">· {post.timestamp.strftime('%b %d')}</span>
                        {f'<span class="badge bg-warning text-dark ms-1" style="font-size:0.6rem;">🎥 Video</span>' if post.video_url else ''}
                    </div>
                    <div class="ms-auto">
                        {"<a href='#' class='text-muted me-1' onclick='editPost("+str(post.id)+")'><i class='bi bi-pencil'></i></a>" if is_author else ""}
                        {"<a href='#' class='text-danger' onclick='deletePost("+str(post.id)+")'><i class='bi bi-trash'></i></a>" if is_author else ""}
                    </div>
                </div>
                <div class="post-content">{linked_content}</div>
                {f'<img src="{post.image_url}" class="img-fluid-post">' if post.image_url else ''}
                {f'<video src="{post.video_url}" class="video-fluid-post" controls playsinline></video>' if post.video_url else ''}
                <div class="mt-1">{tag_badges}</div>
                <div class="post-actions">
                    <a class="action-btn-group vote-btn" data-postid="{post.id}" data-direction="up">
                        <i class="bi bi-arrow-up-circle icon {up_class}"></i>
                    </a>
                    <span class="count fw-bold" id="score-{post.id}">{post.score}</span>
                    <a class="action-btn-group vote-btn" data-postid="{post.id}" data-direction="down">
                        <i class="bi bi-arrow-down-circle icon {down_class}"></i>
                    </a>
                    <a class="action-btn-group" data-bs-toggle="collapse" href="#comments-{post.id}" role="button">
                        <i class="bi bi-chat icon"></i>
                        <span class="count">{len(post.comments)}</span>
                    </a>
                </div>
            </div>
            """
    else:
        html += '<div class="p-4 text-center text-muted"><p>No posts with #{tag_name} yet. Be the first!</p></div>'
    
    html += get_base_foot()
    return html

# --- PREMIUM (Unchanged) ---
@app.route('/premium', methods=['GET', 'POST'])
@login_required
def premium():
    if request.method == 'POST':
        action = request.form.get('action')
        method = request.form.get('method')
        if action == 'buy':
            if current_user.is_premium: flash('Already Premium!', 'info'); return redirect(url_for('premium'))
            if method == 'card': flash('💳 Payment simulated via Card!', 'success')
            elif method == 'paypal': flash('🟦 Payment simulated via PayPal!', 'success')
            else: flash('Payment error.', 'error'); return redirect(url_for('premium'))
            current_user.is_premium = True; current_user.premium_since = datetime.utcnow(); db.session.commit()
            flash('💎 Premium unlocked!', 'success'); return redirect(url_for('premium'))
        elif action == 'karma_unlock':
            if current_user.is_premium: flash('Already Premium!', 'info'); return redirect(url_for('premium'))
            if current_user.has_used_karma_unlock: flash('Already used Karma unlock!', 'info'); return redirect(url_for('premium'))
            if current_user.get_karma() >= 2000:
                current_user.is_premium = True; current_user.premium_since = datetime.utcnow(); current_user.has_used_karma_unlock = True
                db.session.commit(); flash('🏆 Unlocked with Karma!', 'success')
            else: flash(f'Need {2000 - current_user.get_karma()} more Karma.', 'error')
            return redirect(url_for('premium'))

    html = get_base_head("VibeHub - Premium")
    for message, category in get_flashed_messages(with_categories=True): html += f'<div class="flash-message" data-message="{message}" data-category="{category}"></div>'
    
    if current_user.is_founding_member():
        days_left = (current_user.premium_expiry - datetime.utcnow()).days
        html += f"""
        <div class="p-4 text-center"><div style="font-size:4rem;">⭐</div><h1 class="gold-text">Founding Member</h1><p>You have <strong>{days_left} days</strong> of free Premium remaining.</p><a href="/" class="btn btn-primary rounded-pill px-5">Go to Feed</a></div>
        """
    elif current_user.is_premium:
        since = current_user.premium_since.strftime('%B %d, %Y') if current_user.premium_since else 'recently'
        html += f"""
        <div class="p-4 text-center"><div style="font-size:4rem;">👑</div><h1 class="gold-text">Premium Member</h1><p class="text-muted">Since {since}</p><a href="/" class="btn btn-primary rounded-pill px-5">Go to Feed</a></div>
        """
    else:
        karma = current_user.get_karma(); progress = min(100, (karma / 2000) * 100); can_unlock = karma >= 2000 and not current_user.has_used_karma_unlock
        html += f"""
        <div class="p-3">
            <div class="feed-post" style="border-radius:16px;margin-bottom:12px;border:1px solid var(--border-color);">
                <div class="text-center py-2"><div style="font-size:2.5rem;">💳</div><h3>Lazy Mode</h3><p class="lead">R20 / $2</p><button class="btn btn-gold rounded-pill px-5" data-bs-toggle="modal" data-bs-target="#checkoutModal">Buy Premium</button></div>
            </div>
            <div class="feed-post" style="border-radius:16px;border:1px solid var(--border-color);">
                <div class="text-center py-2"><div style="font-size:2.5rem;">🏆</div><h3>Grind Mode</h3><p class="mb-1">Karma: <strong>{karma}</strong> / 2000</p><div class="progress" style="height:8px;width:100%;background:var(--border-color);"><div class="progress-bar" style="width:{progress}%;background:var(--gold);"></div></div><div class="mt-3">"""
        if can_unlock: html += '<form method="POST"><input type="hidden" name="action" value="karma_unlock"><button class="btn btn-success rounded-pill px-5" type="submit">🚀 Unlock for Free</button></form>'
        elif current_user.has_used_karma_unlock: html += '<p class="text-muted">Already used.</p>'
        else: html += f'<p class="text-muted">Need {2000 - karma} more Karma.</p>'
        html += """</div></div></div>
        <div class="modal fade" id="checkoutModal" tabindex="-1"><div class="modal-dialog modal-dialog-centered"><div class="modal-content" style="background:var(--bg-card);color:var(--text-color);"><div class="modal-header border-bottom"><h5>💎 Checkout</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="d-flex gap-2 mb-3"><div class="p-3 border rounded flex-fill text-center active" id="cardMethod" onclick="selectMethod('card')" style="border-color:var(--accent);background:var(--bg-body);">💳 Card</div><div class="p-3 border rounded flex-fill text-center" id="paypalMethod" onclick="selectMethod('paypal')" style="border-color:var(--border-color);">🟦 PayPal</div></div><form method="POST" action="/premium"><input type="hidden" name="action" value="buy"><input type="hidden" name="method" id="selectedMethod" value="card"><div id="cardFields"><div class="mb-2"><input class="form-control" placeholder="Card Number" value="4242 4242 4242 4242"></div><div class="row g-2"><div class="col-6"><input class="form-control" placeholder="MM/YY" value="12/28"></div><div class="col-6"><input class="form-control" placeholder="CVC" value="123"></div></div></div><div id="paypalFields" style="display:none;" class="text-center py-3"><h5>🟦 PayPal</h5><p class="text-muted">Simulated.</p></div><button class="btn btn-gold w-100 mt-3 rounded-pill" type="submit">Pay Now</button></form></div></div></div></div>
        <script>function selectMethod(m){document.getElementById('selectedMethod').value=m;document.getElementById('cardMethod').className='p-3 border rounded flex-fill text-center'+(m==='card'?' active':'');document.getElementById('paypalMethod').className='p-3 border rounded flex-fill text-center'+(m==='paypal'?' active':'');document.getElementById('cardFields').style.display=m==='card'?'block':'none';document.getElementById('paypalFields').style.display=m==='paypal'?'block':'none';}</script>
        """
    html += get_base_foot()
    return html

# --- PROFILE (Unchanged) ---
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        if 'avatar_file' in request.files:
            file = request.files['avatar_file']
            if file and file.filename:
                if not allowed_file(file.filename) or is_video_file(file.filename):
                    flash('❌ Invalid file type.', 'error'); return redirect(url_for('profile'))
                try:
                    ext = file.filename.rsplit('.', 1)[1].lower()
                    filename = f"avatar_{current_user.id}_{uuid.uuid4().hex}.{ext}"
                    filepath = os.path.join(AVATAR_FOLDER, filename)
                    file.save(filepath)
                    old_pic = current_user.profile_pic
                    if old_pic and old_pic.startswith('/static/avatars/'):
                        old_path = old_pic.lstrip('/')
                        if os.path.exists(old_path): os.remove(old_path)
                    current_user.profile_pic = f"/static/avatars/{filename}"
                    db.session.commit()
                    flash('Profile picture updated!', 'success')
                    return redirect(url_for('profile'))
                except Exception as e:
                    flash(f'⚠️ Upload failed: {str(e)}', 'error')
                    return redirect(url_for('profile'))
            else: flash('No file selected.', 'error'); return redirect(url_for('profile'))
        else:
            new_pic = request.form.get('profile_pic')
            if new_pic and new_pic.strip():
                old_pic = current_user.profile_pic
                if old_pic and old_pic.startswith('/static/avatars/'):
                    old_path = old_pic.lstrip('/')
                    if os.path.exists(old_path): os.remove(old_path)
                current_user.profile_pic = new_pic.strip()
                db.session.commit()
                flash('Profile picture updated!', 'success')
            return redirect(url_for('profile'))

    user_posts = Post.query.filter_by(user_id=current_user.id).order_by(Post.timestamp.desc()).all()
    avatar_url = get_avatar_url(current_user, 90)
    badge = render_badge(current_user)
    html = get_base_head("VibeHub - Profile")
    for message, category in get_flashed_messages(with_categories=True):
        html += f'<div class="flash-message" data-message="{message}" data-category="{category}"></div>'
    
    karma_display = "∞" if current_user.is_owner else current_user.get_karma()
    status_text = current_user.get_premium_status()
    status_color = "var(--gold)" if current_user.is_founding_member() else "var(--accent)" if current_user.is_premium else "var(--text-muted)"
    premium_badge_html = f'<span class="premium-badge" style="background:{status_color};color:#000;padding:2px 12px;border-radius:50px;font-size:0.8rem;font-weight:700;">{status_text}</span>'
    html += f"""
    <div class="profile-header">
        <img src="{avatar_url}" class="profile-avatar">
        <h4 class="mt-2">{badge}@{current_user.username}</h4>
        <p class="text-muted">Karma: <strong>{karma_display}</strong> {premium_badge_html}</p>
        <hr>
        <h6>Change Profile Picture</h6>
        <form method="POST" enctype="multipart/form-data" class="mb-2">
            <div class="d-flex gap-2">
                <input type="file" class="form-control form-control-sm" name="avatar_file" accept="image/*" required>
                <button class="btn btn-primary btn-sm rounded-pill px-3" type="submit">Upload</button>
            </div>
            <small class="text-muted">Max 5MB (PNG, JPG, GIF, WEBP)</small>
        </form>
        <div class="d-flex align-items-center gap-2 my-2"><span class="text-muted">or</span></div>
        <form method="POST" class="d-flex gap-2">
            <input type="url" class="form-control form-control-sm" name="profile_pic" placeholder="Paste image URL...">
            <button class="btn btn-outline-secondary btn-sm rounded-pill px-3" type="submit">Set URL</button>
        </form>
        <small class="text-muted">Right-click any image → "Copy image address"</small>
        <hr>
        <p><small class="text-muted">Current: {current_user.profile_pic if current_user.profile_pic else 'None (auto-generated)'}</small></p>
    </div>
    <div class="p-2"><h5 class="px-2">📝 Your Posts</h5>
    """
    if user_posts:
        for post in user_posts:
            linked_content = linkify_hashtags(post.content)
            tag_badges = ""
            for ph in post.hashtags:
                tag_badges += f'<a href="/hashtag/{ph.hashtag.name}" class="hashtag-tag text-decoration-none">#{ph.hashtag.name}</a>'
            html += f"""
            <div class="feed-post"><div class="d-flex justify-content-between"><div><small class="text-muted">{post.timestamp.strftime('%b %d, %Y')}</small><p class="mb-0">{linked_content}</p><div class="mt-1">{tag_badges}</div><small class="text-muted">Score: {post.score}</small>{f'<br><img src="{post.image_url}" style="max-height:120px;border-radius:8px;">' if post.image_url else ''}{f'<br><video src="{post.video_url}" style="max-height:120px;border-radius:8px;" controls></video>' if post.video_url else ''}</div><a href="/" class="text-decoration-none text-muted"><i class="bi bi-arrow-right"></i></a></div></div>"""
    else:
        html += '<div class="p-3 text-muted text-center">No posts yet. <a href="/">Go post!</a></div>'
    html += "</div>" + get_base_foot()
    return html

# --- LEADERBOARD (HIDES OWNER) ---
@app.route('/leaderboard')
@login_required
def leaderboard():
    users = User.query.filter_by(is_owner=False).all()
    sorted_users = sorted(users, key=lambda u: u.get_karma(), reverse=True)
    html = get_base_head("VibeHub - Leaderboard")
    html += '<div class="p-3"><h4 class="mb-3">🏆 Leaderboard</h4>'
    if not sorted_users:
        html += '<p class="text-muted">No users to display yet.</p>'
    for idx, user in enumerate(sorted_users, 1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
        avatar_url = get_avatar_url(user, 36)
        badge = render_badge(user)
        post_count = len(user.posts)
        founder_star = ' ⭐' if user.is_founding_member() else ''
        karma_display = "∞" if user.is_owner else user.get_karma()
        html += f"""
        <div class="lb-item"><span style="min-width:40px;font-weight:700;">{medal}</span><img src="{avatar_url}" width="36" height="36" class="rounded-circle"><span class="flex-grow-1 fw-semibold">{badge}{user.username}{founder_star}</span><span class="badge bg-secondary rounded-pill me-1">{post_count}</span><span class="badge bg-primary rounded-pill">Karma {karma_display}</span></div>"""
    html += "</div>" + get_base_foot()
    return html

# --- SETTINGS (Unchanged) ---
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        password = request.form.get('owner_password')
        if password == 'Awande2013':
            if not current_user.is_owner:
                current_user.is_owner = True
                db.session.commit()
                flash('✅ Owner mode activated! You now have infinite karma and are hidden from the leaderboard. The activation form is now hidden.', 'success')
            else:
                flash('👑 You are already the owner!', 'info')
            return redirect(url_for('settings'))
        else:
            flash('❌ Incorrect owner password. Try again.', 'error')
            return redirect(url_for('settings'))

    html = get_base_head("VibeHub - Settings")
    
    for message, category in get_flashed_messages(with_categories=True):
        html += f'<div class="flash-message" data-message="{message}" data-category="{category}"></div>'

    html += """
    <div class="settings-card">
        <h5><i class="bi bi-gear"></i> Settings</h5>
        <hr>
    """
    
    if current_user.is_owner:
        html += """
        <div class="alert alert-success" role="alert">
            <i class="bi bi-check-circle-fill"></i> <strong>Owner Mode is ACTIVE.</strong><br>
            You have infinite Karma (∞) and are hidden from the leaderboard. This settings menu is now locked.
        </div>
        """
    else:
        html += """
        <p>Enter the owner password to activate <strong>Owner Mode</strong>.</p>
        <p class="text-muted small">Owner Mode gives you infinite Karma and hides you from the leaderboard.</p>
        <form method="POST">
            <div class="mb-3">
                <label for="owner_password" class="form-label">Owner Password</label>
                <input type="password" class="form-control" id="owner_password" name="owner_password" placeholder="Enter owner password..." required>
            </div>
            <button type="submit" class="btn btn-primary">Activate Owner Mode</button>
        </form>
        """
    
    html += """
        <hr>
        <a href="/" class="btn btn-outline-secondary btn-sm">← Back to Feed</a>
    </div>
    """
    
    html += get_base_foot()
    return html

# --- INBOX (Unchanged) ---
@app.route('/inbox')
@login_required
def inbox():
    sent_to = db.session.query(Message.receiver_id).filter_by(sender_id=current_user.id).distinct().all()
    received_from = db.session.query(Message.sender_id).filter_by(receiver_id=current_user.id).distinct().all()
    
    user_ids = set([id for (id,) in sent_to] + [id for (id,) in received_from])
    chat_users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
    
    chats = []
    for user in chat_users:
        last_msg = Message.query.filter(
            ((Message.sender_id == current_user.id) & (Message.receiver_id == user.id)) |
            ((Message.sender_id == user.id) & (Message.receiver_id == current_user.id))
        ).order_by(Message.timestamp.desc()).first()
        unread = Message.query.filter_by(receiver_id=current_user.id, sender_id=user.id, is_read=False).count()
        chats.append({
            'user': user,
            'last_msg': last_msg,
            'unread': unread
        })
    
    chats.sort(key=lambda x: x['last_msg'].timestamp if x['last_msg'] else datetime.min, reverse=True)
    
    html = get_base_head("VibeHub - Inbox")
    html += '<div class="p-2"><h5 class="mb-3">💬 Messages</h5>'
    
    if not chats:
        html += '<p class="text-muted text-center py-4">No conversations yet. <br>Start a chat by visiting someone\'s profile and clicking "Message".</p>'
    else:
        for chat in chats:
            user = chat['user']
            avatar = get_avatar_url(user, 36)
            badge = render_badge(user)
            last_text = chat['last_msg'].content[:50] + '...' if chat['last_msg'] and len(chat['last_msg'].content) > 50 else (chat['last_msg'].content if chat['last_msg'] else 'No messages')
            time_str = chat['last_msg'].timestamp.strftime('%I:%M %p') if chat['last_msg'] else ''
            unread_class = 'fw-bold' if chat['unread'] > 0 else ''
            
            html += f"""
            <a href="/chat/{user.id}" class="inbox-item">
                <img src="{avatar}" width="36" height="36" class="rounded-circle">
                <div class="flex-grow-1">
                    <span class="{unread_class}">{badge}{user.username}</span>
                    <div class="text-muted" style="font-size:0.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px;">
                        {last_text}
                    </div>
                </div>
                <div class="text-muted" style="font-size:0.7rem;text-align:right;">
                    {time_str}
                    {f'<span class="badge bg-danger rounded-pill">{chat["unread"]}</span>' if chat["unread"] > 0 else ''}
                </div>
            </a>
            """
    
    html += '</div>'
    html += get_base_foot()
    return html

# --- CHAT (Unchanged) ---
@app.route('/chat/<int:user_id>', methods=['GET', 'POST'])
@login_required
def chat(user_id):
    other_user = User.query.get_or_404(user_id)
    
    if other_user.id == current_user.id:
        flash('You cannot message yourself!', 'error')
        return redirect(url_for('inbox'))
    
    if request.method == 'POST':
        content = request.form.get('content')
        if content and content.strip():
            new_msg = Message(
                content=content.strip(),
                sender_id=current_user.id,
                receiver_id=other_user.id
            )
            db.session.add(new_msg)
            db.session.commit()
            flash('Message sent!', 'success')
        else:
            flash('Cannot send empty message.', 'error')
        return redirect(url_for('chat', user_id=other_user.id))
    
    unread_msgs = Message.query.filter_by(receiver_id=current_user.id, sender_id=other_user.id, is_read=False).all()
    for msg in unread_msgs:
        msg.is_read = True
    db.session.commit()
    
    messages = Message.query.filter(
        ((Message.sender_id == current_user.id) & (Message.receiver_id == other_user.id)) |
        ((Message.sender_id == other_user.id) & (Message.receiver_id == current_user.id))
    ).order_by(Message.timestamp.asc()).all()
    
    html = get_base_head(f"Chat with @{other_user.username}")
    
    avatar = get_avatar_url(other_user, 36)
    html += f"""
    <div style="padding: 12px 16px; background: var(--bg-card); border-bottom: 1px solid var(--border-color);">
        <div class="d-flex align-items-center gap-3">
            <a href="/inbox" class="text-decoration-none text-muted"><i class="bi bi-arrow-left fs-5"></i></a>
            <img src="{avatar}" width="36" height="36" class="rounded-circle">
            <div>
                <strong>{other_user.username}</strong>
                <div style="font-size:0.7rem;color:var(--text-muted);">Last active: {other_user.premium_since.strftime('%b %d') if other_user.premium_since else 'Recently'}</div>
            </div>
        </div>
    </div>
    """
    
    html += '<div style="padding: 12px 16px; display: flex; flex-direction: column; gap: 4px; min-height: 300px;">'
    if messages:
        for msg in messages:
            is_sent = msg.sender_id == current_user.id
            time_str = msg.timestamp.strftime('%I:%M %p')
            html += f"""
            <div class="chat-message {'sent' if is_sent else 'received'}" style="align-self: {'flex-end' if is_sent else 'flex-start'}; max-width: 80%;">
                {msg.content}
                <span class="time">{time_str}</span>
            </div>
            """
    else:
        html += '<p class="text-muted text-center py-4">No messages yet. Say hello!</p>'
    html += '</div>'
    
    html += f"""
    <div style="padding: 12px 16px; background: var(--bg-card); border-top: 1px solid var(--border-color); position: sticky; bottom: 0;">
        <form method="POST" class="d-flex gap-2">
            <input type="text" class="form-control composer-input" name="content" placeholder="Type a message..." required style="flex:1;">
            <button class="btn btn-primary rounded-pill px-4" type="submit"><i class="bi bi-send"></i> Send</button>
        </form>
    </div>
    """
    
    html += get_base_foot()
    return html

# --- UNREAD COUNT API ---
@app.route('/unread_count')
@login_required
def unread_count():
    count = Message.query.filter_by(receiver_id=current_user.id, is_read=False).count()
    return jsonify({"count": count})

# --- WAITLIST SIGNUP ---
@app.route('/waitlist_signup', methods=['POST'])
def waitlist_signup():
    email = request.form.get('email')
    if not email:
        flash('Please enter an email address.', 'error')
        return redirect(url_for('home'))
    
    if Waitlist.query.filter_by(email=email).first():
        flash('You are already on the waitlist!', 'info')
        return redirect(url_for('home'))
    
    new_entry = Waitlist(email=email)
    db.session.add(new_entry)
    db.session.commit()
    flash('🎉 You are on the waitlist! We will notify you at launch.', 'success')
    return redirect(url_for('home'))

# --- ROUTES: Edit/Delete/Vote/Comment (Unchanged) ---
@app.route('/edit_post/<int:post_id>', methods=['POST'])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author != current_user:
        return jsonify({"error": "Not your post!"}), 403
    data = request.get_json()
    if not data or not data.get('content').strip():
        return jsonify({"error": "Cannot be empty!"}), 400
    post.content = data['content'].strip()
    db.session.commit()
    PostHashtag.query.filter_by(post_id=post.id).delete()
    tag_names = extract_hashtags(post.content)
    for tag_name in tag_names:
        tag_name = tag_name.lower()
        hashtag = Hashtag.query.filter_by(name=tag_name).first()
        if not hashtag:
            hashtag = Hashtag(name=tag_name)
            db.session.add(hashtag)
            db.session.commit()
        post_tag = PostHashtag(post_id=post.id, hashtag_id=hashtag.id)
        db.session.add(post_tag)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/delete_post/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author != current_user:
        return jsonify({"error": "Not your post!"}), 403
    
    if post.image_url:
        try:
            filepath = post.image_url.lstrip('/')
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
    
    if post.video_url:
        try:
            filepath = post.video_url.lstrip('/')
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
    
    db.session.delete(post)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/edit_comment/<int:comment_id>', methods=['POST'])
@login_required
def edit_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if comment.author != current_user:
        return jsonify({"error": "Not your comment!"}), 403
    data = request.get_json()
    if not data or not data.get('content').strip():
        return jsonify({"error": "Cannot be empty!"}), 400
    comment.content = data['content'].strip()
    db.session.commit()
    return jsonify({"success": True})

@app.route('/delete_comment/<int:comment_id>', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if comment.author != current_user:
        return jsonify({"error": "Not your comment!"}), 403
    db.session.delete(comment)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/vote/<int:post_id>/<direction>')
@login_required
def vote(post_id, direction):
    post = Post.query.get_or_404(post_id)
    if post.author == current_user:
        return jsonify({"error": "Cannot vote on your own post!"}), 400
    existing_vote = Vote.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    if direction == 'up':
        if existing_vote:
            if existing_vote.value == 1:
                db.session.delete(existing_vote)
                post.score -= 1
                user_vote = 0
            else:
                existing_vote.value = 1
                post.score += 2
                user_vote = 1
        else:
            db.session.add(Vote(user_id=current_user.id, post_id=post_id, value=1))
            post.score += 1
            user_vote = 1
    else:
        if existing_vote:
            if existing_vote.value == -1:
                db.session.delete(existing_vote)
                post.score += 1
                user_vote = 0
            else:
                existing_vote.value = -1
                post.score -= 2
                user_vote = -1
        else:
            db.session.add(Vote(user_id=current_user.id, post_id=post_id, value=-1))
            post.score -= 1
            user_vote = -1
    db.session.commit()
    return jsonify({"success": True, "score": post.score, "user_vote": user_vote})

@app.route('/comment/<int:post_id>', methods=['POST'])
@login_required
def add_comment(post_id):
    content = request.form.get('comment')
    if content and content.strip():
        db.session.add(Comment(content=content, author=current_user, post_id=post_id))
        db.session.commit()
        flash('Comment added!', 'success')
    return redirect(url_for('home'))

# --- AUTH ROUTES (Unchanged) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Username taken!', 'error')
            return redirect(url_for('register'))
        
        user_count = User.query.count()
        is_founding = user_count < 100
        order_number = user_count + 1
        
        hashed = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed)
        
        if is_founding:
            new_user.signup_order = order_number
            new_user.is_premium = True
            new_user.premium_expiry = datetime.utcnow() + timedelta(days=30)
            new_user.premium_since = datetime.utcnow()
            flash(f'⭐ You\'re a Founding Member! 30-day free Premium. (User #{order_number})', 'success')
        else:
            flash('Account created! Please log in.', 'success')
        
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    
    user_count = User.query.count()
    spots_left = max(0, 100 - user_count)
    founding_msg = f"⭐ <strong>{spots_left}</strong> Founding Member spots left!" if spots_left > 0 else "🚀 Founding Member spots are full!"
    
    return f"""
    <!DOCTYPE html>
    <html><head><title>VibeHub - Register</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>body{{background:#f4f6fa;font-family:'Inter',sans-serif;padding-top:0 !important;}}.card{{border-radius:20px;border:none;box-shadow:0 10px 40px rgba(0,0,0,0.06);}}</style>
    </head>
    <body class="d-flex align-items-center" style="min-height:100vh;">
        <div class="container" style="max-width:400px;">
            <div class="card p-4">
                <h3 class="text-center mb-2">📝 Join VibeHub</h3>
                <p class="text-center text-muted small" style="font-size:0.85rem;">{founding_msg}</p>
                <form method="POST">
                    <div class="mb-3"><input type="text" class="form-control" name="username" placeholder="Username" required></div>
                    <div class="mb-3"><input type="password" class="form-control" name="password" placeholder="Password" required></div>
                    <button class="btn btn-primary w-100 rounded-pill" type="submit">Sign Up</button>
                </form>
                <p class="text-center mt-3"><a href="/login">Already have an account? Login</a></p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Welcome back!', 'success')
            return redirect(url_for('home'))
        flash('Invalid credentials.', 'error')
        return redirect(url_for('login'))
    return """
    <!DOCTYPE html>
    <html><head><title>VibeHub - Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>body{background:#f4f6fa;font-family:'Inter',sans-serif;padding-top:0 !important;}.card{border-radius:20px;border:none;box-shadow:0 10px 40px rgba(0,0,0,0.06);}</style>
    </head>
    <body class="d-flex align-items-center" style="min-height:100vh;">
        <div class="container" style="max-width:400px;">
            <div class="card p-4">
                <h3 class="text-center">🔐 Login</h3>
                <form method="POST">
                    <div class="mb-3"><input type="text" class="form-control" name="username" placeholder="Username" required></div>
                    <div class="mb-3"><input type="password" class="form-control" name="password" placeholder="Password" required></div>
                    <button class="btn btn-primary w-100 rounded-pill" type="submit">Login</button>
                </form>
                <p class="text-center mt-3"><a href="/register">Don't have an account? Sign up</a></p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out.', 'info')
    return """
    <div class="container text-center mt-5"><h3>Logged out!</h3><a href="/login" class="btn btn-primary rounded-pill px-4">Login again</a></div>
    """

# --- This line MUST be OUTSIDE the if block for Render/Gunicorn to work ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)