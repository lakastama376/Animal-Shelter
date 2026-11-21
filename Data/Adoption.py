from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash, Response
from firebase_admin import firestore
from datetime import datetime, timedelta
from functools import wraps
import os
import threading
import requests
import smtplib
from itsdangerous import URLSafeTimedSerializer, BadSignature
from email.message import EmailMessage

from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore

adoption_management_bp = Blueprint('adoption_management_bp', __name__)


def send_email_async(to_email, subject, html_content):
    def _worker():
        email_notification(to_email, subject, html_content)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

def auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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

def notify_admins_new_application(db, applicant_name, pet_name):
    try:
        admin_docs = db.collection('Users').where('role', '==', 'Admin').stream()
        for admin_doc in admin_docs:
            admin_data = admin_doc.to_dict()
            admin_email = admin_data.get('email')
            if admin_email:
                subject = f"New Adoption Application Submitted for {pet_name} by {applicant_name}"
                html_body = render_template(
                    'EmailNotification.html',
                    applicant_name=applicant_name,
                    pet_name=pet_name,
                    status='Admin New Application'
                )
                send_email_async(admin_email, subject, html_body)
    except Exception as e:
        print(f"Failed to send new application notification to admins: {e}")

def get_serializer():
    return URLSafeTimedSerializer(os.getenv('SECRET_KEY'))

def obfuscate_file_id(file_id):
    if not file_id:
        return ""
    return get_serializer().dumps(file_id, salt='media-proxy-salt')

def deobfuscate_file_id(token):
    try:
        return get_serializer().loads(token, salt='media-proxy-salt', max_age=86400)
    except BadSignature:
        return None

def get_file_url(file_id):
    if not file_id:
        return ""
    if file_id.startswith('http://') or file_id.startswith('https://'):
        if '/files/' in file_id and '/view' in file_id:
            try:
                parts = file_id.split('/files/')
                if len(parts) > 1:
                    file_id = parts[1].split('/')[0]
            except Exception:
                pass
        else:
            return file_id
        
    token = obfuscate_file_id(file_id)
    return url_for('adoption_management_bp.media_proxy', file_id=token, _external=True)

def get_active_adoption_block(db, uid):
    if not uid:
        return False, "", ""

    user_apps = db.collection('Applications').where('userId', '==', uid).stream()
    for doc in user_apps:
        app = doc.to_dict() or {}
        status = str(app.get('status') or '').strip()
        mon_status = str(app.get('monitoringStatus') or app.get('monitoring_status') or '').strip()

        if status in ['Returned', 'Rejected']:
            continue

        if status == 'Adopted' and mon_status.lower() in ['finalized', 'completed']:
            continue

        pet_name = app.get('petName') or 'a pet'
        if status == 'Adopted':
            detail = f"ongoing post-adoption monitoring for {pet_name}"
        else:
            detail = f"an active adoption application ({status or 'In Progress'}) for {pet_name}"

        return True, pet_name, detail

    return False, "", ""

def _submitted_sort_key(doc):
    data = doc.to_dict() or {}
    val = data.get('submittedAt')
    if not val:
        return datetime.min
    if hasattr(val, 'to_datetime'):
        return val.to_datetime().replace(tzinfo=None)
    if isinstance(val, datetime):
        return val.replace(tzinfo=None)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace(" ", "T")).replace(tzinfo=None)
        except ValueError:
            return datetime.min
    return datetime.min

# Management
@adoption_management_bp.route('/adoption/management', methods=['GET'])
@auth_required
def management_view():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    user_doc = db.collection('Users').document(uid).get()
    role = user_doc.to_dict().get('role', 'Adopter') if user_doc.exists else 'Adopter'

    has_active_adoption = False
    active_adoption_message = ""
    if role != 'Admin' and uid:
        has_active_adoption, _, active_detail = get_active_adoption_block(db, uid)
        if has_active_adoption:
            active_adoption_message = (
                f"You currently have {active_detail}. You need to complete the full adoption "
                f"and post-adoption monitoring process before starting your next adoption."
            )
   
    if role == 'Admin':
        app_docs = list(db.collection('Applications').order_by('submittedAt', direction=firestore.Query.DESCENDING).stream())
    else:
        app_docs = list(db.collection('Applications').where('userId', '==', uid).stream())
        app_docs.sort(key=_submitted_sort_key, reverse=True)
        
    if not app_docs:
        return render_template(
            'Adoption.html',
            role=role,
            applications=[],
            counts={},
            has_active_adoption=has_active_adoption,
            active_adoption_message=active_adoption_message
        )

    pet_ids = set()
    user_ids = set()
    for doc in app_docs:
        data = doc.to_dict()
        if data.get('petId'):
            pet_ids.add(data.get('petId'))
        if data.get('userId'):
            user_ids.add(data.get('userId'))

    pets_map = {}
    if pet_ids:
        for p_id in pet_ids:
            p_doc = db.collection('Pets').document(p_id).get()
            if p_doc.exists:
                p_dict = p_doc.to_dict()
                if 'name' not in p_dict and 'petName' in p_dict:
                    p_dict['name'] = p_dict['petName']
                pets_map[p_doc.id] = p_dict

    users_map = {}
    if role == 'Admin' and user_ids:
        for u_id in user_ids:
            try:
                u_doc = db.collection('Users').document(str(u_id)).get()

                if u_doc.exists:
                    users_map[u_doc.id] = u_doc.to_dict() or {}

            except Exception as e:
                print(f"Error retrieving user {u_id}: {e}")

    applications_list = []
    now = datetime.now()
    
    for doc in app_docs:
        app_data = doc.to_dict()
        app_data['id'] = doc.id
        
        gov_id = app_data.get('documentGovIdUrl') or app_data.get('document_gov_id_url')
        proof_addr = app_data.get('documentProofAddressUrl') or app_data.get('document_proof_address_url')
        
        if gov_id:
            resolved_gov = get_file_url(gov_id)
            app_data['documentGovIdUrl'] = resolved_gov
            app_data['document_gov_id_url'] = resolved_gov
        if proof_addr:
            resolved_proof = get_file_url(proof_addr)
            app_data['documentProofAddressUrl'] = resolved_proof
            app_data['document_proof_address_url'] = resolved_proof

        raw_uploaded = app_data.get('uploaded_images') or app_data.get('uploadedImages') or []
        if raw_uploaded:
            resolved_uploads = [get_file_url(img) for img in raw_uploaded]
            app_data['uploaded_images'] = resolved_uploads
            app_data['uploadedImages'] = resolved_uploads

        weekly_updates = []
        latest_update_time = None
        updates_ref = db.collection('Applications').document(doc.id).collection('WeeklyUpdates').order_by('weekNumber').stream()
        for update_doc in updates_ref:
            up_dict = update_doc.to_dict()

            if 'submittedAt' in up_dict and up_dict['submittedAt']:
                sub_at = up_dict['submittedAt']
                latest_update_time = sub_at  
                if hasattr(sub_at, 'strftime'):
                    up_dict['submittedAtFormatted'] = sub_at.strftime("%B %d, %Y %I:%M %p")
                    up_dict['submittedAt'] = sub_at.isoformat()
                else:
                    up_dict['submittedAtFormatted'] = str(sub_at)

            raw_photos = up_dict.get('photoUrls') or up_dict.get('photos') or []
            up_dict['photoUrls'] = [get_file_url(p) for p in raw_photos]
            raw_videos = up_dict.get('videoUrls') or up_dict.get('videos') or []
            up_dict['videoUrls'] = [get_file_url(v) for v in raw_videos]
            weekly_updates.append(up_dict)

        app_data['weeklyUpdates'] = weekly_updates
        app_data['weekly_updates'] = weekly_updates

        if not app_data.get('updatedAt') and latest_update_time:
            app_data['updatedAt'] = latest_update_time

        late_count = 0
        submitted_at_raw = app_data.get('submittedAt')
        monitoring_status = app_data.get('monitoringStatus') or app_data.get('monitoring_status') or ''
        
        if submitted_at_raw and (app_data.get('status') == 'Adopted' or monitoring_status == 'Ongoing' or 'Week' in monitoring_status):
            try:
                if isinstance(submitted_at_raw, str):
                    base_date = datetime.fromisoformat(submitted_at_raw.replace(" ", "T")).replace(tzinfo=None)
                elif hasattr(submitted_at_raw, 'to_datetime'):
                    base_date = submitted_at_raw.to_datetime().replace(tzinfo=None)
                else:
                    base_date = submitted_at_raw.replace(tzinfo=None)

                submitted_weeks = {int(up['weekNumber']) for up in weekly_updates if up.get('weekNumber')}

                total_days_late = 0
                for w in range(1, 5):
                    due_date = base_date + timedelta(days=7 * w)
                    if w not in submitted_weeks and now > due_date:
                        days_overdue = (now - due_date).days
                        total_days_late += days_overdue

                if total_days_late > 0:
                    late_count = min(total_days_late, 150)

            except Exception as e:
                print(f"Error calculating overdue count: {e}")

        app_data['late_count'] = late_count

        pet_id = app_data.get('petId')
        if pet_id and pet_id in pets_map:
            p_dict = pets_map[pet_id]
            if p_dict.get('images'):
                p_dict['images'] = [get_file_url(img) for img in p_dict['images'] if img]
            if p_dict.get('photoUrl'):
                p_dict['photoUrl'] = get_file_url(p_dict['photoUrl'])
            app_data['pet_details'] = p_dict
        else:
            cached_name = app_data.get('petName') or "Unknown Pet"
            app_data['pet_details'] = {"name": cached_name, "photoUrl": ""}

        if role == 'Admin':
            user_id = app_data.get('userId')
            applicant_dict = None

            if user_id:
                applicant_dict = users_map.get(str(user_id))

            if not applicant_dict:
                applicant_email = app_data.get('email') or app_data.get('applicantEmail')

                if applicant_email:
                    try:
                        email_query = db.collection('Users').where(
                            'email', '==', applicant_email
                        ).limit(1).get()

                        if email_query:
                            applicant_dict = email_query[0].to_dict() or {}

                    except Exception as e:
                        print(f"Error retrieving applicant using email: {e}")

            if applicant_dict:
                applicant_name = (
                    applicant_dict.get('fullName')
                    or applicant_dict.get('displayName')
                    or f"{applicant_dict.get('firstName', '')} {applicant_dict.get('lastName', '')}".strip()
                    or applicant_dict.get('name')
                )
                app_data['applicant_name'] = applicant_name or 'Anonymous'

                raw_pic = (
                    applicant_dict.get('photoURL')
                    or applicant_dict.get('profilePicture')
                    or applicant_dict.get('profilePictureUrl')
                    or ''
                )
                app_data['applicant_pic'] = get_file_url(raw_pic) if raw_pic else ''

            else:
                app_data['applicant_name'] = (
                    app_data.get('fullName')
                    or app_data.get('applicantName')
                    or app_data.get('name')
                    or 'Unknown User'
                )

                raw_pic = (
                    app_data.get('photoURL')
                    or app_data.get('profilePicture')
                    or ''
                )

                app_data['applicant_pic'] = get_file_url(raw_pic) if raw_pic else ''
                
        applications_list.append(app_data)

    counts = {
        'Submitted': 0,
        'Under Review': 0,
        'Approved': 0,
        'Visit Scheduled': 0,
        'Adopted': 0,
        'Return Requested': 0,
        'Return Scheduled': 0,
        'Returned': 0,
        'Rejected': 0,
        'Monitoring Ongoing': 0,
        'Monitoring Completed': 0,
        'Overdue Monitoring': 0
    }

    for app in applications_list:
        status = app.get('status')
        if status in counts:
            counts[status] += 1

        monitoring_status = app.get('monitoringStatus') or app.get('monitoring_status') or ''
        is_returned_state = status in ['Returned', 'Return Requested', 'Return Scheduled']

        if not is_returned_state:
            if monitoring_status == 'Ongoing' or 'Week' in monitoring_status:
                counts['Monitoring Ongoing'] += 1
                if app.get('late_count', 0) > 0:
                    counts['Overdue Monitoring'] += 1
            elif monitoring_status == 'Completed':
                counts['Monitoring Completed'] += 1

    return render_template(
        'Adoption.html',
        role=role,
        applications=applications_list,
        counts=counts,
        has_active_adoption=has_active_adoption,
        active_adoption_message=active_adoption_message
    )

@adoption_management_bp.route('/adoption/media-proxy/<path:file_id>')
@auth_required
def media_proxy(file_id):
    try:
        real_file_id = deobfuscate_file_id(file_id)
        if not real_file_id:
            return jsonify({"error": "Not found"}), 404

        bucket_id = os.getenv('APPWRITE_BUCKET_ID')
        project_id = os.getenv('APPWRITE_PROJECT_ID')
        api_key = os.getenv('APPWRITE_API_KEY')
        endpoint = os.getenv('APPWRITE_ENDPOINT')
        appwrite_url = f"{endpoint}/storage/buckets/{bucket_id}/files/{real_file_id}/view?project={project_id}"
        
        headers = {
            'X-Appwrite-Key': api_key,
            'X-Appwrite-Project': project_id
        }
        
        resp = requests.get(appwrite_url, headers=headers, stream=True)
        if resp.status_code == 200:
            return Response(resp.iter_content(chunk_size=1024), content_type=resp.headers.get('content-type', 'image/jpeg'))
        else:
            return jsonify({"error": "File not found or access denied"}), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Request Return
@adoption_management_bp.route('/adoption/request-return/<app_id>', methods=['POST'])
@auth_required
def request_pet_return(app_id):
    try:
        db = firestore.client()
        data = request.json or {}
        return_reason = data.get('returnReason')

        app_ref = db.collection('Applications').document(app_id)
        app_doc = app_ref.get()
        if not app_doc.exists:
            return jsonify({"status": "error", "message": "Application not found."}), 404

        app_data = app_doc.to_dict()
        user_id = app_data.get('userId')
        pet_id = app_data.get('petId')
        update_payload = {
            'status': 'Return Requested',
            'updatedAt': firestore.SERVER_TIMESTAMP
        }

        if return_reason:
            update_payload['returnReason'] = return_reason

        app_ref.update(update_payload)
        applicant_name = "Adopter"
        if user_id:
            user_doc = db.collection('Users').document(user_id).get()
            if user_doc.exists:
                u_data = user_doc.to_dict()
                applicant_name = u_data.get('fullName') or u_data.get('firstName') or applicant_name

        pet_name = app_data.get('petName') or "Unknown Pet"
        if pet_id:
            pet_doc = db.collection('Pets').document(pet_id).get()
            if pet_doc.exists:
                pet_data = pet_doc.to_dict()
                pet_name = pet_data.get('name') or pet_data.get('petName') or pet_name

        current_uid = session['user'].get('uid') or session['user'].get('user_id')
        current_user_name = applicant_name
        if current_uid:
            curr_user_doc = db.collection('Users').document(current_uid).get()
            if curr_user_doc.exists:
                cu_data = curr_user_doc.to_dict()
                current_user_name = cu_data.get('fullName') or f"{cu_data.get('firstName', '')} {cu_data.get('lastName', '')}".strip() or current_user_name

        log_audit_event(
            action="Requested Pet Return",
            details=f"{current_user_name} requested a return for adoption application (Pet: {pet_name}). Reason: {return_reason or 'None provided'}",
            user_id=current_uid,
            user_name=current_user_name
        )

        admin_docs = db.collection('Users').where('role', '==', 'Admin').stream()
        for admin_doc in admin_docs:
            admin_data = admin_doc.to_dict()
            admin_email = admin_data.get('email')
            if admin_email:
                subject = f"Pet Return Requested for {pet_name} by {applicant_name}"
                html_body = render_template(
                    'EmailNotification.html',
                    applicant_name=applicant_name,
                    pet_name=pet_name,
                    status='Admin Return Requested',
                    return_reason=return_reason
                )
                send_email_async(admin_email, subject, html_body)

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@adoption_management_bp.route('/adoption/suggest-schedule/<app_id>', methods=['POST'])
@auth_required
def suggest_adoption_schedule(app_id):
    try:
        db = firestore.client()
        user_data = session.get('user', {})
        current_uid = user_data.get('uid') or user_data.get('user_id')

        if not current_uid:
            return jsonify({"status": "error", "message": "Your session is invalid. Please log in again."}), 401

        data = request.json or {}
        suggested_date = data.get('suggestedDate', '').strip()
        if not suggested_date:
            return jsonify({"status": "error", "message": "Please select your preferred date and time."}), 400
        try:
            selected_datetime = datetime.fromisoformat(suggested_date)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid date format."}), 400

        if selected_datetime <= datetime.now():
            return jsonify({"status": "error", "message": "Please select a future date and time."}), 400

        app_ref = db.collection('Applications').document(app_id)
        app_doc = app_ref.get()
        if not app_doc.exists:
            return jsonify({"status": "error", "message": "Application not found."}), 404

        app_data = app_doc.to_dict() or {}
        if str(app_data.get('userId', '')) != str(current_uid):
            return jsonify({"status": "error", "message": "You are not authorized to update this application."}), 403

        current_status = str(app_data.get('status', '')).strip()
        if current_status in ['Approved', 'Visit Scheduled']:
            update_payload = {
                'suggestedVisitation': suggested_date,
                'suggestedVisitationUpdatedAt': firestore.SERVER_TIMESTAMP
            }
            suggestion_type = "visitation"

        elif current_status == 'Return Scheduled':
            update_payload = {
                'suggestedReturn': suggested_date,
                'suggestedReturnUpdatedAt': firestore.SERVER_TIMESTAMP
            }
            suggestion_type = "return"

        else:
            return jsonify({"status": "error","message": "Schedule suggestions are only allowed when status is Approved, Visit Scheduled, or Return Scheduled."}), 400

        app_ref.update(update_payload)
        return jsonify({"status": "success", "message": "Your preferred schedule has been submitted successfully.", "type": suggestion_type }), 200

    except Exception as e:
        print(f"Error submitting schedule suggestion: {e}")
        return jsonify({"status": "error", "message": "An error occurred while submitting your schedule suggestion."}), 500

# Update Status
@adoption_management_bp.route('/adoption/update-status/<app_id>', methods=['POST'])
@admin_only
def update_status(app_id):
    try:
        db = firestore.client()
        data = request.json or {}
        new_status = data.get('status')
        visitation_date = data.get('scheduleVisitation')
        return_date = data.get('scheduleReturn')
       
        app_ref = db.collection('Applications').document(app_id)
        app_doc = app_ref.get()
        if not app_doc.exists:
            return jsonify({"status": "error", "message": "Application not found."}), 404

        app_data = app_doc.to_dict()
        old_status = app_data.get('status')
        old_visitation = app_data.get('scheduleVisitation')
        user_id = app_data.get('userId')
        pet_id = app_data.get('petId')
        update_payload = {"status": new_status}

        if visitation_date:
            update_payload['scheduleVisitation'] = visitation_date
        if return_date:
            update_payload['scheduleReturn'] = return_date

        if new_status in ["Approved", "Visit Scheduled"]:
            if pet_id:
                pet_ref = db.collection('Pets').document(pet_id)
                pet_doc = pet_ref.get()
                if pet_doc.exists and (pet_doc.to_dict() or {}).get('status') != 'Adopted':
                    pet_ref.update({'status': 'Reserved'})

        elif new_status == "Adopted":
            update_payload['monitoringStatus'] = "Ongoing"

            if old_status != "Adopted" and user_id:
                user_ref = db.collection('Users').document(user_id)
                user_ref.update({
                    'adoptionCount': firestore.Increment(1)
                })

            if pet_id:
                pet_ref = db.collection('Pets').document(pet_id)
                pet_ref.update({'status': 'Adopted'})

        elif new_status == "Returned" or (old_status == "Adopted" and new_status != "Adopted"):
            if pet_id:
                pet_ref = db.collection('Pets').document(pet_id)
                pet_ref.update({'status': 'Available'})

            if user_id and old_status == "Adopted":
                user_ref = db.collection('Users').document(user_id)
                user_ref.update({
                    'adoptionCount': firestore.Increment(-1)
                })

        elif old_status in ["Approved", "Visit Scheduled"] and new_status not in ["Approved", "Visit Scheduled", "Adopted"]:
            if pet_id:
                other_reserved = False
                other_apps = db.collection('Applications').where('petId', '==', pet_id).stream()
                for other_doc in other_apps:
                    if other_doc.id == app_id:
                        continue
                    other_st = (other_doc.to_dict() or {}).get('status')
                    if other_st in ["Approved", "Visit Scheduled"]:
                        other_reserved = True
                        break
                pet_ref = db.collection('Pets').document(pet_id)
                pet_doc = pet_ref.get()
                if pet_doc.exists and (pet_doc.to_dict() or {}).get('status') != 'Adopted':
                    pet_ref.update({'status': 'Reserved' if other_reserved else 'Available'})

        app_ref.update(update_payload)
        admin_uid = session['user'].get('uid') or session['user'].get('user_id')
        admin_name = "Admin"
        if admin_uid:
            admin_user_doc = db.collection('Users').document(admin_uid).get()
            if admin_user_doc.exists:
                au_data = admin_user_doc.to_dict()
                admin_name = au_data.get('fullName') or f"{au_data.get('firstName', '')} {au_data.get('lastName', '')}".strip() or admin_name

        pet_name = "unknown pet"
        if pet_id:
            pet_doc = db.collection('Pets').document(pet_id).get()
            if pet_doc.exists:
                pet_data = pet_doc.to_dict()
                pet_name = pet_data.get('name') or pet_data.get('petName') or pet_name
        else:
            pet_name = app_data.get('petName') or pet_name

        log_audit_event(
            action="Updated Adoption Status",
            details=f"Admin {admin_name} changed adoption application status for {pet_name} from '{old_status}' to '{new_status}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        applicant_email = None
        applicant_name = "Adopter"
        if user_id:
            user_doc = db.collection('Users').document(user_id).get()
            if user_doc.exists:
                u_data = user_doc.to_dict()
                applicant_email = u_data.get('email')
                applicant_name = u_data.get('fullName') or u_data.get('firstName') or applicant_name

        if not applicant_email:
            applicant_email = app_data.get('email')

        if applicant_email:
            subject = None
            email_status = None

            if new_status == "Approved" and old_status != "Approved":
                subject = f"Adoption Application Approved for {pet_name}"
                email_status = "Approved"

            elif visitation_date and (visitation_date != old_visitation or new_status == "Visit Scheduled"):
                subject = f"Visit Schedule Confirmed for {pet_name}"
                email_status = "Visit Scheduled"

            elif return_date and (new_status == "Return Scheduled"):
                subject = f"Pet Return Drop-off Scheduled for {pet_name}"
                email_status = "Return Scheduled"

            elif new_status == "Adopted" and old_status != "Adopted":
                subject = f"Adoption Finalized for {pet_name}"
                email_status = "Adopted"

            elif new_status == "Rejected" and old_status != "Rejected":
                subject = f"Update Regarding Your Adoption Application for {pet_name}"
                email_status = "Rejected"

            if email_status:
                html_body = render_template(
                    'EmailNotification.html',
                    applicant_name=applicant_name,
                    pet_name=pet_name,
                    status=email_status,
                    visitation_date=visitation_date,
                    return_date=return_date
                )
                send_email_async(applicant_email, subject, html_body)

        return jsonify({"status": "success", "message": "Application updated cleanly."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
# Notification
@adoption_management_bp.app_context_processor
def inject_adoption_notifications():
    if 'user' not in session:
        return dict(has_new_adoptions=False)
        
    try:
        db = firestore.client()
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')
        user_doc = db.collection('Users').document(uid).get()
        role = user_doc.to_dict().get('role', 'Adopter') if user_doc.exists else 'Adopter'
        
        query = db.collection('Applications')
        if role == 'Admin':
            query = query.where('status', 'in', ['Submitted', 'Under Review', 'Approved', 'Return Requested'])
        else:
            query = query.where('userId', '==', uid)\
                   .where('status', 'in', ['Approved', 'Visit Scheduled', 'Return Scheduled'])
                   
        pending_apps = query.limit(1).get()
        return dict(has_new_adoptions=len(pending_apps) > 0)
        
    except Exception as e:
        print(f"Error checking notifications: {e}")
        return dict(has_new_adoptions=False)