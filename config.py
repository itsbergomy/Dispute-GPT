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

    # Backward-compatible endpoint aliases so existing templates
    # using url_for('index') etc. still work without blueprint prefix
    _aliases = {
        # disputes blueprint
        'index': 'disputes.index',
        'upload_pdf': 'disputes.upload_pdf',
        'select_account': 'disputes.select_account',
        'confirm_account': 'disputes.confirm_account',
        'save_confirmed_account': 'disputes.save_confirmed_account',
        'select_entity': 'disputes.select_entity',
        'handle_entity': 'disputes.handle_entity',
        'define_details': 'disputes.define_details',
        'choose_template': 'disputes.choose_template',
        'prompt_packs': 'disputes.prompt_packs',
        'generate_letter_screen': 'disputes.generate_letter_screen',
        'generate_process': 'disputes.generate_process',
        'final_review': 'disputes.final_review',
        'manual_mode': 'disputes.manual_mode',
        'mail_letter': 'disputes.mail_letter',
        'convert_pdf': 'disputes.convert_pdf',
        'confirm_next_round': 'disputes.confirm_next_round',
        'dispute_folder': 'disputes.dispute_folder',
        'add_log': 'disputes.add_log',
        'add_letter': 'disputes.add_letter',
        'upload_doc': 'disputes.upload_doc',
        'report_analyzer': 'disputes.report_analyzer',
        'funding_sequencer': 'disputes.funding_sequencer',
        # auth blueprint
        'login': 'auth.login',
        'logout': 'auth.logout',
        'signup': 'auth.signup',
        'join_pro': 'auth.join_pro',
        'join_business': 'auth.join_business',
        'create_payment_intent': 'auth.create_payment_intent',
        'update_plan': 'auth.update_plan',
        # business blueprint
        'business_dashboard': 'business.business_dashboard',
        'create_client': 'business.create_client',
        'view_client': 'business.view_client',
        'edit_client': 'business.edit_client',
        'client_file': 'business.client_file',
        'upload_correspondence': 'business.upload_correspondence',
        'view_correspondence_file': 'business.view_correspondence_file',
        'run_analysis_for_client': 'business.run_analysis_for_client',
        'messages_thread': 'business.messages_thread',
        'update_recommendations': 'business.update_recommendations',
        'send_analysis_email_route': 'business.send_analysis_email_route',
        'mail_analysis_to_client': 'business.mail_analysis_to_client',
        'run_disputegpt_flow': 'business.run_disputegpt_flow',
        'finalize_disputegpt_letter': 'business.finalize_disputegpt_letter',
        'extract_for_disputegpt': 'business.extract_for_disputegpt',
        'toggle_workflow': 'business.toggle_workflow',
        'list_custom_letters': 'business.list_custom_letters',
        'new_custom_letter': 'business.new_custom_letter',
        'edit_custom_letter': 'business.edit_custom_letter',
        'delete_custom_letter': 'business.delete_custom_letter',
    }

    from flask import url_for as _original_url_for
    @app.url_build_error_handlers.append
    def _handle_url_build_error(error, endpoint, values):
        """Redirect old endpoint names to blueprint-prefixed versions."""
        if endpoint in _aliases:
            return _original_url_for(_aliases[endpoint], **values)
        raise error

    return app
