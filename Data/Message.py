from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, Response
from functools import wraps
import html
import os
import requests
from itsdangerous import URLSafeTimedSerializer, BadSignature
import firebase_admin
from firebase_admin import firestore
from datetime import datetime, timedelta, timezone

message_bp = Blueprint('message_bp', __name__)

def get_db():
    return firestore.client()

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
    return url_for('message_bp.media_proxy', file_id=token, _external=True)

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

@message_bp.route('/messages/media-proxy/<path:file_id>')
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

# Messages
@message_bp.route('/api/conversations', methods=['GET'])
@auth_required
def get_conversations():
    uid = session['user']['uid']

    try:
        db = get_db()
        convs_ref = db.collection('Conversations').where('participants', 'array_contains', uid)
        docs = convs_ref.stream()

        admin_uids = set()
        for doc in db.collection('Users').where('role', 'in', ['Admin', 'admin']).stream():
            admin_uids.add(doc.id)

        user_doc_current = db.collection('Users').document(uid).get()
        is_current_user_admin = False
        if user_doc_current.exists:
            is_current_user_admin = str(user_doc_current.to_dict().get('role', '')).strip().lower() == 'admin'

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
            
            if is_current_user_admin:
                other_uid = next((p for p in participants if p not in admin_uids), None)
            else:
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
                        raw_photo = user_data.get('photoURL')
                        photo_url = get_file_url(raw_photo) if raw_photo else None

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
        admin_docs = db.collection('Users').where('role', '==', 'Admin').limit(1).get()
        admin_uid = None

        for doc in admin_docs:
            admin_uid = doc.id
            break
            
        if not admin_uid:
            admin_docs_lower = db.collection('Users').where('role', '==', 'admin').limit(1).get()
            for doc in admin_docs_lower:
                admin_uid = doc.id
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
                existing_conv['subject'] = existing_conv.get('subject', 'General Inquiry')
                existing_conv['photoURL'] = None
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
        new_conv['subject'] = 'General Inquiry'
        new_conv['photoURL'] = None
        
        return jsonify({"status": "success", "conversation": new_conv}), 201

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Fetch Messages
@message_bp.route('/api/conversations/<conversation_id>/messages', methods=['GET'])
@auth_required
def get_messages(conversation_id):
    uid = session['user']['uid']
    try:
        db = get_db()
        conv_doc = db.collection('Conversations').document(conversation_id).get()

        if not conv_doc.exists:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        conv_data = conv_doc.to_dict()
        participants = conv_data.get('participants', [])

        user_doc = db.collection('Users').document(uid).get()
        is_admin = False
        if user_doc.exists:
            is_admin = str(user_doc.to_dict().get('role', '')).strip().lower() == 'admin'

        if uid not in participants and not is_admin:
            return jsonify({"status": "error", "message": "Unauthorized access to conversation"}), 403

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
    raw_text = payload.get('text', '').strip()

    if not raw_text:
        return jsonify({"status": "error", "message": "Message body cannot be empty"}), 400

    if len(raw_text) > 500:
        return jsonify({"status": "error", "message": "Message cannot exceed 500 characters."}), 400

    text = html.escape(raw_text)
    try:
        db = get_db()
        conv_doc = db.collection('Conversations').document(conversation_id).get()
        if not conv_doc.exists:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        conv_data = conv_doc.to_dict()
        participants = conv_data.get('participants', [])
        user_doc = db.collection('Users').document(uid).get()
        is_admin = user_doc.exists and str(user_doc.to_dict().get('role', '')).strip().lower() == 'admin'

        if uid not in participants and not is_admin:
            return jsonify({"status": "error", "message": "Unauthorized"}), 403

        admin_uids = set()
        for doc in db.collection('Users').where('role', '==', 'Admin').stream():
            admin_uids.add(doc.id)
        for doc in db.collection('Users').where('role', '==', 'admin').stream():
            admin_uids.add(doc.id)

        updated_participants = list(set(participants).union(admin_uids))

        now = firestore.SERVER_TIMESTAMP
        msg_data = {
            'senderId': uid,
            'text': text,
            'timestamp': now
        }

        db.collection('Conversations').document(conversation_id)\
          .collection('Messages').add(msg_data)
        
        db.collection('Conversations').document(conversation_id).update({
            'participants': updated_participants,
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
    uid = session['user']['uid']
    try:
        db = get_db()
        conv_doc = db.collection('Conversations').document(conversation_id).get()

        if not conv_doc.exists:
            return jsonify({"status": "error", "message": "Conversation not found"}), 404

        conv_data = conv_doc.to_dict()
        participants = conv_data.get('participants', [])

        user_doc = db.collection('Users').document(uid).get()
        is_admin = False
        if user_doc.exists:
            is_admin = str(user_doc.to_dict().get('role', '')).strip().lower() == 'admin'

        if uid not in participants and not is_admin:
            return jsonify({"status": "error", "message": "Unauthorized access to conversation"}), 403

        db.collection('Conversations').document(conversation_id).update({
            'unread': False
        })
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@message_bp.app_context_processor
def inject_unread_status():
    if 'user' not in session:
        return dict(has_new_adoptions=False, has_unread_messages=False)

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