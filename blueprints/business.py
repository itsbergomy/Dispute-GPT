"""
Business dashboard blueprint — client management, analysis, DisputeGPT, custom letters.
Extracted from dispute_ui.py.
"""

import os
import json
from flask import (
    Blueprint, request, render_template, flash, redirect,
    url_for, session, send_from_directory, abort, current_app
)
from flask_login import login_required, current_user
from flask_mail import Message as MailMessage
from werkzeug.utils import secure_filename

from models import (
    db, Client, ClientReportAnalysis, ClientDisputeLetter,
    WorkflowSetting, CustomLetter, MessageThread, Message,
    Correspondence, DisputePipeline
)
from services.pdf_parser import extract_negative_items_from_pdf
from services.report_analyzer import run_report_analysis
from services.letter_generator import PACKS, generate_letter, letter_to_pdf, image_to_pdf, merge_dispute_package
from config import mail

business_bp = Blueprint('business', __name__)


@business_bp.before_request
@login_required
def require_business_plan():
    """Gate all business routes to business-plan users only."""
    if current_user.plan != 'business':
        flash('Business plan required.', 'error')
        return redirect(url_for('disputes.index'))


@business_bp.route('/business-dashboard')
@login_required
def business_dashboard():
    client_id = request.args.get('client_id', type=int)
    clients = Client.query.filter_by(business_user_id=current_user.id).all()

    selected_client = None
    workflow_enabled = False

    if client_id:
        selected_client = Client.query.get(client_id)
        if selected_client and selected_client.business_user_id == current_user.id:
            setting = WorkflowSetting.query.filter_by(
                client_id=client_id,
                key='cfpb_collection'
            ).first()
            if setting:
                workflow_enabled = setting.enabled

    total_clients = len(clients)
    total_workflows_enabled = WorkflowSetting.query.filter_by(
        business_user_id=current_user.id,
        enabled=True
    ).count()

    # Get pipeline statuses for each client
    pipelines = DisputePipeline.query.filter_by(user_id=current_user.id).order_by(
        DisputePipeline.created_at.desc()
    ).all()

    active_pipelines = sum(1 for p in pipelines if p.state not in ('completed', 'failed'))
    letters_sent = ClientDisputeLetter.query.join(Client).filter(
        Client.business_user_id == current_user.id
    ).count()

    stats = {
        'total_clients': total_clients,
        'active_pipelines': active_pipelines,
        'letters_sent': letters_sent,
        'workflows_enabled': total_workflows_enabled,
    }

    correspondence = []
    active_tab = request.args.get('tab', 'clients')

    return render_template("business_dashboard.html",
                           clients=clients,
                           selected_client=selected_client,
                           workflow_enabled=workflow_enabled,
                           stats=stats,
                           correspondence=correspondence,
                           active_tab=active_tab,
                           pipelines=pipelines)


@business_bp.route('/clients/create', methods=['POST'])
@login_required
def create_client():
    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')
    email = request.form.get('email')

    if not all([first_name, last_name, email]):
        flash("First name, last name, and email are required.", "error")
        return redirect(url_for("business.business_dashboard"))

    client = Client(
        first_name=first_name,
        last_name=last_name,
        email=email,
        business_user_id=current_user.id,
        address_line1=request.form.get('address_line1', '').strip() or None,
        address_line2=request.form.get('address_line2', '').strip() or None,
        city=request.form.get('city', '').strip() or None,
        state=request.form.get('state', '').strip() or None,
        zip_code=request.form.get('zip_code', '').strip() or None,
        notes=request.form.get('notes', '').strip() or None,
    )
    db.session.add(client)
    db.session.commit()

    # Save uploaded files
    upload_dir = current_app.config['UPLOAD_FOLDER']
    client_dir = os.path.join(upload_dir, str(client.id))
    os.makedirs(client_dir, exist_ok=True)

    file_fields = {
        'pdf_file': 'pdf_filename',
        'id_file': 'id_filename',
        'ssn_file': 'ssn_filename',
        'utility_file': 'utility_filename',
    }
    for form_key, model_attr in file_fields.items():
        f = request.files.get(form_key)
        if f and f.filename:
            safe_name = secure_filename(f.filename)
            save_path = os.path.join(client_dir, safe_name)
            f.save(save_path)
            setattr(client, model_attr, safe_name)

    db.session.commit()

    thread = MessageThread(client_id=client.id)
    db.session.add(thread)
    db.session.commit()

    flash(f"Client {first_name} {last_name} created.", "success")
    return redirect(url_for("business.business_dashboard"))


@business_bp.route('/clients/<int:client_id>')
@login_required
def view_client(client_id):
    client = Client.query.get_or_404(client_id)
    client_parsed_accounts = session.get("client_parsed_accounts", [])

    settings = WorkflowSetting.query.filter_by(client_id=client.id).all()
    workflow_settings = {s.key: s.enabled for s in settings}

    # Get active pipeline for this client
    active_pipeline = DisputePipeline.query.filter(
        DisputePipeline.client_id == client_id,
        DisputePipeline.state.notin_(['completed', 'failed']),
    ).first()

    pipeline_status = None
    if active_pipeline:
        from services.pipeline_engine import get_pipeline_status
        pipeline_status = get_pipeline_status(active_pipeline.id)

    return render_template("view_client.html",
                           client=client,
                           client_parsed_accounts=client_parsed_accounts,
                           workflow_settings=workflow_settings,
                           active_pipeline=active_pipeline,
                           pipeline_status=pipeline_status)


@business_bp.route('/clients/<int:client_id>/upload-correspondence', methods=['POST'])
@login_required
def upload_correspondence(client_id):
    client = Client.query.get_or_404(client_id)
    file = request.files.get('correspondence_file')

    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        new_file = Correspondence(
            client_id=client.id,
            user_id=current_user.id,
            filename=filename,
            file_url=filepath,
        )
        db.session.add(new_file)
        db.session.commit()

    return redirect(url_for('business.view_client', client_id=client_id))


@business_bp.route('/view-correspondence/<filename>')
@login_required
def view_correspondence_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@business_bp.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_client(client_id):
    client = Client.query.get_or_404(client_id)

    if client.business_user_id != current_user.id:
        flash("Unauthorized", "error")
        return redirect(url_for('business.business_dashboard'))

    if request.method == 'POST':
        client.first_name = request.form['first_name']
        client.last_name = request.form['last_name']
        client.email = request.form['email']
        client.address_line1 = request.form.get('address_line1')
        client.address_line2 = request.form.get('address_line2')
        client.city = request.form.get('city')
        client.state = request.form.get('state')
        client.zip_code = request.form.get('zip_code')
        client.round_status = request.form.get('round_status')
        client.notes = request.form.get('notes')

        uploads = [
            ('id_file', 'id_filename'),
            ('ssn_file', 'ssn_filename'),
            ('utility_file', 'utility_filename'),
            ('pdf_file', 'pdf_filename'),
        ]
        for field_name, model_attr in uploads:
            f = request.files.get(field_name)
            if f and f.filename:
                filename = f"{client.id}_{field_name}_{secure_filename(f.filename)}"
                full_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                f.save(full_path)
                setattr(client, model_attr, filename)

        db.session.commit()
        flash("Client updated", "success")
        return redirect(url_for('business.view_client', client_id=client.id))

    return render_template('edit_client.html', client=client)


@business_bp.route('/client-files/<int:client_id>/<filetype>')
@login_required
def client_file(client_id, filetype):
    c = Client.query.get_or_404(client_id)
    if c.business_user_id != current_user.id:
        abort(403)

    mapping = {
        'id': c.id_filename,
        'ssn': c.ssn_filename,
        'util': c.utility_filename,
        'pdf': c.pdf_filename
    }
    fn = mapping.get(filetype)
    if not fn:
        abort(404)

    return send_from_directory(current_app.config['UPLOAD_FOLDER'], fn, as_attachment=False)


@business_bp.route('/clients/<int:client_id>/run-analysis', methods=['POST'])
@login_required
def run_analysis_for_client(client_id):
    client = Client.query.get_or_404(client_id)
    if client.business_user_id != current_user.id:
        abort(403)

    if not client.pdf_filename:
        flash("No credit report uploaded!", "error")
        return redirect(url_for('business.view_client', client_id=client.id))

    pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], client.pdf_filename)
    analysis_data = run_report_analysis(pdf_path)

    analysis = ClientReportAnalysis(
        client_id=client_id,
        analysis_json=json.dumps(analysis_data)
    )
    db.session.add(analysis)
    db.session.commit()

    flash("Report analysis complete!", "success")
    return redirect(url_for('business.view_client', client_id=client.id))


@business_bp.route('/clients/<int:client_id>/messages', methods=['GET', 'POST'])
@login_required
def messages_thread(client_id):
    client = Client.query.get_or_404(client_id)
    if client.business_user_id != current_user.id:
        abort(403)

    thread = MessageThread.query.filter_by(client_id=client.id).first()
    if not thread:
        thread = MessageThread(client_id=client.id)
        db.session.add(thread)
        db.session.commit()

    if request.method == 'POST':
        body = request.form.get('body', '').strip()
        if body:
            msg = Message(thread_id=thread.id, from_business=True, body=body)
            db.session.add(msg)
            db.session.commit()
        return redirect(url_for('business.messages_thread', client_id=client.id))

    return render_template('messages_thread.html', thread=thread)


@business_bp.route("/analyses/<int:analysis_id>/update-recommendations", methods=["POST"])
@login_required
def update_recommendations(analysis_id):
    analysis = ClientReportAnalysis.query.get_or_404(analysis_id)
    client = Client.query.get_or_404(analysis.client_id)

    if client.business_user_id != current_user.id:
        abort(403)

    raw_text = request.form.get("recommendations", "")
    updated_recs = [line.strip() for line in raw_text.strip().splitlines() if line.strip()]

    try:
        data = json.loads(analysis.analysis_json)
        data["recommendations"] = updated_recs
        analysis.analysis_json = json.dumps(data)
        db.session.commit()
        flash("Recommendations updated successfully!", "success")
    except Exception as e:
        flash(f"Error updating recommendations: {str(e)}", "error")

    return redirect(url_for("business.view_client", client_id=client.id))


@business_bp.route('/analyses/<int:analysis_id>/delete', methods=['POST'])
@login_required
def delete_analysis(analysis_id):
    """Delete an analysis record."""
    analysis = ClientReportAnalysis.query.get_or_404(analysis_id)
    client = Client.query.get_or_404(analysis.client_id)

    if client.business_user_id != current_user.id:
        abort(403)

    client_id = client.id
    db.session.delete(analysis)
    db.session.commit()
    flash("Analysis deleted.", "success")
    return redirect(url_for("business.view_client", client_id=client_id))


@business_bp.route('/analyses/<int:analysis_id>/send-email', methods=['POST'])
@login_required
def send_analysis_email_route(analysis_id):
    analysis_record = ClientReportAnalysis.query.get_or_404(analysis_id)
    client = Client.query.get_or_404(analysis_record.client_id)

    if client.business_user_id != current_user.id:
        abort(403)

    analysis = json.loads(analysis_record.analysis_json)
    _send_analysis_email(client, analysis)
    flash("Email sent to client!", "success")
    return redirect(url_for('business.view_client', client_id=client.id))


@business_bp.route('/clients/<int:client_id>/mail-analysis', methods=['POST'])
@login_required
def mail_analysis_to_client(client_id):
    client = Client.query.get_or_404(client_id)
    if client.business_user_id != current_user.id:
        abort(403)

    latest_analysis = ClientReportAnalysis.query.filter_by(client_id=client.id).order_by(
        ClientReportAnalysis.created_at.desc()
    ).first()
    if not latest_analysis:
        flash("No analysis found to email.", "error")
        return redirect(url_for('business.view_client', client_id=client.id))

    try:
        analysis_data = json.loads(latest_analysis.analysis_json)
        _send_analysis_email(client, analysis_data)
        flash("Analysis emailed to client!", "success")
    except Exception as e:
        flash(f"Failed to send email: {e}", "error")

    return redirect(url_for('business.view_client', client_id=client.id))


@business_bp.route("/client/<int:client_id>/run-disputegpt", methods=["POST"])
@login_required
def run_disputegpt_flow(client_id):
    client = Client.query.get_or_404(client_id)

    account_number = request.form["account_number"]
    entity = request.form["entity"]
    action = request.form["action"]
    issue = request.form["issue"]
    prompt_pack = request.form.get("prompt_pack", "default")

    parsed_accounts = extract_negative_items_from_pdf(
        os.path.join(current_app.config["UPLOAD_FOLDER"], client.pdf_filename)
    )
    selected = next((acc for acc in parsed_accounts if acc["account_number"] == account_number), None)

    if not selected:
        flash("Couldn't find the selected account.", "error")
        return redirect(url_for("business.view_client", client_id=client.id))

    ctx = {
        "entity": entity,
        "account_name": selected["account_name"],
        "account_number": selected["account_number"],
        "marks": selected["status"],
        "action": action,
        "issue": issue,
        "dispute_date": "",
        "days": "",
    }

    custom_id = request.form.get("custom_letter_id")
    if custom_id:
        tpl = CustomLetter.query.get(int(custom_id))
        if not tpl or tpl.user_id != current_user.id:
            flash("Invalid custom template.", "error")
            return redirect(url_for("business.view_client", client_id=client.id))
        prompt = tpl.body
    else:
        prompt = PACKS.get(prompt_pack, PACKS["default"])[0].format(**ctx)

    letter = generate_letter(prompt)

    flash("Letter generated!", "success")
    return render_template("disputegpt_result.html",
                           client=client,
                           letter=letter,
                           custom_letters=current_user.custom_letters,
                           custom_id=custom_id)


@business_bp.route("/client/<int:client_id>/finalize-disputegpt", methods=["POST"])
@login_required
def finalize_disputegpt_letter(client_id):
    client = Client.query.get_or_404(client_id)
    final_text = request.form["edited_letter"].strip()

    if not final_text:
        flash("No letter content to finalize.", "error")
        return redirect(url_for("business.view_client", client_id=client.id))

    upload_folder = current_app.config["UPLOAD_FOLDER"]

    # Build PDF package
    letter_pdf = letter_to_pdf(final_text, os.path.join(upload_folder, 'letter.pdf'))
    pdf_paths = [letter_pdf]

    for attr, field_type in [("id_filename", "id_file"), ("ssn_filename", "ssn_file")]:
        filename = getattr(client, attr)
        if not filename:
            continue
        path = os.path.join(upload_folder, filename)
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext in ("jpg", "jpeg", "png"):
            img_pdf = image_to_pdf(path, field_type=field_type)
            pdf_paths.append(img_pdf)
        elif ext == "pdf":
            pdf_paths.append(path)

    final_pdf = merge_dispute_package(pdf_paths, os.path.join(upload_folder, "DisputePackage.pdf"))
    final_url = url_for('business.client_file', client_id=client.id, filetype='DisputePackage', _external=True)
    session['final_pdf_url'] = final_url

    flash("Letter finalized! Ready to mail.", "success")
    return redirect(url_for('disputes.mail_letter'))


@business_bp.route('/client/<int:client_id>/extract-disputegpt', methods=['POST'])
@login_required
def extract_for_disputegpt(client_id):
    client = Client.query.get_or_404(client_id)

    if not client.pdf_filename:
        flash("No PDF found for this client.", "error")
        return redirect(url_for("business.view_client", client_id=client.id))

    pdf_path = os.path.join(current_app.config["UPLOAD_FOLDER"], client.pdf_filename)
    parsed_accounts = extract_negative_items_from_pdf(pdf_path)
    session["client_parsed_accounts"] = parsed_accounts

    flash(f"Found {len(parsed_accounts)} negative account(s) from the PDF.", "success")
    return redirect(url_for("business.view_client", client_id=client.id))


@business_bp.route('/toggle-workflow', methods=['POST'])
def toggle_workflow():
    client_id = int(request.form['client_id'])
    key = request.form['workflow_key']
    enabled = bool(int(request.form['enabled']))

    setting = WorkflowSetting.query.filter_by(client_id=client_id, key=key).first()
    if setting:
        setting.enabled = enabled
    else:
        setting = WorkflowSetting(
            client_id=client_id,
            key=key,
            enabled=enabled,
            business_user_id=current_user.id
        )
        db.session.add(setting)
    db.session.commit()

    return redirect(url_for('business.business_dashboard', client_id=client_id))


# ─── Custom Letters ───

@business_bp.route("/custom-letters")
@login_required
def list_custom_letters():
    letters = CustomLetter.query.filter_by(user_id=current_user.id).all()
    return render_template("custom_letters/list.html", letters=letters)


@business_bp.route("/custom-letters/new", methods=["GET", "POST"])
@login_required
def new_custom_letter():
    if request.method == "POST":
        letter = CustomLetter(
            user_id=current_user.id,
            name=request.form["name"],
            subject=request.form.get("subject", ""),
            body=request.form["body"]
        )
        db.session.add(letter)
        db.session.commit()
        flash("Custom letter saved!", "success")
        return redirect(url_for("business.list_custom_letters"))
    return render_template("custom_letters/new.html")


@business_bp.route("/custom-letters/<int:letter_id>/edit", methods=["GET", "POST"])
@login_required
def edit_custom_letter(letter_id):
    letter = CustomLetter.query.get_or_404(letter_id)
    if letter.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        letter.name = request.form["name"]
        letter.subject = request.form.get("subject", "")
        letter.body = request.form["body"]
        db.session.commit()
        flash("Custom letter updated.", "success")
        return redirect(url_for("business.list_custom_letters"))
    return render_template("custom_letters/edit.html", letter=letter)


@business_bp.route("/custom-letters/<int:letter_id>/delete", methods=["POST"])
@login_required
def delete_custom_letter(letter_id):
    letter = CustomLetter.query.get_or_404(letter_id)
    if letter.user_id != current_user.id:
        abort(403)
    db.session.delete(letter)
    db.session.commit()
    flash("Custom letter deleted.", "info")
    return redirect(url_for("business.list_custom_letters"))


# ─── Helper ───

def _send_analysis_email(client, analysis):
    """Send analysis results email to client."""
    from flask import render_template as rt
    msg = MailMessage(
        subject=f"DisputeGPT Analysis Results - For {client.first_name} {client.last_name}",
        sender=current_app.config['MAIL_USERNAME'],
        recipients=[client.email]
    )
    msg.html = rt("email/analysis_summary.html", client=client, analysis=analysis)
    mail.send(msg)
