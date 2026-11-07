import os
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, BadSignature
from flask import Blueprint, render_template, session, abort, url_for
from firebase_admin import firestore

pet_profile_bp = Blueprint('pet_profile_bp', __name__)

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

@pet_profile_bp.route('/profile/<id>', methods=['GET'])
def pet_profile(id):
    try:
        db = firestore.client()
        doc = db.collection('Pets').document(id).get()
        if not doc.exists:
            abort(404)
            
        pet_data = doc.to_dict()
        pet_data['id'] = doc.id

        if pet_data.get('images'):
            pet_data['images'] = [get_file_url(img) for img in pet_data['images'] if img]
        if pet_data.get('storyVideos'):
            pet_data['storyVideos'] = [get_file_url(vid) for vid in pet_data['storyVideos'] if vid]
        if pet_data.get('videos'):
            pet_data['videos'] = [get_file_url(vid) for vid in pet_data['videos'] if vid]

        raw_date = pet_data.get('rescueDate')
        if raw_date:
            try:
                parsed_date = datetime.strptime(raw_date, '%Y-%m-%d')
                pet_data['rescueDate'] = parsed_date.strftime('%B %d, %Y')
            except ValueError:
                pass

        user_profile = None
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')
        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                user_profile = user_doc.to_dict()

        current_type = pet_data.get('type', '')
        current_traits = set([t.lower() for t in pet_data.get('personality', []) if t])
        current_compat = set([c.lower() for c in pet_data.get('compatibility', []) if c])
        current_tags = current_traits.union(current_compat)
        
        similar_pets = []
        all_same_type_docs = db.collection('Pets') \
            .where('type', '==', current_type) \
            .where('status', '==', 'Available') \
            .get()
        
        for p_doc in all_same_type_docs:
            if p_doc.id == id:
                continue 
                
            other_data = p_doc.to_dict()
            other_data['id'] = p_doc.id

            if other_data.get('images'):
                other_data['images'] = [get_file_url(img) for img in other_data['images'] if img]

            other_traits = set([t.lower() for t in other_data.get('personality', []) if t])
            other_compat = set([c.lower() for c in other_data.get('compatibility', []) if c])
            other_tags = other_traits.union(other_compat)
            
            if current_tags or other_tags:
                intersection = current_tags.intersection(other_tags)
                union = current_tags.union(other_tags)
                match_percentage = int((len(intersection) / len(union)) * 100) if union else 0
            else:
                match_percentage = 0

            other_data['match_percentage'] = match_percentage
            similar_pets.append(other_data)
            
        similar_pets = sorted(similar_pets, key=lambda x: x['match_percentage'], reverse=True)[:3]
        return render_template('PetProfile.html', pet=pet_data, similar_pets=similar_pets, user_profile=user_profile)
        
    except Exception as e:
        print(f"Error fetching individual profile dynamic components: {e}")
        abort(500)