from datetime import datetime
from flask import Blueprint, render_template, session, abort
from firebase_admin import firestore

pet_profile_bp = Blueprint('pet_profile_bp', __name__)

@pet_profile_bp.route('/profile/<id>', methods=['GET'])
def pet_profile(id):
    try:
        db = firestore.client()
        doc = db.collection('Pets').document(id).get()
        if not doc.exists:
            abort(404)
            
        pet_data = doc.to_dict()
        pet_data['id'] = doc.id
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