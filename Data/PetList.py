import os
from flask import Blueprint, render_template, request, jsonify, session, url_for, Response
from firebase_admin import firestore
from itsdangerous import URLSafeTimedSerializer, BadSignature
import requests

pet_public_bp = Blueprint('pet_public_bp', __name__)

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
    return url_for('pet_public_bp.public_media_proxy', file_id=token, _external=True)

@pet_public_bp.route('/explore/media-proxy/<path:file_id>')
def public_media_proxy(file_id):
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

# Viewing Pets
@pet_public_bp.route('/explore', methods=['GET'])
def pet_list():
    try:
        db = firestore.client()
        pets_ref = db.collection('Pets')
        docs = pets_ref.stream()
        user_data = session.get('user') or {}
        uid = user_data.get('uid') or user_data.get('user_id') or session.get('user_id') or session.get('uid')
        is_logged_in = True if uid or 'user' in session else False
        wishlist = []
        if is_logged_in and uid:
            user_ref = db.collection('Users').document(uid)
            user_doc = user_ref.get()
            if user_doc.exists:
                wishlist = user_doc.to_dict().get('wishlist', [])

        pets_list = []
        for d in docs:
            p_data = d.to_dict()
            p_data['id'] = d.id

            raw_images = p_data.get('images')
            if raw_images:
                p_data['images'] = [get_file_url(img) for img in raw_images if img]
            else:
                p_data['images'] = ['/static/images/placeholder.png']
                
            p_data['is_favorited'] = d.id in wishlist
            pets_list.append(p_data)
            
        return render_template('PetList.html', pets=pets_list, is_logged_in=is_logged_in)
        
    except Exception as e:
        print(f"Error fetching public pet listings: {e}")
        return render_template('PetList.html', pets=[], is_logged_in=False)