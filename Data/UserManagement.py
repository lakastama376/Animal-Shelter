from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, abort, Response
from functools import wraps
import os
import requests
import firebase_admin
from firebase_admin import firestore, auth
from itsdangerous import URLSafeTimedSerializer, BadSignature
from Data.audit import log_audit_event

user_management_bp = Blueprint('user_management_bp', __name__)

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
    return url_for('user_management_bp.media_proxy', file_id=token, _external=True)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        
        uid = session['user'].get('uid')
        if not uid:
            abort(403)
            
        db = firestore.client()
        user_doc = db.collection('Users').document(uid).get()
        
        if not user_doc.exists or user_doc.to_dict().get('role') != 'Admin':
            return jsonify({"status": "error", "message": "Access denied. Admin privileges required."}), 403
            
        return f(*args, **kwargs)
    return decorated_function

def get_admin_info():
    db = firestore.client()
    uid = session['user'].get('uid') or session['user'].get('user_id') or "Unknown"
    admin_name = "Admin"
    if uid != "Unknown":
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict()
            admin_name = u_data.get('fullName') or u_data.get('name') or f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or admin_name
    return uid, admin_name

@user_management_bp.route('/user-management')
@admin_required
def view_users():
    return render_template('UserManagement.html')

@user_management_bp.route('/api/users', methods=['GET'])
@admin_required
def get_users():
    try:
        db = firestore.client()
        users_ref = db.collection('Users').stream()
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)
        
        users = []
        for doc in users_ref:
            data = doc.to_dict()
            phone = data.get('phone') or data.get('phoneNumber') or data.get('contactNumber') or 'N/A'
            raw_status = data.get('status', 'active')
            last_login = data.get('lastLogin') or data.get('lastSeen')
            joined_at_raw = data.get('joinedAt')

            user_role = data.get('role', 'Adopter')
            if user_role.lower() == 'admin':
                continue

            status = raw_status
            if raw_status != 'blocked' and last_login:
                if hasattr(last_login, 'to_datetime'):
                    last_login_dt = last_login.to_datetime()
                elif isinstance(last_login, datetime):
                    last_login_dt = last_login
                elif isinstance(last_login, str):
                    try:
                        last_login_dt = datetime.fromisoformat(last_login.replace('Z', '+00:00'))
                    except ValueError:
                        last_login_dt = None
                else:
                    last_login_dt = None

                if last_login_dt:
                    if last_login_dt < thirty_days_ago:
                        status = 'inactive'
                        if raw_status != 'inactive':
                            db.collection('Users').document(doc.id).update({'status': 'inactive'})
                    else:
                        if raw_status == 'inactive':
                            status = 'active'
                            db.collection('Users').document(doc.id).update({'status': 'active'})

            joined_at_str = 'N/A'
            if joined_at_raw:
                if hasattr(joined_at_raw, 'strftime'):
                    joined_at_str = joined_at_raw.strftime('%B %d, %Y')
                elif hasattr(joined_at_raw, 'to_datetime'):
                    joined_at_str = joined_at_raw.to_datetime().strftime('%B %d, %Y')

            raw_photo = data.get('photoURL', '')
            resolved_photo = get_file_url(raw_photo) if raw_photo else ''

            users.append({
                'uid': doc.id,
                'fullName': data.get('fullName') or f"{data.get('firstName', '')} {data.get('lastName', '')}".strip() or 'N/A',
                'email': data.get('email', 'N/A'),
                'phone': phone,
                'address': data.get('address', 'N/A'),
                'role': data.get('role', 'Adopter'),
                'status': status,
                'photoURL': resolved_photo,
                'bio': data.get('bio', ''),
                'joinedAt': joined_at_str,
                'adoptionCount': data.get('adoptionCount', 0),
                'rescueReportsSubmitted': data.get('rescueReportsSubmitted', 0)
            })
            
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@user_management_bp.route('/user-management/media-proxy/<path:file_id>')
@admin_required
def media_proxy(file_id):
    try:
        real_file_id = deobfuscate_file_id(file_id)
        if not real_file_id:
            real_file_id = file_id

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

# Status
@user_management_bp.route('/api/users/<uid>/role', methods=['PUT'])
@admin_required
def update_user_role(uid):
    try:
        db = firestore.client()
        data = request.json or {}
        new_role = data.get('role')
        new_status = data.get('status')

        if not new_role:
            return jsonify({"status": "error", "message": "Role is required."}), 400

        target_doc = db.collection('Users').document(uid).get()
        target_name = "Unknown User"
        if target_doc.exists:
            t_data = target_doc.to_dict()
            target_name = t_data.get('fullName') or f"{t_data.get('firstName', '')} {t_data.get('lastName', '')}".strip() or target_name

        update_data = {'role': new_role}
        if new_status:
            update_data['status'] = new_status

            try:
                auth.update_user(uid, disabled=(new_status == 'blocked'))
            except auth.UserNotFoundError:
                pass

        db.collection('Users').document(uid).update(update_data)

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated User Details",
            details=f"Admin {admin_name} updated user {target_name} role to '{new_role}'" + (f" and status to '{new_status}'" if new_status else ""),
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "User updated successfully."}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# Blocking a User
@user_management_bp.route('/api/users/<uid>/block', methods=['PATCH'])
@admin_required
def block_user(uid):
    try:
        current_admin_uid = session['user'].get('uid')
        if uid == current_admin_uid:
            return jsonify({"status": "error", "message": "You cannot block your own admin account."}), 400

        data = request.json or {}
        should_block = data.get('block', True)
        new_status = 'blocked' if should_block else 'active'

        db = firestore.client()
        target_doc = db.collection('Users').document(uid).get()
        target_name = "Unknown User"
        if target_doc.exists:
            t_data = target_doc.to_dict()
            target_name = t_data.get('fullName') or f"{t_data.get('firstName', '')} {t_data.get('lastName', '')}".strip() or target_name

        db.collection('Users').document(uid).update({'status': new_status})
        try:
            auth.update_user(uid, disabled=should_block)
        except auth.UserNotFoundError:
            pass

        admin_uid, admin_name = get_admin_info()
        action_label = "Blocked User" if should_block else "Unblocked User"
        log_audit_event(
            action=action_label,
            details=f"Admin {admin_name} {'blocked' if should_block else 'unblocked'} user {target_name}",
            user_id=admin_uid,
            user_name=admin_name
        )

        msg = "User successfully blocked." if should_block else "User successfully unblocked."
        return jsonify({"status": "success", "message": msg}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Delete
@user_management_bp.route('/api/users/<uid>', methods=['DELETE'])
@admin_required
def delete_user(uid):
    try:
        current_admin_uid = session['user'].get('uid')
        if uid == current_admin_uid:
            return jsonify({"status": "error", "message": "You cannot delete your own admin account."}), 400

        db = firestore.client()
        target_doc = db.collection('Users').document(uid).get()
        target_name = "Unknown User"
        if target_doc.exists:
            t_data = target_doc.to_dict()
            target_name = t_data.get('fullName') or f"{t_data.get('firstName', '')} {t_data.get('lastName', '')}".strip() or target_name

        db.collection('Users').document(uid).delete()

        try:
            auth.delete_user(uid)
        except auth.UserNotFoundError:
            pass 

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Deleted User",
            details=f"Admin {admin_name} deleted user {target_name} from the system",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "User deleted successfully."}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Message directly to the User
@user_management_bp.route('/api/conversations/user/<target_uid>', methods=['POST'])
@admin_required
def admin_start_user_conversation(target_uid):
    admin_uid = session['user'].get('uid')
    if not admin_uid:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    try:
        db = firestore.client()
        query = db.collection('Conversations')\
                  .where('participants', 'array_contains', admin_uid)\
                  .stream()

        existing_conv = None
        for doc in query:
            data = doc.to_dict()
            participants = data.get('participants', [])
            if target_uid in participants:
                existing_conv = data
                existing_conv['id'] = doc.id
   
                if 'updatedAt' in existing_conv and hasattr(existing_conv['updatedAt'], 'isoformat'):
                    existing_conv['updatedAt'] = existing_conv['updatedAt'].isoformat()
                
                break

        if existing_conv:
            return jsonify({"status": "success", "conversation": existing_conv}), 200

        target_doc = db.collection('Users').document(target_uid).get()
        target_name = "User"
        if target_doc.exists:
            user_data = target_doc.to_dict()
            target_name = user_data.get('fullName') or f"{user_data.get('firstName', '')} {user_data.get('lastName', '')}".strip() or "User"

        now_utc = datetime.now(timezone.utc)

        new_conv = {
            'participants': [admin_uid, target_uid],
            'title': target_name,
            'subject': 'Direct Message',
            'avatarText': "".join([n[0].upper() for n in target_name.split() if n])[:2] if target_name else 'U',
            'lastMessage': '',
            'updatedAt': firestore.SERVER_TIMESTAMP,
            'unread': False
        }

        doc_ref = db.collection('Conversations').add(new_conv)
        new_conv['updatedAt'] = now_utc.isoformat()
        new_conv['id'] = doc_ref[1].id

        return jsonify({"status": "success", "conversation": new_conv}), 201

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500