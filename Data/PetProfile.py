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

def get_active_adoption_block(db, uid):
    if not uid:
        return False, "", ""

    user_apps = db.collection('Applications').where('userId', '==', uid).stream()
    for doc in user_apps:
        app = doc.to_dict() or {}
        status = str(app.get('status') or '').strip()
        mon_status = str(app.get('monitoringStatus') or app.get('monitoring_status') or '').strip()

        if status in ['Returned', 'Rejected']:
            continue

        if status == 'Adopted' and mon_status.lower() in ['finalized', 'completed']:
            continue

        pet_name = app.get('petName') or 'a pet'
        if status == 'Adopted':
            detail = f"ongoing post-adoption monitoring for {pet_name}"
        else:
            detail = f"an active adoption application ({status or 'In Progress'}) for {pet_name}"

        return True, pet_name, detail

    return False, "", ""

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

        raw_deceased_date = pet_data.get('deceasedDate')
        if raw_deceased_date:
            try:
                parsed_deceased_date = datetime.strptime(raw_deceased_date, '%Y-%m-%d')
                pet_data['deceasedDate'] = parsed_deceased_date.strftime('%B %d, %Y')
            except ValueError:
                pass

        user_profile = None
        user_application_status = None
        has_active_adoption = False
        active_adoption_message = ""
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')

        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                user_profile = user_doc.to_dict()

            existing_apps = list(
                db.collection('Applications')
                .where('userId', '==', uid)
                .where('petId', '==', id)
                .stream()
            )
            
            priority_statuses = ['Approved', 'Visit Scheduled', 'Adopted', 'Under Review', 'Submitted']
            found_statuses = [app.to_dict().get('status') for app in existing_apps if app.to_dict().get('status')]
            for st in priority_statuses:
                if st in found_statuses:
                    user_application_status = st
                    break
            if not user_application_status and found_statuses:
                user_application_status = found_statuses[0]

            has_active_adoption, _, active_detail = get_active_adoption_block(db, uid)
            if has_active_adoption:
                active_adoption_message = f"You cannot apply for another pet while you have {active_detail} until monitoring is completed."

        raw_pet_status = (pet_data.get('status') or '').strip()
        if raw_pet_status.lower() == 'deceased':
            display_status = 'Deceased'
        elif user_application_status in ['Approved', 'Visit Scheduled'] or raw_pet_status.lower() == 'reserved':
            display_status = 'Reserved'
        else:
            display_status = raw_pet_status or 'Available'

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
        return render_template(
            'PetProfile.html',
            pet=pet_data,
            similar_pets=similar_pets,
            user_profile=user_profile,
            user_application_status=user_application_status,
            display_status=display_status,
            has_active_adoption=has_active_adoption,
            active_adoption_message=active_adoption_message
        )
        
    except Exception as e:
        print(f"Error fetching individual profile dynamic components: {e}")
        abort(500)