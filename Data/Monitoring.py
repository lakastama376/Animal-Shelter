from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
from firebase_admin import firestore
from datetime import datetime, timedelta, timezone
import uuid
import os
import smtplib
from email.message import EmailMessage
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

monitoring_bp = Blueprint('monitoring_bp', __name__)

def get_storage():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT', 'https://sgp.cloud.appwrite.io/v1'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63'))
    client.set_key(os.getenv('APPWRITE_API_KEY', 'standard_36d0a70b8164a32ef4e013f4137f1b37511b2d99c1064cf9fa4e8feeea1bb8f49a8ddb42ec5fcf8a33eeca64ca1cf77dc1e5537a5ca396882cd97214978482fe74adae5c0614b6d9a4c866106549d484fff2b3767b1ea9d5383ae518a91c3321cfc79a78f540bda830c6a0e457075f78446e3884ce157976fe18e4335536672f'))
    return Storage(client)

def auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_session_user_name():
    user_data = session.get('user', {})
    return user_data.get('name') or user_data.get('fullName') or user_data.get('email', 'User')

def ensure_utc(dt, default_fallback=None):
    if default_fallback is None:
        default_fallback = datetime.now(timezone.utc)
        
    if dt is None:
        return default_fallback
    if hasattr(dt, 'to_datetime'):
        dt = dt.to_datetime()
    elif isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return default_fallback

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return default_fallback

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

@monitoring_bp.route('/post-monitoring', methods=['GET'])
@auth_required
def monitoring_view():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    
    docs = db.collection('Applications')\
             .where('userId', '==', uid)\
             .where('status', '==', 'Adopted')\
             .where('monitoringStatus', '==', 'Ongoing')\
             .stream()
            
    eligible_apps = []
    for doc in docs:
        app_data = doc.to_dict()
        app_data['id'] = doc.id
        pet_doc = db.collection('Pets').document(app_data.get('petId')).get()
        app_data['pet_details'] = pet_doc.to_dict() if pet_doc.exists else {"name": app_data.get('petName') or "Unknown Pet"}
        eligible_apps.append(app_data)
        
    if not eligible_apps:
        return render_template('PostMonitoring.html', eligible_apps=[], application=None, pet=None, weeks=[], active_week=1, completed_count=0)
        
    selected_app_id = request.args.get('app_id')
    application = next((a for a in eligible_apps if a['id'] == selected_app_id), eligible_apps[0])
    pet_doc = db.collection('Pets').document(application.get('petId')).get()
    pet = pet_doc.to_dict() if pet_doc.exists else None
    updates_ref = db.collection('Applications').document(application['id']).collection('WeeklyUpdates').stream()

    submitted_weeks = {}
    for update in updates_ref:
        data = update.to_dict()
        week_num = int(data.get('weekNumber', 0))
        submitted_weeks[week_num] = data

    completed_count = len(submitted_weeks)
    active_week = completed_count + 1
    now = datetime.now(timezone.utc)
    base_time = application.get('adoptedAt') or application.get('submittedAt')
    base_date = ensure_utc(base_time, default_fallback=now)

    weeks = []
    for i in range(1, 5):
        is_submitted = i in submitted_weeks
        due_date = base_date + timedelta(days=7 * i)
        unlock_date = base_date + timedelta(days=7 * (i - 1))
        can_submit = (i == active_week) and (now >= unlock_date)
        is_overdue = (not is_submitted) and (now > due_date)

        weeks.append({
            "number": i,
            "is_submitted": is_submitted,
            "can_submit": can_submit,
            "is_overdue": is_overdue,
            "due_date": due_date.strftime("%B %d, %Y"),
            "unlock_date": unlock_date.strftime("%B %d, %Y"),
            "data": submitted_weeks.get(i, {})
        })

    if active_week <= 4:
        active_week_data = next((w for w in weeks if w['number'] == active_week), None)
        if active_week_data and active_week_data['can_submit']:
            notified_key = f"notified_week_{active_week}"
            if not application.get(notified_key):
                applicant_email = application.get('email') or user_data.get('email')
                applicant_name = get_session_user_name()
                pet_name = application.get('petName', 'your pet')

                if applicant_email:
                    subject = f"Week {active_week} Post-Adoption Monitoring Available for {pet_name}"
                    html_body = render_template(
                        'EmailNotification.html',
                        applicant_name=applicant_name,
                        pet_name=pet_name,
                        status='Monitoring Available',
                        week_number=active_week
                    )
                    email_notification(applicant_email, subject, html_body)
                    db.collection('Applications').document(application['id']).update({
                        notified_key: True
                    })

    return render_template(
        'PostMonitoring.html', 
        eligible_apps=eligible_apps,
        application=application,
        pet=pet,
        weeks=weeks,
        active_week=active_week,
        completed_count=completed_count
    )


@monitoring_bp.route('/monitoring/<application_id>/submit/<int:week_number>', methods=['POST'])
@auth_required
def submit_week_update(application_id, week_number):
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    user_name = get_session_user_name()
    full_name = user_name
    user_email = user_data.get('email')

    if uid:
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict()
            full_name = (
                u_data.get('fullName') or 
                f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or 
                user_name
            )
            user_email = u_data.get('email') or user_email

    try:
        app_ref = db.collection('Applications').document(application_id)
        app_doc = app_ref.get()

        if not app_doc.exists:
            return jsonify({"success": False, "message": "Application not found"}), 404

        app_data = app_doc.to_dict()
        if app_data.get('userId') != uid:
            return jsonify({"success": False, "message": "Unauthorized action"}), 403

        pet_name = app_data.get('petName', 'Unknown Pet')
        applicant_email = app_data.get('email') or user_email

        update_text = request.form.get('weekly_update')
        if not update_text:
            return jsonify({"success": False, "message": "Update text is required."}), 400

        uploaded_photos = request.files.getlist('update_photos')
        uploaded_videos = request.files.getlist('update_videos')
        saved_photo_urls = []
        saved_video_urls = []

        total_photos_size = sum(p.seek(0, os.SEEK_END) or p.tell() for p in uploaded_photos if p and p.filename != '')
        for p in uploaded_photos: 
            if p and p.filename != '': p.seek(0)

        if total_photos_size > 5 * 1024 * 1024:
            return jsonify({"success": False, "message": "The combined size of all photos exceeds the 5MB limit."}), 400

        total_videos_size = sum(v.seek(0, os.SEEK_END) or v.tell() for v in uploaded_videos if v and v.filename != '')
        for v in uploaded_videos: 
            if v and v.filename != '': v.seek(0)

        if total_videos_size > 25 * 1024 * 1024:
            return jsonify({"success": False, "message": "The combined size of all videos exceeds the 25MB limit."}), 400

        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645') 
        project_id = os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63')

        for photo in uploaded_photos:
            if photo and photo.filename != '':
                file_bytes = photo.read()
                filename = f"{uid}_week{week_number}_img_{uuid.uuid4().hex[:6]}_{photo.filename}"
                
                result = storage.create_file(
                    bucket_id=bucket_id,
                    file_id='unique()',
                    file=InputFile.from_bytes(file_bytes, filename)
                )
                file_id = result.id
                photo_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{file_id}/view?project={project_id}"
                saved_photo_urls.append(photo_url)

        for video in uploaded_videos:
            if video and video.filename != '':
                file_bytes = video.read()
                filename = f"{uid}_week{week_number}_vid_{uuid.uuid4().hex[:6]}_{video.filename}"
                
                result = storage.create_file(
                    bucket_id=bucket_id,
                    file_id='unique()',
                    file=InputFile.from_bytes(file_bytes, filename)
                )
                file_id = result.id
                video_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{file_id}/view?project={project_id}"
                saved_video_urls.append(video_url)

        update_id = f"week_{week_number}"
        update_payload = {
            "weekNumber": week_number,
            "text": update_text,
            "photoUrls": saved_photo_urls,  
            "videoUrls": saved_video_urls,  
            "submittedAt": firestore.SERVER_TIMESTAMP
        }

        app_ref.collection('WeeklyUpdates').document(update_id).set(update_payload)
        total_submitted = len(list(app_ref.collection('WeeklyUpdates').stream()))

        is_completed = total_submitted >= 4
        if is_completed:
            app_ref.update({
                "monitoringStatus": "Completed",
                "status": "Finalized"
            })
        else:
            app_ref.update({
                "monitoringStatus": "Ongoing"
            })

        action_title = "Completed Post-Adoption Monitoring" if is_completed else "Submitted Monitoring Update"
        details_text = f"{full_name} submitted Week {week_number} update for pet '{pet_name}'"
        if is_completed:
            details_text += " - All 4 weeks completed!"

        log_audit_event(
            action=action_title,
            details=details_text,
            user_id=uid,
            user_name=full_name
        )

        if applicant_email:
            subject = f"Week {week_number} Post-Adoption Monitoring Submitted for {pet_name}"
            html_body = render_template(
                'EmailNotification.html',
                applicant_name=full_name,
                pet_name=pet_name,
                status='Monitoring Submitted',
                week_number=week_number,
                is_completed=is_completed
            )
            email_notification(applicant_email, subject, html_body)

        return redirect(url_for('monitoring_bp.monitoring_view', app_id=application_id))

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# Notification
@monitoring_bp.app_context_processor
def inject_monitoring_status():
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    
    if not uid:
        return dict(has_ongoing_monitoring=False)

    db = firestore.client()
    docs = db.collection('Applications')\
             .where('userId', '==', uid)\
             .where('status', '==', 'Adopted')\
             .where('monitoringStatus', '==', 'Ongoing')\
             .stream()

    now = datetime.now(timezone.utc)
    requires_action = False

    for doc in docs:
        app_data = doc.to_dict()
        app_id = doc.id
        updates_ref = db.collection('Applications').document(app_id).collection('WeeklyUpdates').stream()
        submitted_weeks = {int(u.to_dict().get('weekNumber', 0)) for u in updates_ref}
        completed_count = len(submitted_weeks)

        active_week = completed_count + 1
        if active_week > 4:
            continue

        base_time = app_data.get('adoptedAt') or app_data.get('submittedAt')
        base_date = ensure_utc(base_time, default_fallback=now)
        unlock_date = base_date + timedelta(days=7 * (active_week - 1))

        if now >= unlock_date:
            requires_action = True
            break

    return dict(has_ongoing_monitoring=requires_action)