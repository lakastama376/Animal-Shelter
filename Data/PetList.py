from flask import Blueprint, render_template, request, jsonify, session
from firebase_admin import firestore

pet_public_bp = Blueprint('pet_public_bp', __name__)

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

            if not p_data.get('images'):
                p_data['images'] = ['https://via.placeholder.com/400x250']
                
            p_data['is_favorited'] = d.id in wishlist
            pets_list.append(p_data)
            
        return render_template('PetList.html', pets=pets_list, is_logged_in=is_logged_in)
        
    except Exception as e:
        print(f"Error fetching public pet listings: {e}")
        return render_template('PetList.html', pets=[], is_logged_in=False)