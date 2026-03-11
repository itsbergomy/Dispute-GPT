"""
Application configuration and factory.
Centralizes all config and provides create_app() for use by task workers and blueprints.
"""

import os
import tempfile
from flask import Flask
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_mail import Mail
from dotenv import load_dotenv

load_dotenv()


class Config:
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
    ALLOWED_EXTENSIONS = {'pdf'}
    SQLALCHEMY_DATABASE_URI = 'sqlite:///dispute.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv('SECRET_KEY', 'smartflow')

    # Mail
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')


mail = Mail()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'


def create_app():
    """Application factory."""
    app = Flask(__name__)
    app.config.from_object(Config)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.environ['UPLOAD_FOLDER'] = app.config['UPLOAD_FOLDER']

    from models import db
    db.init_app(app)
    Migrate(app, db)

    mail.init_app(app)
    login_manager.init_app(app)

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register blueprints
    from blueprints.auth import auth_bp
    from blueprints.disputes import disputes_bp
    from blueprints.business import business_bp
    from blueprints.pipeline_api import pipeline_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(disputes_bp)
    app.register_blueprint(business_bp)
    app.register_blueprint(pipeline_bp, url_prefix='/api')

    # Template filter
    import json
    @app.template_filter('loads')
    def loads_filter(s):
        return json.loads(s)

    return app
