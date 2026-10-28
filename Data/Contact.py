from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from firebase_admin import firestore
from functools import wraps

from Data.audit import log_audit_event

contact_bp = Blueprint('contact_bp', __name__)

def get_db():
    return firestore.client()

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

# Direct Message with Admin
@contact_bp.route('/api/contact/send', methods=['POST'])
def send_contact_message():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    subject = data.get('subject', '').strip()
    message_text = data.get('message', '').strip()

    if not name or not email or not subject or not message_text:
        return jsonify({"status": "error", "message": "Please fill out all required fields."}), 400

    admin_uid = "dA0yV8QvfXQKTHwIPAEq96LH0rC2"
    db = get_db()
    now = firestore.SERVER_TIMESTAMP

    if 'user' in session:
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
        for doc in conv_query:
            c_data = doc.to_dict()
            if admin_uid in c_data.get('participants', []):
                conv_doc_id = doc.id
                break

        if not conv_doc_id:
            new_conv = {
                'participants': [sender_uid, admin_uid],
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
            details=f"{name} sent a message to Admin regarding {subject} - {email}",
            user_id=sender_uid,
            user_name=name or "Guest User"
        )

        return jsonify({
            "status": "success", 
            "message": "Your message has been sent directly to the Admin!"
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500