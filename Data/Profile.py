from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from firebase_admin import firestore
from functools import wraps
import uuid
import os
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

profile_bp = Blueprint('profile_bp', __name__)

def get_storage():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT', 'https://sgp.cloud.appwrite.io/v1'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63'))
    client.set_key(os.getenv('APPWRITE_API_KEY', 'standard_36d0a70b8164a32ef4e013f4137f1b37511b2d99c1064cf9fa4e8feeea1bb8f49a8ddb42ec5fcf8a33eeca64ca1cf77dc1e5537a5ca396882cd97214978482fe74adae5c0614b6d9a4c866106549d484fff2b3767b1ea9d5383ae518a91c3321cfc79a78f540bda830c6a0e457075f78446e3884ce157976fe18e4335536672f'))
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
    return render_template('Profile.html')

# Uploading Photo
@profile_bp.route('/profile/upload_photo', methods=['POST'])
@auth_required
def upload_photo():
    try:
        db = firestore.client()
        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645')
        project_id = os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63')
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
            photo_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{result.id}/view?project={project_id}"

            user_ref = db.collection('Users').document(uid)
            user_ref.update({'photoURL': photo_url})
            user_name = get_user_full_name(db, uid, user_session)
            log_audit_event(
                action="Updated Profile",
                details=f"{user_name} uploaded and updated their profile picture.",
                user_id=uid,
                user_name=user_name
            )

            return jsonify({'success': True, 'photoURL': photo_url})
        
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
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645')
        project_id = os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63')
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
                update_payload['photoURL'] = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{result.id}/view?project={project_id}"

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