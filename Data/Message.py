from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import firebase_admin
from firebase_admin import firestore
from datetime import datetime, timedelta, timezone

message_bp = Blueprint('message_bp', __name__)

def get_db():
    return firestore.client()

def auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def format_timestamp(ts):
    if not ts:
        return None
    try:
        parsed_dt = None
        if hasattr(ts, 'datetime'):
            parsed_dt = ts.datetime()
        elif hasattr(ts, 'strftime'):
            parsed_dt = ts
        elif isinstance(ts, str):
            parsed_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        
        if parsed_dt:
            if parsed_dt.tzinfo is not None:
                ph_tz = timezone(timedelta(hours=8))
                parsed_dt = parsed_dt.astimezone(ph_tz)
            return parsed_dt
    except Exception:
        pass
    return None

@message_bp.route('/messages')
@auth_required
def message_page():
    uid = session['user']['uid']
    user_role = 'user'
    
    try:
        db = get_db()
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            user_role = str(user_doc.to_dict().get('role', '')).strip().lower()
    except Exception as e:
        print(f"Error fetching user role: {e}")

    return render_template('Message.html', user_role=user_role)

# Messages
@message_bp.route('/api/conversations', methods=['GET'])
@auth_required
def get_conversations():
    uid = session['user']['uid']

    try:
        db = get_db()
        convs_ref = db.collection('Conversations').where('participants', 'array_contains', uid)
        docs = convs_ref.stream()
        
        conversations = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            
            updated_at = data.get('updatedAt')
            parsed_dt = format_timestamp(updated_at)
            if parsed_dt:
                data['formattedDate'] = parsed_dt.strftime('%b %d, %Y')
                data['sort_date'] = parsed_dt
            else:
                data['formattedDate'] = ''
                data['sort_date'] = datetime.min

            last_sender = data.get('lastSenderId')
            if last_sender == uid:
                data['unread'] = False

            participants = data.get('participants', [])
            other_uid = next((p for p in participants if p != uid), None)
            display_name = data.get('senderName') or data.get('userName') or data.get('title') or 'Guest'
            photo_url = None
            
            if other_uid:
                user_doc = db.collection('Users').document(other_uid).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                    user_role = str(user_data.get('role', '')).strip().lower()

                    if user_role == 'admin':
                        display_name = 'Tails of Freedom Admin'
                        photo_url = None   
                    else:
                        display_name = (
                            user_data.get('fullName') 
                            or f"{user_data.get('firstName', '')} {user_data.get('lastName', '')}".strip() 
                            or 'User'
                        )
                        photo_url = user_data.get('photoURL')

            data['displayName'] = display_name
            data['photoURL'] = photo_url
            conversations.append(data)

        conversations.sort(key=lambda x: x.get('sort_date', datetime.min), reverse=True)
        for c in conversations:
            c.pop('sort_date', None)

        return jsonify({"status": "success", "conversations": conversations}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Direct Conversation with Admin
@message_bp.route('/api/conversations/admin', methods=['POST'])
@auth_required
def admin_conversation():
    uid = session['user']['uid']
    
    try:
        db = get_db()
        users_ref = db.collection('Users').stream()
        admin_uid = None

        for user_doc in users_ref:
            u_data = user_doc.to_dict()
            role = str(u_data.get('role', '')).strip().lower()
            if role == 'admin':
                admin_uid = user_doc.id
                break 
            
        if not admin_uid:
            return jsonify({"status": "error", "message": "No system administrator found."}), 404

        query = db.collection('Conversations')\
                  .where('participants', 'array_contains', uid)\
                  .stream()
                  
        existing_conv = None
        for doc in query:
            data = doc.to_dict()
            if admin_uid in data.get('participants', []):
                existing_conv = data
                existing_conv['id'] = doc.id
                existing_conv['displayName'] = 'Tails of Freedom Admin'
                break

        if existing_conv:
            return jsonify({"status": "success", "conversation": existing_conv}), 200
        
        now = firestore.SERVER_TIMESTAMP
        new_conv = {
            'participants': [uid, admin_uid], 
            'title': 'Tails of Freedom Admin',
            'subject': 'General Inquiry',
            'lastMessage': '',
            'updatedAt': now,
            'unread': False
        }
        doc_ref = db.collection('Conversations').add(new_conv)
        new_conv['id'] = doc_ref[1].id
        new_conv['displayName'] = 'Tails of Freedom Admin'
        return jsonify({"status": "success", "conversation": new_conv}), 201

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Fetch Messages
@message_bp.route('/api/conversations/<conversation_id>/messages', methods=['GET'])
@auth_required
def get_messages(conversation_id):
    try:
        db = get_db()
        msgs_ref = db.collection('Conversations').document(conversation_id)\
                     .collection('Messages').order_by('timestamp', direction=firestore.Query.ASCENDING)
        
        messages = []
        for doc in msgs_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            
            timestamp = data.get('timestamp')
            parsed_dt = format_timestamp(timestamp)
            if parsed_dt:
                data['formattedTime'] = parsed_dt.strftime('%b %d, %Y %I:%M %p')
            else:
                data['formattedTime'] = ''
                
            messages.append(data)
            
        return jsonify({"status": "success", "messages": messages}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Send Message
@message_bp.route('/api/conversations/<conversation_id>/send', methods=['POST'])
@auth_required
def send_message(conversation_id):
    uid = session['user']['uid']
    payload = request.get_json() or {}
    text = payload.get('text', '').strip()
    
    if not text:
        return jsonify({"status": "error", "message": "Message body cannot be empty"}), 400
        
    try:
        db = get_db()
        now = firestore.SERVER_TIMESTAMP
        msg_data = {
            'senderId': uid,
            'text': text,
            'timestamp': now
        }
    
        db.collection('Conversations').document(conversation_id)\
          .collection('Messages').add(msg_data)
        db.collection('Conversations').document(conversation_id).update({
            'lastMessage': text,
            'lastSenderId': uid,
            'updatedAt': now,
            'unread': True
        })
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Notification
@message_bp.route('/api/conversations/<conversation_id>/read', methods=['POST'])
@auth_required
def mark_as_read(conversation_id):
    try:
        db = get_db()
        db.collection('Conversations').document(conversation_id).update({
            'unread': False
        })
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@message_bp.app_context_processor
def inject_unread_status():
    if 'user' not in session:
        return dict(has_unread_messages=False)
        
    uid = session['user']['uid']
    try:
        db = get_db()
        query = db.collection('Conversations')\
                  .where('participants', 'array_contains', uid)\
                  .where('unread', '==', True)\
                  .stream()
        has_unread = any(doc.to_dict().get('lastSenderId') != uid for doc in query)
        return dict(has_unread_messages=has_unread)
    except Exception:
        return dict(has_unread_messages=False)