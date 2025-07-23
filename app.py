from flask import Flask, redirect, request, flash, url_for, render_template, session, send_file, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, current_user, login_required
from authlib.integrations.flask_client import OAuth
import os
from datetime import date, timedelta
from google.oauth2 import id_token
from google.auth.transport import requests as grequests
from werkzeug.utils import secure_filename


app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev')
os.makedirs(app.instance_path, exist_ok=True)


from models import db, User, Medicine
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

oauth = OAuth(app)
GOOGLE_CLIENT_ID = "184957211263-1thhnhgv80b47e2htjjalmnmu64c28js.apps.googleusercontent.com"
GOOGLE_DISCOVERY_URL = (
    "https://accounts.google.com/.well-known/openid-configuration"
)

google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret='dummy_secret',
    server_metadata_url=GOOGLE_DISCOVERY_URL,
    client_kwargs={
        'scope': 'openid email profile',
    }
)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login')
def login():
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def auth_callback():
    token = google.authorize_access_token()
    userinfo = google.parse_id_token(token)
    if not userinfo:
        flash('Failed to get user info from Google.', 'danger')
        return redirect(url_for('home'))
    email = userinfo['email']
    name = userinfo.get('name', email)
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name)
        db.session.add(user)
        db.session.commit()
    login_user(user)
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('home'))

@app.route('/token-signin', methods=['POST'])
def token_signin():
    token = request.form.get('id_token')
    if not token:
        flash('Missing ID token.', 'danger')
        return redirect(url_for('home'))
    try:
        idinfo = id_token.verify_oauth2_token(
            token,
            grequests.Request(),
            GOOGLE_CLIENT_ID
        )
        email = idinfo['email']
        name = idinfo.get('name', email)
    except Exception as e:
        flash('Invalid ID token.', 'danger')
        return redirect(url_for('home'))
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name)
        db.session.add(user)
        db.session.commit()
    login_user(user)
    return redirect(url_for('dashboard'))

@app.route('/')
def home():
    return render_template('home.html')

