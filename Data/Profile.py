from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, Response
from firebase_admin import firestore
from functools import wraps
import uuid
import os
import requests
from itsdangerous import URLSafeTimedSerializer, BadSignature
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

profile_bp = Blueprint('profile_bp', __name__)

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
    return url_for('profile_bp.media_proxy', file_id=token, _external=True)

def get_storage():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID'))
    client.set_key(os.getenv('APPWRITE_API_KEY'))
    return Storage(client)

def auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        else:
            return f(*args, **kwargs)
    return decorated_function

def get_user_full_name(db, uid, user_session):
    user_name = user_session.get('name') or user_session.get('fullName') or user_session.get('email', 'User')
    if uid and uid != "Unknown":
        try:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                u_data = user_doc.to_dict()
                user_name = u_data.get('fullName') or f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or user_name
        except Exception:
            pass
    return user_name

@profile_bp.route('/profile', methods=['GET'])
@auth_required
def profile():
    db = firestore.client()
    user_session = session.get('user', {})
    uid = user_session.get('uid') or user_session.get('user_id')
    user_info = {}
    if uid:
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            user_info = user_doc.to_dict()
            raw_photo = user_info.get('photoURL', '')
            user_info['photoURL'] = get_file_url(raw_photo) if raw_photo else ''
            
            if not user_info.get('username') and user_info.get('email'):
                user_info['username'] = user_info.get('email').split('@')[0]
            if not user_info.get('role'):
                user_info['role'] = 'Adopter'

    return render_template('Profile.html', user_profile=user_info)

@profile_bp.route('/profile/media-proxy/<path:file_id>')
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

# Uploading Photo
@profile_bp.route('/profile/upload_photo', methods=['POST'])
@auth_required
def upload_photo():
    try:
        db = firestore.client()
        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID')
        user_session = session.get('user', {})
        uid = user_session.get('uid') or user_session.get('user_id')
        
        if not uid:
            return jsonify({'success': False, 'error': 'User session not found.'}), 401

        if 'profilePhoto' not in request.files:
            return jsonify({'success': False, 'error': 'No file part in the request.'}), 400

        file = request.files['profilePhoto']
        if file and file.filename != '':
            file_bytes = file.read()
            filename = f"profile_{uid}_{uuid.uuid4().hex[:8]}_{file.filename}"
            
            result = storage.create_file(
                bucket_id=bucket_id,
                file_id='unique()',
                file=InputFile.from_bytes(file_bytes, filename)
            )
            
            file_id = result.id
            user_ref = db.collection('Users').document(uid)
            user_ref.update({'photoURL': file_id})
            
            resolved_photo_url = get_file_url(file_id)
            user_name = get_user_full_name(db, uid, user_session)
            log_audit_event(
                action="Updated Profile",
                details=f"{user_name} uploaded and updated their profile picture.",
                user_id=uid,
                user_name=user_name
            )

            return jsonify({'success': True, 'photoURL': resolved_photo_url})
        
        return jsonify({'success': False, 'error': 'Empty file submission.'}), 400
    except Exception as e:
        print(f"Error auto-saving profile picture: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Updating Profile
@profile_bp.route('/profile/update', methods=['POST'])
@auth_required
def update_profile():
    try:
        db = firestore.client()
        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID')
        user_session = session.get('user', {})
        uid = user_session.get('uid') or user_session.get('user_id')
        
        if not uid:
            flash('User session not found.', 'error')
            return redirect(url_for('login'))

        first_name = request.form.get('firstName', '').strip()
        last_name = request.form.get('lastName', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        bio = request.form.get('bio', '').strip()

        update_payload = {
            'firstName': first_name,
            'lastName': last_name,
            'fullName': f"{first_name} {last_name}".strip(),
            'phone': phone,
            'address': address,
            'bio': bio
        }

        if 'profilePhoto' in request.files:
            file = request.files['profilePhoto']
            
            if file and file.filename != '':
                file_bytes = file.read()
                filename = f"profile_{uid}_{uuid.uuid4().hex[:8]}_{file.filename}"
                
                result = storage.create_file(
                    bucket_id=bucket_id,
                    file_id='unique()',
                    file=InputFile.from_bytes(file_bytes, filename)
                )
                update_payload['photoURL'] = result.id

        user_ref = db.collection('Users').document(uid)
        user_ref.update(update_payload)

        user_name = get_user_full_name(db, uid, user_session)
        log_audit_event(
            action="Updated Profile",
            details=f"{user_name} updated their profile details.",
            user_id=uid,
            user_name=user_name
        )

        flash('Profile updated successfully!', 'success')
        
    except Exception as e:
        print(f"Error updating profile: {e}")
        flash('An error occurred while updating your profile.', 'error')

    return redirect(url_for('profile_bp.profile'))