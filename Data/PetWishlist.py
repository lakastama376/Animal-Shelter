from flask import Blueprint, render_template, session, jsonify, redirect, url_for
from firebase_admin import firestore
from functools import wraps
import datetime
from Data.audit import log_audit_event

wishlist_bp = Blueprint('wishlist_bp', __name__)

def auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@wishlist_bp.route('/wishlist')
@auth_required
def view_wishlist():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    wishlist_items = []
    
    try:
        user_ref = db.collection('Users').document(uid)
        user_doc = user_ref.get()
        
        if user_doc.exists:
            profile = user_doc.to_dict()
            wishlist_ids = profile.get('wishlist', [])
            
            for pet_id in wishlist_ids:
                pet_ref = db.collection('Pets').document(pet_id)
                pet_doc = pet_ref.get()
                
                if pet_doc.exists:
                    pet_data = pet_doc.to_dict()
                    pet_data['id'] = pet_doc.id  
                    wishlist_items.append({
                        'pet': pet_data,
                        'date_added': datetime.date.today() 
                    })
                    
    except Exception as e:
        print(f"Error gathering wishlist records: {e}")

    return render_template('PetWishlist.html', wishlist_items=wishlist_items)

# Adding Pet to Wishlist
@wishlist_bp.route('/wishlist/add/<string:pet_id>', methods=['POST'])
@auth_required
def add_to_wishlist(pet_id):
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    
    if not uid:
        return jsonify({"success": False, "message": "User ID missing from session session data."}), 400

    try:
        user_ref = db.collection('Users').document(uid)
        pet_doc = db.collection('Pets').document(pet_id).get()
        
        if not pet_doc.exists:
            return jsonify({"success": False, "message": "Pet records do not exist."}), 404

        pet_info = pet_doc.to_dict() or {}
        pet_name = pet_info.get('petName') or pet_info.get('name') or "a pet"
        user_doc = user_ref.get()
        user_info = user_doc.to_dict() if user_doc.exists else {}
        full_name = user_info.get('fullName') or f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip() or "User"

        user_ref.update({
            'wishlist': firestore.ArrayUnion([pet_id])
        })

        log_audit_event(
            action="Wishlist Updated",
            details=f"{full_name} added {pet_name} to their wishlist",
            user_id=uid,
            user_name=full_name
        )
        
        return jsonify({"success": True, "message": "Pet successfully added to wishlist"}), 200
    except Exception as e:
        print(f"Error adding item to wishlist: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# Remove
@wishlist_bp.route('/wishlist/remove/<string:pet_id>', methods=['POST'])
@auth_required
def remove_from_wishlist(pet_id):
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id') 

    if not uid:
        return jsonify({"success": False, "message": "User ID missing from session session data."}), 400
        
    try:
        user_ref = db.collection('Users').document(uid)
        pet_doc = db.collection('Pets').document(pet_id).get()
        pet_info = pet_doc.to_dict() if pet_doc.exists else {}
        pet_name = pet_info.get('petName') or pet_info.get('name') or "a pet"
        user_doc = user_ref.get()
        user_info = user_doc.to_dict() if user_doc.exists else {}
        full_name = user_info.get('fullName') or f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip() or "User"

        user_ref.update({
            'wishlist': firestore.ArrayRemove([pet_id])
        })

        log_audit_event(
            action="Wishlist Updated",
            details=f"{full_name} removed {pet_name} from their wishlist",
            user_id=uid,
            user_name=full_name
        )
        
        return jsonify({"success": True, "message": "Pet removed from wishlist"}), 200
    except Exception as e:
        print(f"Error removing item from wishlist: {e}")
        return jsonify({"success": False, "message": str(e)}), 500