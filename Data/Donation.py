from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from firebase_admin import firestore
import requests
import base64
import os
import smtplib
from email.message import EmailMessage
from Data.audit import log_audit_event

donation_bp = Blueprint('donation_bp', __name__)

PAYMONGO_SECRET_KEY = os.getenv('PAYMONGO_SECRET_KEY')
PAYMONGO_PUBLIC_KEY = os.getenv('PAYMONGO_PUBLIC_KEY')

def get_db():
    return firestore.client()

def get_auth_header(key):
    encoded_key = base64.b64encode(f"{key}:".encode('utf-8')).decode('utf-8')
    return {
        "Authorization": f"Basic {encoded_key}",
        "Content-Type": "application/json"
    }

def get_admin_info():
    db = get_db()
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

def cash_donation_email(donor_email, donor_name, amount, payment_id):
    if not donor_email or '@' not in donor_email:
        print(f"Invalid donor email for cash donation: {donor_email}")
        return
    try:
        subject = "Thank You for Your Cash Donation - Tails of Freedom"
        html_body = render_template(
            'EmailNotification.html',
            reporter_name=donor_name,
            amount=f"{amount:,.2f}",
            payment_id=payment_id,
            status='Donation_Cash'
        )
        email_notification(donor_email, subject, html_body)
    except Exception as e:
        print(f"Error sending cash donation email: {e}")

def inkind_donation_email(donor_email, donor_name, donation_type, schedule, contact):
    if not donor_email or '@' not in donor_email:
        print(f"Invalid donor email for in-kind donation: {donor_email}")
        return
    try:
        subject = "Thank You for Your In-Kind Donation - Tails of Freedom"
        html_body = render_template(
            'EmailNotification.html',
            reporter_name=donor_name,
            donation_type=donation_type,
            schedule=schedule,
            contact=contact,
            status='Donation_InKind'
        )
        email_notification(donor_email, subject, html_body)
    except Exception as e:
        print(f"Error sending in-kind donation email: {e}")

@donation_bp.route('/donation', methods=['GET'])
def index():
    donors = []
    try:
        db = get_db()
        docs = list(
            db.collection('DonorWall')
            .order_by('createdAt', direction=firestore.Query.DESCENDING)
            .stream()
        )
        
        for doc in docs:
            data = doc.to_dict()
            created_at = data.get('createdAt')
            formatted_date = data.get('date') or (
                created_at.strftime("%B %d, %Y") if created_at and hasattr(created_at, 'strftime') else "N/A"
            )

            donors.append({
                "id": doc.id,
                "donorName": data.get("donorName", "Anonymous Donor"),
                "date": formatted_date,
                "badge": data.get("badge", "Supporter"),
                "photoURL": data.get("photoURL", ""),
                "notes": data.get("description", "") or data.get("notes", ""),
                "media": data.get("media", []),
                "raw_date": created_at
            })

        donors.sort(key=lambda x: x['raw_date'] if x['raw_date'] is not None else '', reverse=True)
        for d in donors:
            d.pop('raw_date', None)

    except Exception as e:
        print(f"Error fetching donor wall data: {e}")

    return render_template('Donation.html', donors=donors[:6])

# Generate QRPH
@donation_bp.route('/api/donation/qrph/generate', methods=['POST'])
def generate_qrph():
    try:
        data = request.json or {}
        amount = float(data.get('amount', 0))
        if amount < 1: 
            return jsonify({"status": "error", "message": "Minimum donation amount for QR Ph is PHP 1.00"}), 400

        user_info = session.get('user', {})
        donor_name = data.get('donor_name', user_info.get('name', 'Anonymous Donor'))
        donor_email = data.get('donor_email', user_info.get('email', 'donor@example.com'))
        photo_url = user_info.get('photoURL', '') or user_info.get('picture', '')
   
        amount_in_centavos = int(amount * 100)
        intent_payload = {
            "data": {
                "attributes": {
                    "amount": amount_in_centavos,
                    "payment_method_allowed": ["qrph"],
                    "currency": "PHP",
                    "description": f"Donation to Tails of Freedom by {donor_name}"
                }
            }
        }
        
        intent_res = requests.post(
            "https://api.paymongo.com/v1/payment_intents",
            headers=get_auth_header(PAYMONGO_SECRET_KEY),
            json=intent_payload
        )
        intent_json = intent_res.json()
        
        if 'errors' in intent_json:
            return jsonify({"status": "error", "message": intent_json['errors'][0]['detail']}), 400

        intent_id = intent_json['data']['id']
        client_key = intent_json['data']['client_key'] if 'client_key' in intent_json['data'] else intent_json['data']['attributes']['client_key']
        
        method_payload = {
            "data": {
                "attributes": {
                    "type": "qrph",
                    "expiry_seconds": 600,
                    "billing": {
                        "name": donor_name,
                        "email": donor_email
                    }
                }
            }
        }
        
        method_res = requests.post(
            "https://api.paymongo.com/v1/payment_methods",
            headers=get_auth_header(PAYMONGO_SECRET_KEY),
            json=method_payload
        )

        method_json = method_res.json()
        if 'errors' in method_json:
            return jsonify({"status": "error", "message": method_json['errors'][0]['detail']}), 400

        payment_method_id = method_json['data']['id']
        attach_payload = {
            "data": {
                "attributes": {
                    "payment_method": payment_method_id,
                    "client_key": client_key
                }
            }
        }

        attach_res = requests.post(
            f"https://api.paymongo.com/v1/payment_intents/{intent_id}/attach",
            headers=get_auth_header(PAYMONGO_SECRET_KEY),
            json=attach_payload
        )

        attach_json = attach_res.json()
        if 'errors' in attach_json:
            return jsonify({"status": "error", "message": attach_json['errors'][0]['detail']}), 400

        next_action = attach_json['data']['attributes'].get('next_action', {})
        qr_image_url = next_action.get('code', {}).get('image_url')

        if not qr_image_url:
            return jsonify({"status": "error", "message": "Failed to generate QR Ph image code."}), 500

        db = get_db()
        doc_ref = db.collection('Donations').document(intent_id)
        doc_ref.set({
            "type": "cash",
            "paymentIntentId": intent_id,
            "amount": amount,
            "currency": "PHP",
            "donorName": donor_name,
            "donorEmail": donor_email,
            "uid": user_info.get('uid', None),
            "photoURL": photo_url,
            "status": "Pending",
            "createdAt": firestore.SERVER_TIMESTAMP
        })

        log_audit_event(
            action="Generated Cash Donation QR",
            details=f"Initiated PHP {amount:,.2f} donation QR code for {donor_name}",
            user_id=user_info.get('uid'),
            user_name=donor_name
        )

        return jsonify({
            "status": "success",
            "paymentIntentId": intent_id,
            "qrCodeUrl": qr_image_url,
            "amount": amount
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# QRPH Status Payment
@donation_bp.route('/api/donation/qrph/status/<payment_intent_id>', methods=['GET'])
def check_qrph_status(payment_intent_id):
    try:
        res = requests.get(
            f"https://api.paymongo.com/v1/payment_intents/{payment_intent_id}",
            headers=get_auth_header(PAYMONGO_SECRET_KEY)
        )
        data = res.json()

        if 'errors' in data:
            return jsonify({"status": "error", "message": data['errors'][0]['detail']}), 400

        intent_status = data['data']['attributes']['status']
        if intent_status == 'succeeded':
            db = get_db()
            doc_ref = db.collection('Donations').document(payment_intent_id)
            doc_snap = doc_ref.get()
            
            already_succeeded = doc_snap.exists and doc_snap.to_dict().get('status') == 'succeeded'

            if not already_succeeded and doc_snap.exists:
                doc_ref.update({
                    "status": "succeeded",
                    "updatedAt": firestore.SERVER_TIMESTAMP
                })

                donation_data = doc_snap.to_dict()
                amount = donation_data.get('amount', 0)
                donor_name = donation_data.get('donorName', 'Donor')
                donor_email = donation_data.get('donorEmail')
                uid = donation_data.get('uid')

                log_audit_event(
                    action="Completed Cash Donation",
                    details=f"Received PHP {amount:,.2f} donation from {donor_name}",
                    user_id=uid,
                    user_name=donor_name
                )

                cash_donation_email(donor_email, donor_name, amount, payment_intent_id)

        return jsonify({"status": "success", "paymentStatus": intent_status}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# In-Kind Donations
@donation_bp.route('/api/donation/in-kind', methods=['POST'])
def submit_inkind_donation():
    try:
        data = request.json or {}
        name = data.get('name')
        contact = data.get('contact')
        donation_type = data.get('donation_type')
        schedule = data.get('schedule')
        notes = data.get('notes', '')

        if not name or not contact or not donation_type or not schedule:
            return jsonify({"status": "error", "message": "Please fill in all required fields."}), 400

        user_info = session.get('user', {})
        photo_url = user_info.get('photoURL', '') or user_info.get('picture', '')
        
        donor_email = user_info.get('email')
        if not donor_email and '@' in contact:
            donor_email = contact

        db = get_db()
        db.collection('Donations').add({
            "type": "in-kind",
            "donorName": name,
            "donorEmail": donor_email or "",
            "contactNumber": contact,
            "donationCategory": donation_type,
            "scheduledDate": schedule,
            "notes": notes,
            "uid": user_info.get('uid', None),
            "photoURL": photo_url,
            "status": "Pending Schedule",
            "createdAt": firestore.SERVER_TIMESTAMP
        })

        log_audit_event(
            action="Submitted In-Kind Donation",
            details=f"{name} submitted {donation_type} donation request scheduled for {schedule}",
            user_id=user_info.get('uid'),
            user_name=name
        )

        return jsonify({"status": "success", "message": "In-kind donation request submitted successfully!"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Donation History
@donation_bp.route('/api/donation/history', methods=['GET'])
def get_donation_history():
    try:
        user_info = session.get('user')
        if not user_info or not user_info.get('uid'):
            return jsonify({"status": "error", "message": "User not authenticated"}), 401

        uid = user_info.get('uid')
        db = get_db()
        donations_ref = db.collection('Donations').where('uid', '==', uid)
        docs = list(donations_ref.stream())

        history = []
        total_cash = 0
        completed_inkind_count = 0
        for doc in docs:
            data = doc.to_dict()
            doc_type = data.get("type", "cash")
            status = data.get("status", "Pending")

            if doc_type == "cash" and status != "succeeded":
                continue

            created_at = data.get('createdAt')
            formatted_date = created_at.strftime("%b %d, %Y %I:%M %p") if created_at and hasattr(created_at, 'strftime') else "N/A"
            if doc_type == "cash" and status == "succeeded":
                total_cash += float(data.get("amount", 0))
            elif doc_type == "in-kind" and status in ["Completed", "Received"]:
                completed_inkind_count += 1

            history.append({
                "id": doc.id,
                "type": doc_type,
                "amount": data.get("amount"),
                "category": data.get("donationCategory"),
                "status": status,
                "date": formatted_date,
                "raw_date": created_at   
            })

        if total_cash >= 10000: 
            badge = "Sponsor"
        elif total_cash >= 5000 or completed_inkind_count >= 10:
            badge = "Hero"
        elif total_cash > 0 or completed_inkind_count > 0:
            badge = "Supporter"
        else:
            badge = "Donor"

        history.sort(
            key=lambda x: x['raw_date'] if x['raw_date'] is not None else '',
            reverse=True
        )

        for item in history:
            item.pop('raw_date', None)

        return jsonify({
            "status": "success",
            "history": history,
            "badge": badge,
            "completedInKindCount": completed_inkind_count,
            "totalCash": total_cash
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Donation Wall
@donation_bp.route('/api/donor-wall/all', methods=['GET'])
def get_all_donor_wall_entries():
    try:
        db = get_db()
        docs = list(
            db.collection('DonorWall')
            .order_by('createdAt', direction=firestore.Query.DESCENDING)
            .stream()
        )
        donors = []

        for doc in docs:
            data = doc.to_dict()
            created_at = data.get('createdAt')
            formatted_date = data.get('date') or (
                created_at.strftime("%B %d, %Y") if created_at and hasattr(created_at, 'strftime') else "N/A"
            )

            donors.append({
                "id": doc.id,
                "donorName": data.get("donorName", "Anonymous Donor"),
                "badge": data.get("badge", "Supporter"),
                "description": data.get("description", "") or data.get("notes", ""),
                "photoURL": data.get("photoURL", ""),
                "media": data.get("media", []),
                "date": formatted_date,
                "raw_date": created_at
            })

        donors.sort(key=lambda x: x['raw_date'] if x['raw_date'] is not None else '', reverse=True)
        for d in donors:
            d.pop('raw_date', None)

        return jsonify({"status": "success", "data": donors}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Add Donor to Wall
@donation_bp.route('/api/donor-wall/add', methods=['POST'])
def add_donor_to_wall():
    try:
        user_info = session.get('user')
        if not user_info:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.json or {}
        donor_name = data.get('donorName', 'Anonymous Donor')
        badge = data.get('badge', 'Supporter')  
        description = data.get('description', '')
        photo_url = data.get('photoURL', '')
        media = data.get('media', [])

        if not description:
            return jsonify({"status": "error", "message": "Description is required."}), 400

        db = get_db()
        db.collection('DonorWall').add({
            "donorName": donor_name,
            "badge": badge,
            "description": description,
            "photoURL": photo_url,
            "media": media,
            "createdAt": firestore.SERVER_TIMESTAMP
        })

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Added Donor Wall Entry",
            details=f"Admin {admin_name} added donor wall entry for '{donor_name}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "Donor successfully added to the wall!"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Update Donor Wall Entry
@donation_bp.route('/api/donor-wall/update', methods=['POST'])
def update_donor_wall_entry():
    try:
        user_info = session.get('user')
        if not user_info:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.json or {}
        doc_id = data.get('id')
        if not doc_id:
            return jsonify({"status": "error", "message": "Missing entry ID."}), 400

        donor_name = data.get('donorName', 'Anonymous Donor')
        badge = data.get('badge', 'Supporter')
        description = data.get('description', '')
        photo_url = data.get('photoURL', '')
        media = data.get('media', [])

        if not description:
            return jsonify({"status": "error", "message": "Description is required."}), 400

        db = get_db()
        doc_ref = db.collection('DonorWall').document(doc_id)
        if not doc_ref.get().exists:
            return jsonify({"status": "error", "message": "Donor wall entry not found."}), 404

        update_payload = {
            "donorName": donor_name,
            "badge": badge,
            "description": description,
            "media": media,
            "updatedAt": firestore.SERVER_TIMESTAMP
        }
        if photo_url:
            update_payload["photoURL"] = photo_url

        doc_ref.update(update_payload)

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated Donor Wall Entry",
            details=f"Admin {admin_name} updated donor wall entry for '{donor_name}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "Donor wall entry updated successfully!"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Delete Donor Wall Entry
@donation_bp.route('/api/donor-wall/delete', methods=['POST'])
def delete_donor_wall_entry():
    try:
        user_info = session.get('user')
        if not user_info:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.json or {}
        doc_id = data.get('id')
        if not doc_id:
            return jsonify({"status": "error", "message": "Missing entry ID."}), 400

        db = get_db()
        doc_ref = db.collection('DonorWall').document(doc_id)
        doc_snap = doc_ref.get()

        if not doc_snap.exists:
            return jsonify({"status": "error", "message": "Donor wall entry not found."}), 404

        wall_data = doc_snap.to_dict() or {}
        donor_name = wall_data.get('donorName', 'Anonymous Donor')

        doc_ref.delete()

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Deleted Donor Wall Entry",
            details=f"Admin {admin_name} deleted donor wall entry for '{donor_name}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "Donor wall entry deleted successfully!"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@donation_bp.route('/donation-management', methods=['GET'])
def management():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('DonationManagement.html')

# Donation Management
@donation_bp.route('/api/donation/all', methods=['GET'])
def get_all_donations():
    try:
        user_info = session.get('user')
        if not user_info:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        db = get_db()
        donations_ref = db.collection('Donations').order_by('createdAt', direction=firestore.Query.DESCENDING)
        docs = list(donations_ref.stream())
        uids_to_fetch = set()
        donor_stats = {}

        for doc in docs:
            d = doc.to_dict()
            uid = d.get("uid")
            if uid and not d.get("photoURL"):
                uids_to_fetch.add(uid)

            donor_key = uid or d.get("donorName", "Anonymous")
            if donor_key not in donor_stats:
                donor_stats[donor_key] = {"cash": 0, "inkind_count": 0}

            if d.get("type") == "cash" and d.get("status") == "succeeded":
                donor_stats[donor_key]["cash"] += float(d.get("amount", 0))
            elif d.get("type") == "in-kind" and d.get("status") in ["Completed", "Received"]:
                donor_stats[donor_key]["inkind_count"] += 1

        user_photo_map = {}
        for uid in uids_to_fetch:
            try:
                user_doc = db.collection('Users').document(uid).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                    user_photo_map[uid] = user_data.get('photoURL', '') or user_data.get('picture', '')
            except Exception:
                pass

        cash_donations = []
        inkind_donations = []
        total_cash = 0
        unique_donors = set()

        for doc in docs:
            data = doc.to_dict()
            doc_id = doc.id
            created_at = data.get('createdAt')
            formatted_date = created_at.strftime("%B %d, %Y") if created_at and hasattr(created_at, 'strftime') else "N/A"
            donor_name = data.get("donorName", "Anonymous")
            uid = data.get("uid")
            donor_key = uid or donor_name
            unique_donors.add(donor_key)
            photo_url = data.get("photoURL") or user_photo_map.get(uid, "")
            stats = donor_stats.get(donor_key, {"cash": 0, "inkind_count": 0})

            if stats["cash"] >= 10000:
                badge = "Sponsor"
            elif stats["cash"] >= 5000 or stats["inkind_count"] >= 10:
                badge = "Hero"
            else:
                badge = "Supporter"

            if data.get("type") == "cash" and data.get("status") == "succeeded":
                amount = float(data.get("amount", 0))
                total_cash += amount

                cash_donations.append({
                    "id": doc_id,
                    "donorName": donor_name,
                    "amount": f"₱{amount:,.0f}",
                    "raw_amount": amount,
                    "date": formatted_date,
                    "badge": badge,
                    "photoURL": photo_url
                })

            elif data.get("type") == "in-kind":
                inkind_donations.append({
                    "id": doc_id,
                    "title": data.get("donationCategory", "In-Kind Item"),
                    "donorName": donor_name,
                    "contactNumber": data.get("contactNumber", "N/A"),
                    "notes": data.get("notes", ""),
                    "date": formatted_date,
                    "scheduledDate": data.get("scheduledDate", "N/A"),
                    "status": data.get("status", "Pending_Schedule"),
                    "badge": badge,
                    "photoURL": photo_url
                })

        return jsonify({
            "status": "success",
            "stats": {
                "totalCash": f"₱{total_cash:,.0f}",
                "uniqueDonors": len(unique_donors),
                "inKindCount": len(inkind_donations)
            },

            "cashDonations": cash_donations,
            "inKindDonations": inkind_donations
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Update Donation Status
@donation_bp.route('/api/donation/update-status', methods=['POST'])
def update_donation_status():
    try:
        user_info = session.get('user')
        if not user_info:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.json or {}
        donation_id = data.get('donation_id')
        new_status = data.get('status')

        if not donation_id or not new_status:
            return jsonify({"status": "error", "message": "Missing donation_id or status."}), 400

        db = get_db()
        doc_ref = db.collection('Donations').document(donation_id)
        doc_snap = doc_ref.get()

        if not doc_snap.exists:
            return jsonify({"status": "error", "message": "Donation not found."}), 404

        donation_data = doc_snap.to_dict()
        doc_ref.update({
            "status": new_status,
            "updatedAt": firestore.SERVER_TIMESTAMP
        })

        donor_email = donation_data.get('donorEmail')
        if not donor_email:
            contact = donation_data.get('contactNumber', '')
            if '@' in contact:
                donor_email = contact

        donor_name = donation_data.get('donorName', 'Donor')
        if new_status in ['succeeded', 'Completed', 'Received']:
            if donation_data.get('type') == 'cash':
                amount = donation_data.get('amount', 0)
                cash_donation_email(donor_email, donor_name, amount, donation_id)
            elif donation_data.get('type') == 'in-kind':
                inkind_donation_email(
                    donor_email, 
                    donor_name, 
                    donation_data.get('donationCategory'), 
                    donation_data.get('scheduledDate'), 
                    donation_data.get('contactNumber')
                )

        admin_name = user_info.get('name', 'Admin')
        admin_uid = user_info.get('uid')
        log_audit_event(
            action="Updated Donation Status",
            details=f"Updated status for donation {donor_name} to '{new_status}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": f"Status updated to {new_status}"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@donation_bp.app_context_processor
def inject_donation_notifications():
    if 'user' not in session:
        return dict(has_new_donations=False, pending_donations_count=0)
        
    try:
        db = get_db()
        pending_donations = db.collection('Donations')\
                            .where('status', 'in', ['Pending', 'Pending_Schedule'])\
                            .limit(1).get()
                            
        return dict(
            has_new_donations=len(pending_donations) > 0
        )
    
    except Exception as e:
        print(f"Error checking donation notifications: {e}")
        return dict(has_new_donations=False)