from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from firebase_admin import firestore
from datetime import datetime, timedelta
from functools import wraps
import os
import smtplib
from email.message import EmailMessage
from Data.audit import log_audit_event

adoption_management_bp = Blueprint('adoption_management_bp', __name__)

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
                email_notification(admin_email, subject, html_body)
    except Exception as e:
        print(f"Failed to send new application notification to admins: {e}")

# Management
@adoption_management_bp.route('/adoption/management', methods=['GET'])
@auth_required
def management_view():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    user_doc = db.collection('Users').document(uid).get()
    role = user_doc.to_dict().get('role', 'Adopter') if user_doc.exists else 'Adopter'
   
    applications_list = []
    if role == 'Admin':
        docs = db.collection('Applications').order_by('submittedAt', direction=firestore.Query.DESCENDING).stream()
    else:
        docs = db.collection('Applications').where('userId', '==', uid).stream()
        
    now = datetime.now()
    for doc in docs:
        app_data = doc.to_dict()
        app_data['id'] = doc.id
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
                else:
                    up_dict['submittedAtFormatted'] = str(sub_at)

            weekly_updates.append(up_dict)
        app_data['weeklyUpdates'] = weekly_updates
        if not app_data.get('updatedAt') and latest_update_time:
            app_data['updatedAt'] = latest_update_time

        late_count = 0
        submitted_at_raw = app_data.get('submittedAt')
        monitoring_status = app_data.get('monitoringStatus') or app_data.get('monitoring_status') or ''
        if submitted_at_raw:
            try:
                if isinstance(submitted_at_raw, str):
                    base_date = datetime.fromisoformat(submitted_at_raw.replace(" ", "T")).replace(tzinfo=None)
                elif hasattr(submitted_at_raw, 'to_datetime'):
                    base_date = submitted_at_raw.to_datetime().replace(tzinfo=None)
                else:
                    base_date = submitted_at_raw.replace(tzinfo=None)

                submitted_weeks = {}
                for up in weekly_updates:
                    wn = up.get('weekNumber')
                    sub_time = up.get('submittedAt')
                    if wn and sub_time:
                        if isinstance(sub_time, str):
                            sub_date = datetime.fromisoformat(sub_time.replace(" ", "T")).replace(tzinfo=None)
                        elif hasattr(sub_time, 'to_datetime'):
                            sub_date = sub_time.to_datetime().replace(tzinfo=None)
                        else:
                            sub_date = sub_time.replace(tzinfo=None)
                        submitted_weeks[wn] = sub_date

                for w in range(1, 5):
                    due_date = base_date + timedelta(days=7 * w)
                    if w in submitted_weeks:
                        if submitted_weeks[w] > due_date:
                            late_count += 1
                    else:
                        if (monitoring_status == 'Ongoing' or 'Week' in monitoring_status) and now > due_date:
                            late_count += 1

            except Exception:
                pass

        app_data['late_count'] = late_count
        pet_doc = db.collection('Pets').document(app_data.get('petId')).get()
        if pet_doc.exists:
            pet_dict = pet_doc.to_dict()
            if 'name' not in pet_dict and 'petName' in pet_dict:
                pet_dict['name'] = pet_dict['petName']
            app_data['pet_details'] = pet_dict
        else:
            cached_name = app_data.get('petName') or "Unknown Pet"
            app_data['pet_details'] = {"name": cached_name, "photoUrl": ""}

        if role == 'Admin':
            applicant_doc = db.collection('Users').document(app_data.get('userId')).get()
            if applicant_doc.exists:
                applicant_dict = applicant_doc.to_dict()
                app_data['applicant_name'] = applicant_dict.get('fullName', 'Anonymous')
                app_data['applicant_pic'] = applicant_dict.get('photoURL', '')
            else:
                app_data['applicant_name'] = "Unknown User"
                app_data['applicant_pic'] = ""
                
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
        counts=counts
    )

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
                email_notification(admin_email, subject, html_body)

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
   
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

        if new_status == "Adopted":
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
                email_notification(applicant_email, subject, html_body)

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
        pending_apps = db.collection('Applications')\
                         .where('status', 'in', ['Under Review', 'Visit Scheduled', 'Return Requested', 'Return Scheduled'])\
                         .limit(1).get()
                         
        return dict(has_new_adoptions=len(pending_apps) > 0)
    except Exception as e:
        print(f"Error checking notifications: {e}")
        return dict(has_new_adoptions=False)