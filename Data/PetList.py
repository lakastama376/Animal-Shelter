import os
from flask import Blueprint, render_template, request, jsonify, session, url_for
from firebase_admin import firestore
from itsdangerous import URLSafeTimedSerializer, BadSignature

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
    return url_for('adoption_management_bp.media_proxy', file_id=token, _external=True)

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
                p_data['images'] = ['https://via.placeholder.com/400x250']
                
            p_data['is_favorited'] = d.id in wishlist
            pets_list.append(p_data)
            
        return render_template('PetList.html', pets=pets_list, is_logged_in=is_logged_in)
        
    except Exception as e:
        print(f"Error fetching public pet listings: {e}")
        return render_template('PetList.html', pets=[], is_logged_in=False)