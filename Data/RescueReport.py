from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from firebase_admin import firestore
from functools import wraps
from datetime import datetime
import os
import uuid
import smtplib
from email.message import EmailMessage
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

rescue_report_bp = Blueprint('rescue_report_bp', __name__)

def get_storage():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT', 'https://sgp.cloud.appwrite.io/v1'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63'))
    client.set_key(os.getenv('APPWRITE_API_KEY', 'standard_36d0a70b8164a32ef4e013f4137f1b37511b2d99c1064cf9fa4e8feeea1bb8f49a8ddb42ec5fcf8a33eeca64ca1cf77dc1e5537a5ca396882cd97214978482fe74adae5c0614b6d9a4c866106549d484fff2b3767b1ea9d5383ae518a91c3321cfc79a78f540bda830c6a0e457075f78446e3884ce157976fe18e4335536672f'))
    return Storage(client)

def get_shelter_settings(db):
    settings_ref = db.collection('Settings').document('shelter_config')
    doc = settings_ref.get()
    
    if doc.exists:
        return doc.to_dict()
    else:
        default_settings = {
            "maxCapacity": 50,
            "isAccepting": True,
            "enforceBoundary": True,   
            "showShelterPin": True
        }
        settings_ref.set(default_settings)
        return default_settings

def auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_session_user_name():
    user_data = session.get('user', {})
    return user_data.get('name') or user_data.get('fullName') or user_data.get('email', 'User')

def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        
        db = firestore.client()
        uid = session['user'].get('uid') or session['user'].get('user_id')
        user_doc = db.collection('Users').document(uid).get()
        
        if not user_doc.exists or user_doc.to_dict().get('role') != 'Admin':
            return jsonify({"status": "error", "message": "Access Denied. Admins only."}), 403
        return f(*args, **kwargs)
    return decorated_function

def get_admin_info():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id') or "Unknown"
    admin_name = user_data.get('name') or "Admin"
    if uid != "Unknown":
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict()
            admin_name = u_data.get('fullName') or u_data.get('name') or f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or admin_name
    return uid, admin_name

# Email Notification
def email_notification(to_email, subject, html_content):
    smtp_server = os.getenv('MAIL_SERVER')
    smtp_port = int(os.getenv('MAIL_PORT', 587))
    sender_email = os.getenv('MAIL_USERNAME')
    sender_password = os.getenv('MAIL_PASSWORD')
    sender_name = os.getenv('MAIL_DEFAULT_SENDER_NAME', 'Tails of Freedom Animal Haven')

    if not sender_email or not sender_password:
        print("Email credentials (MAIL_USERNAME/MAIL_PASSWORD) not configured. Skipping email notification.")
        return False

    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = f"{sender_name} <{sender_email}>"
        msg['To'] = to_email
        msg.set_content("Please view this email in an HTML-compatible client.") 
        msg.add_alternative(html_content, subtype='html')

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
            
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False
    
def notify_admins_new_rescue(db, reporter_name, animal_type, location_text):
    try:
        admin_docs = db.collection('Users').where('role', '==', 'Admin').stream()
        for admin_doc in admin_docs:
            admin_data = admin_doc.to_dict()
            admin_email = admin_data.get('email')
            if admin_email:
                subject = f"New Rescue Report Submitted for {animal_type or 'stray animal'}"
                html_body = render_template(
                    'EmailNotification.html',
                    reporter_name=reporter_name,
                    animal_type=animal_type or 'stray animal',
                    location_text=location_text or 'Unspecified Location',
                    status='Admin New Rescue Report'
                )
                email_notification(admin_email, subject, html_body)
    except Exception as e:
        print(f"Failed to notify admins of new rescue report: {e}")
        
# Rescue Form 
@rescue_report_bp.route('/report', methods=['GET'])
@auth_required
def index():
    try:
        db = firestore.client()
        settings = get_shelter_settings(db)
        max_capacity = settings.get('maxCapacity', 50)
        is_accepting_toggle = settings.get('isAccepting', True)
        enforce_boundary = settings.get('enforceBoundary', True)   
        show_shelter_pin = settings.get('showShelterPin', True) 
        pets_ref = db.collection('Pets')
        active_pets = pets_ref.where('status', '!=', 'Adopted').stream()
        occupied_spaces = sum(1 for _ in active_pets)
        capacity_percentage = round((occupied_spaces / max_capacity) * 100, 1) if max_capacity > 0 else 0
        is_accepting = is_accepting_toggle and (occupied_spaces < max_capacity)
        status_text = "Accepting" if is_accepting else "Intake Paused"
        
        capacity_data = {
            "occupied": occupied_spaces,
            "max": max_capacity,
            "percentage": min(capacity_percentage, 100),
            "status": status_text,
            "enforceBoundary": enforce_boundary,
            "showShelterPin": show_shelter_pin  
        }
        
        return render_template('RescueReport.html', capacity=capacity_data)
    except Exception as e:
        print(f"Error checking shelter capacity: {e}")
        fallback_capacity = {
            "occupied": 0, 
            "max": 50, 
            "percentage": 0, 
            "status": "Accepting", 
            "enforceBoundary": True,
            "showShelterPin": True  
        }
        return render_template('RescueReport.html', capacity=fallback_capacity)
    
# Submission Report
@rescue_report_bp.route('/report/submit', methods=['POST'])
@auth_required
def submit_report():
    try:
        db = firestore.client()
        settings = get_shelter_settings(db)
        max_capacity = settings.get('maxCapacity', 50)
        is_accepting_toggle = settings.get('isAccepting', True)
        pets_ref = db.collection('Pets')
        active_pets = pets_ref.where('status', '!=', 'Adopted').stream()
        occupied_spaces = sum(1 for _ in active_pets)
        is_accepting = is_accepting_toggle and (occupied_spaces < max_capacity)
        
        if not is_accepting:
            flash("Intake is currently paused or shelter capacity is reached. We cannot accept new rescue reports at this time.", "error")
            return redirect(url_for('rescue_report_bp.index'))
        
        media_list = []
        photo_count = 0
        video_count = 0
        total_size = 0
        max_combined_size = 25 * 1024 * 1024
        photos = request.files.getlist('rescue_photos')
        videos = request.files.getlist('rescue_videos')
        photos = [p for p in photos if p and p.filename != '']
        videos = [v for v in videos if v and v.filename != '']
        files = photos + videos

        if not files:
            flash("Media upload is required! Please capture 3 photos and 1 video.", "error")
            return redirect(url_for('rescue_report_bp.index'))

        for file in files:
            if file and file.filename != '':
                file.seek(0, os.SEEK_END)
                total_size += file.tell()
                file.seek(0) 

                content_type = file.content_type or ""
                if content_type.startswith('image/'):
                    photo_count += 1
                elif content_type.startswith('video/'):
                    video_count += 1

        if total_size > max_combined_size:
            flash("Upload failed. Total file size exceeds the strict 25MB limitation.", "error")
            return redirect(url_for('rescue_report_bp.index'))

        if photo_count != 3 or video_count != 1:
            flash(f"Upload failed. You must provide exactly 3 photos and 1 video (Received: {photo_count} photos, {video_count} video).", "error")
            return redirect(url_for('rescue_report_bp.index'))

        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645') 
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id') or "Anonymous"

        for file in files:
            if file and file.filename != '':
                file_bytes = file.read()
                filename = f"{uid}_rescue_{uuid.uuid4().hex[:6]}_{file.filename}"
                
                result = storage.create_file(
                    bucket_id=bucket_id,
                    file_id='unique()',
                    file=InputFile.from_bytes(file_bytes, filename)
                )
                file_id = result.id
                file_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{file_id}/view?project=6a9e899400185f330d63"
                media_list.append(file_url)

        user_name = get_session_user_name()
        full_name = user_name
        if uid != "Anonymous":
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                u_data = user_doc.to_dict()
                full_name = (
                    u_data.get('fullName') or 
                    f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or 
                    user_name
                )

        latitude_val = request.form.get('latitude', '').strip()
        longitude_val = request.form.get('longitude', '').strip()
        animal_type = request.form.get('animalType', '').strip()
        location_text = request.form.get('location', '').strip()

        report_payload = {
            "reporterUid": uid,
            "animalType": animal_type,
            "locationText": location_text,
            "latitude": float(latitude_val) if latitude_val else None,
            "longitude": float(longitude_val) if longitude_val else None,
            "animalCondition": request.form.get('condition', '').strip(),
            "contactInfo": request.form.get('contact', '').strip(),
            "description": request.form.get('description', '').strip(),
            "additionalNotes": request.form.get('additionalNotes', '').strip(),
            "media": media_list,  
            "status": "Under Review",
            "submittedAt": firestore.SERVER_TIMESTAMP
        }

        db.collection('RescueReports').add(report_payload)

        if uid != "Anonymous":
            user_ref = db.collection('Users').document(uid)
            user_ref.update({"rescueReportsSubmitted": firestore.Increment(1)})

        log_audit_event(
            action="Submitted Rescue Report",
            details=f"{full_name} submitted rescue report for a {animal_type or 'stray animal'} at '{location_text}'",
            user_id=uid,
            user_name=full_name
        )

        notify_admins_new_rescue(db, full_name, animal_type, location_text)
        flash("Rescue report submitted successfully! Thank you for looking out for them.", "success")
        return redirect(url_for('rescue_report_bp.management_view'))
    except Exception as e:
        print(f"Failed to submit rescue report: {e}")
        flash("An error occurred while submitting your report. Please try again.", "error")

    return redirect(url_for('rescue_report_bp.index'))

# Rescue Management
@rescue_report_bp.route('/rescue/management', methods=['GET'])
@auth_required
def management_view():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    user_doc = db.collection('Users').document(uid).get()
    role = user_doc.to_dict().get('role', 'User') if user_doc.exists else 'User'
    
    reports_list = []
    if role == 'Admin':
        docs = db.collection('RescueReports').order_by('submittedAt', direction=firestore.Query.DESCENDING).stream()
    else:
        docs = db.collection('RescueReports').where('reporterUid', '==', uid).stream()
        
    for doc in docs:
        report_data = doc.to_dict()
        report_data['id'] = doc.id
        ts = report_data.get('submittedAt')
        if ts and hasattr(ts, 'strftime'):
            report_data['formatted_submitted'] = ts.strftime('%B %d, %Y')
        else:
            report_data['formatted_submitted'] = 'Recently'
        
        if role == 'Admin':
            reporter_doc = db.collection('Users').document(report_data.get('reporterUid', '')).get()
            if reporter_doc.exists:
                reporter_dict = reporter_doc.to_dict()
                report_data['reporter_name'] = reporter_dict.get('fullName', 'Anonymous')
            else:
                report_data['reporter_name'] = "Anonymous"
                
        reports_list.append(report_data)
        
    counts = {}
    for r in reports_list:
        status = r.get('status')
        if status:
            counts[status] = counts.get(status, 0) + 1

    return render_template(
        'RescueManagement.html', 
        role=role, 
        reports=reports_list, 
        counts=counts
    )

@rescue_report_bp.route('/rescue/update-status/<report_id>', methods=['POST'])
@admin_only
def update_status(report_id):
    try:
        db = firestore.client()
        data = request.json or {}
        new_status = data.get('status')
        
        if not new_status:
            return jsonify({"status": "error", "message": "Status parameter required."}), 400

        report_ref = db.collection('RescueReports').document(report_id)
        report_doc = report_ref.get()
        if not report_doc.exists:
            return jsonify({"status": "error", "message": "Rescue report not found."}), 404

        report_data = report_doc.to_dict()
        old_status = report_data.get('status')
        animal_type = report_data.get('animalType') or 'stray animal'
        report_ref.update({
            "status": new_status,
            "updatedAt": firestore.SERVER_TIMESTAMP
        })

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated Rescue Report Status",
            details=f"Admin {admin_name} changed rescue report status for {animal_type} from '{old_status}' to '{new_status}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        if new_status != old_status and new_status in ['Dispatched', 'Resolved']:
            reporter_uid = report_data.get('reporterUid')
            reporter_email = None
            reporter_name = "Reporter"

            if reporter_uid and reporter_uid != "Anonymous":
                user_doc = db.collection('Users').document(reporter_uid).get()
                if user_doc.exists:
                    u_data = user_doc.to_dict()
                    reporter_email = u_data.get('email')
                    reporter_name = u_data.get('fullName') or u_data.get('firstName') or reporter_name

            if not reporter_email:
                contact_info = report_data.get('contactInfo', '')
                if '@' in contact_info:
                    reporter_email = contact_info

            if reporter_email:
                location_text = report_data.get('locationText') or 'the specified location'

                subject = f"Rescue Status Update: {new_status} ({animal_type})"
                
                html_body = render_template(
                    'EmailNotification.html',
                    reporter_name=reporter_name,
                    animal_type=animal_type,
                    location_text=location_text,
                    status=new_status
                )

                email_notification(reporter_email, subject, html_body)

        return jsonify({"status": "success", "message": "Rescue report status updated successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
# Notification 
@rescue_report_bp.app_context_processor
def inject_rescue_notifications():
    if 'user' not in session:
        return dict(has_new_rescues=False)
        
    try:
        db = firestore.client()
        pending_reports = db.collection('RescueReports')\
                            .where('status', 'in', ['Submitted', 'Under Review'])\
                            .limit(1).get()
        return dict(has_new_rescues=len(pending_reports) > 0)
    except Exception:
        return dict(has_new_rescues=False)