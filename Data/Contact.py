from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from firebase_admin import firestore
from functools import wraps
import os
import smtplib
import threading
from email.message import EmailMessage
from Data.audit import log_audit_event

contact_bp = Blueprint('contact_bp', __name__)

def get_db():
    return firestore.client()

# Email Notification
def _send_email_sync(to_email, subject, html_content):
    smtp_server = os.getenv('MAIL_SERVER')
    smtp_port = int(os.getenv('MAIL_PORT', 587))
    sender_email = os.getenv('MAIL_USERNAME')
    sender_password = os.getenv('MAIL_PASSWORD')
    sender_name = os.getenv('MAIL_DEFAULT_SENDER_NAME', 'Tails of Freedom Animal Haven')

    if not smtp_server or not sender_email or not sender_password:
        print("Email credentials/server not configured. Skipping contact email notification.")
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
        print(f"Failed to send contact email to {to_email}: {e}")
        return False


def send_email_async(to_email, subject, html_content):
    def _worker():
        _send_email_sync(to_email, subject, html_content)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


@contact_bp.route('/contact')
def contact_page():
    user_info = None
    if 'user' in session:
        uid = session['user'].get('uid')
        if uid:
            try:
                db = get_db()
                doc = db.collection('Users').document(uid).get()
                if doc.exists:
                    user_info = doc.to_dict()
            except Exception as e:
                print(f"Error fetching user profile for contact form: {e}")
                
    return render_template('Contact.html', user_info=user_info)

# Direct Message with Admins
@contact_bp.route('/api/contact/send', methods=['POST'])
def send_contact_message():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    subject = (data.get('subject') or '').strip()
    message_text = (data.get('message') or '').strip()

    if not name or not email or not subject or not message_text:
        return jsonify({"status": "error", "message": "Please fill out all required fields."}), 400

    db = get_db()
    now = firestore.SERVER_TIMESTAMP
    admin_uids = []
    admin_emails = set()
    for admin_doc in db.collection('Users').where('role', 'in', ['Admin', 'admin']).stream():
        admin_uids.append(admin_doc.id)
        u_data = admin_doc.to_dict() or {}
        admin_email = (u_data.get('email') or '').strip()
        if admin_email:
            admin_emails.add(admin_email)

    if not admin_uids:
        return jsonify({"status": "error", "message": "No administrators found to receive your message."}), 503

    if 'user' in session and session['user'].get('uid'):
        sender_uid = session['user']['uid']
    else:
        users = db.collection('Users').where('email', '==', email).limit(1).get()
        if users:
            sender_uid = users[0].id
        else:
            sender_uid = f"guest_{email.replace('@', '_at_').replace('.', '_')}"

    try:
        conv_query = db.collection('Conversations')\
                       .where('participants', 'array_contains', sender_uid)\
                       .stream()
        
        conv_doc_id = None
        existing_participants = []
        for doc in conv_query:
            c_data = doc.to_dict() or {}
            c_parts = c_data.get('participants', [])
            if any(a_id in c_parts for a_id in admin_uids):
                conv_doc_id = doc.id
                existing_participants = c_parts
                break

        all_participants = list(dict.fromkeys(existing_participants + [sender_uid] + admin_uids))
        if not conv_doc_id:
            new_conv = {
                'participants': all_participants,
                'title': name,
                'subject': subject,
                'avatarText': name[:2].upper() if name else 'GU',
                'lastMessage': message_text,
                'updatedAt': now,
                'unread': True,
                'senderName': name,
                'senderEmail': email,
                'userName': name,    
                'userEmail': email     
            }
            _, doc_ref = db.collection('Conversations').add(new_conv)
            conv_doc_id = doc_ref.id
        else:
            db.collection('Conversations').document(conv_doc_id).update({
                'participants': all_participants,
                'subject': subject,
                'lastMessage': message_text,
                'updatedAt': now,
                'unread': True,
                'senderName': name,
                'senderEmail': email,
                'userName': name,
                'userEmail': email
            })

        msg_payload = {
            'senderId': sender_uid,
            'senderName': name,
            'senderEmail': email,
            'subject': subject,
            'text': message_text,
            'timestamp': now
        }
        db.collection('Conversations').document(conv_doc_id)\
          .collection('Messages').add(msg_payload)

        log_audit_event(
            action="Contact Message",
            details=f"{name} sent a message to Admins regarding {subject} - {email}",
            user_id=sender_uid,
            user_name=name or "Guest User"
        )

        email_subject = f"New Contact Message: {subject} (from {name})"
        for admin_email in admin_emails:
            try:
                html_body = render_template(
                    'EmailNotification.html',
                    sender_name=name,
                    sender_email=email,
                    subject=subject,
                    message=message_text,
                    status='Admin Contact Message'
                )
            except Exception:
                html_body = (
                    f"<p><strong>From:</strong> {name} ({email})</p>"
                    f"<p><strong>Subject:</strong> {subject}</p>"
                    f"<p><strong>Message:</strong><br>{message_text}</p>"
                )
            send_email_async(admin_email, email_subject, html_body)

        return jsonify({
            "status": "success", 
            "message": "Your message has been sent to the Admin team!"
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500