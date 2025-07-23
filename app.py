from flask import Flask, redirect, request, flash, url_for, render_template, session, send_file, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, current_user, login_required
from authlib.integrations.flask_client import OAuth
import os
from datetime import date, timedelta
from google.oauth2 import id_token
from google.auth.transport import requests as grequests
from werkzeug.utils import secure_filename
import uuid
import csv
import json

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
UPLOAD_FOLDER = 'static/uploads/'

app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev')
os.makedirs(app.instance_path, exist_ok=True)
db_path = os.path.join(app.instance_path, 'mediacheck.sqlite')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    soon = today + timedelta(days=30)
    expiring_soon_days = 7
    medicines = Medicine.query.filter_by(user_id=current_user.id).order_by(Medicine.expiry_date).all()
    expired = [m for m in medicines if m.expiry_date < today]
    expiring = [m for m in medicines if today <= m.expiry_date <= soon]
    active = [m for m in medicines if m.expiry_date > soon]
    return render_template('dashboard.html', expired=expired, expiring=expiring, active=active, expiring_soon_days=expiring_soon_days, today=today)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_medicine():
    if request.method == 'POST':
        name = request.form.get('name')
        expiry_date = request.form.get('expiry_date')
        notes = request.form.get('notes')
        image = request.files.get('image')
        image_filename = None
        if image and image.filename and allowed_file(image.filename):
            ext = image.filename.rsplit('.', 1)[1].lower()
            image_filename = f"{uuid.uuid4().hex}.{ext}"
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
        if not name or not expiry_date:
            flash('Name and expiry date are required.', 'danger')
            return render_template('add.html')
        try:
            from datetime import datetime
            expiry = datetime.strptime(expiry_date, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date format.', 'danger')
            return render_template('add.html')
        med = Medicine(
            user_id=current_user.id,
            name=name,
            expiry_date=expiry,
            notes=notes,
            image_filename=image_filename
        )
        db.session.add(med)
        db.session.commit()
        flash('Medicine added!', 'success')
        return redirect('/dashboard')
    return render_template('add.html')

@app.route('/edit/<int:med_id>', methods=['GET', 'POST'])
@login_required

@app.route('/export')
@login_required
def export_csv():
    medicines = Medicine.query.filter_by(user_id=current_user.id).order_by(Medicine.expiry_date).all()
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['Name', 'Expiry Date', 'Notes', 'Created At', 'Image Filename'])
    for m in medicines:
        writer.writerow([
            m.name,
            m.expiry_date.strftime('%Y-%m-%d'),
            m.notes or '',
            m.created_at.strftime('%Y-%m-%d %H:%M'),
            m.image_filename or ''
        ])
    output = si.getvalue()
    return Response(
        output,
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment;filename=medicines.csv"}
    )

@app.route('/import', methods=['POST'])
@login_required
def import_csv():
    file = request.files.get('csvfile')
    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect('/dashboard')
    try:
        stream = StringIO(file.stream.read().decode('utf-8'))
        reader = csv.DictReader(stream)
        count = 0
        for row in reader:
            name = row.get('Name')
            expiry_date = row.get('Expiry Date')
            notes = row.get('Notes')
            if not name or not expiry_date:
                continue
            from datetime import datetime
            try:
                expiry = datetime.strptime(expiry_date, '%Y-%m-%d').date()
            except Exception:
                continue
            med = Medicine(
                user_id=current_user.id,
                name=name,
                expiry_date=expiry,
                notes=notes
            )
            db.session.add(med)
            count += 1
        db.session.commit()
        flash(f'Imported {count} medicines from CSV.', 'success')
    except Exception as e:
        flash('Failed to import CSV. Please check the file format.', 'danger')
    return redirect('/dashboard')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True) 
    
    
    
    
    

    