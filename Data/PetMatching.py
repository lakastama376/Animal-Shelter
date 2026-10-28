from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from firebase_admin import firestore
import random
from Data.audit import log_audit_event

pet_matching_bp = Blueprint('pet_matching_bp', __name__)

@pet_matching_bp.route('/matching', methods=['GET'])
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    is_eligible = session.get('is_eligible', False)
    return render_template('PetMatching.html', is_eligible=is_eligible)

# Pet Matching Questions
@pet_matching_bp.route('/matching/questions', methods=['GET'])
def get_matching_questions():
    try:
        db = firestore.client()
        docs = db.collection('PetMatchingQuestions').order_by('createdAt').stream()
        
        questions = []
        for doc in docs:
            q_data = doc.to_dict()
            q_data['id'] = doc.id
            questions.append(q_data)
            
        return jsonify({"success": True, "questions": questions}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# Calculating Matching Pets
@pet_matching_bp.route('/matching/calculate', methods=['POST'])
def calculate_matches():
    try:
        if 'user' not in session:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
            
        preferences = request.json.get('preferences', {})
        db = firestore.client()
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')
        user_wishlist = []
        
        user_name = user_data.get('name') or user_data.get('fullName') or user_data.get('email') or "User"
        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                u_dict = user_doc.to_dict()
                user_wishlist = u_dict.get('wishlist', [])
                user_name = u_dict.get('fullName') or u_dict.get('name') or f"{u_dict.get('firstName', '')} {u_dict.get('lastName', '')}".strip() or user_name
 
        pets_ref = db.collection('Pets').where('status', '==', 'Available')
        pets_docs = pets_ref.stream()
        
        matched_pets = []   
        for doc in pets_docs:
            pet = doc.to_dict()
            pet_id = doc.id
            pet['id'] = pet_id
            pet['is_favorited'] = pet_id in user_wishlist
            
            score = 100
            total_rules = 0
            
            pref_type = preferences.get('Choose your preferred companion?')
            if pref_type and pref_type != 'Either':
                total_rules += 1
                if pet.get('type', '').lower() != pref_type.lower():
                    continue  
            
            pref_age = preferences.get('What age feels right?')
            if pref_age and pref_age != 'Any Age':
                total_rules += 1
                pet_age_str = pet.get('age', '').lower()
                if 'year' in pet_age_str or 'month' in pet_age_str:
                    is_match = False
                    if 'under 1 year' in pref_age and 'month' in pet_age_str:
                        is_match = True
                    elif '1-3 years' in pref_age and any(x in pet_age_str for x in ['1 year', '2 years', '3 years']):
                        is_match = True
                    elif '3-8 years' in pref_age and any(str(i) in pet_age_str for i in range(3, 9)):
                        is_match = True
                    elif '8+ years' in pref_age and ('8' in pet_age_str or '9' in pet_age_str or '10' in pet_age_str):
                        is_match = True
                        
                    if not is_match:
                        score -= 30

            pref_activity = preferences.get('How active is your lifestyle?')
            if pref_activity:
                total_rules += 1
                pet_activity = pet.get('activityLevel', 'Medium')
                if pref_activity != pet_activity:
                    score -= 30

            pref_household = preferences.get("Who's in your household?")
            if pref_household:
                total_rules += 1
                compatibility_list = [c.lower() for c in pet.get('compatibility', [])]
                personality_list = [c.lower() for c in pet.get('personality', [])]
                if 'living solo' in pref_household.lower() and ('calm' not in personality_list and 'chill' not in personality_list):
                    score -= 20
                if 'couple' in pref_household.lower() and ('friendly' not in compatibility_list and 'people' not in compatibility_list):
                    score -= 20
                if 'family with kids' in pref_household.lower() and ('family' not in compatibility_list and 'kid' not in compatibility_list):
                    score -= 20
                if 'roommates' in pref_household.lower() and ('friendly' not in compatibility_list and 'people' not in compatibility_list):
                    score -= 20

            pref_experience = preferences.get('Your pet experience?')
            if pref_experience:
                total_rules += 1
                personality_list = [p.lower() for p in pet.get('personality', [])]
                beginner_friendly_traits = ['friendly', 'gentle', 'calm', 'chill', 'easygoing', 'affectionate']
                moderate_traits = ['energetic', 'loud', 'playful', 'protective', 'shy']
                high_maintenance_traits = ['reactive', 'aggressive', 'anxious', 'stubborn', 'special needs', 'independent']
                exp_lower = pref_experience.lower()
                if 'first timer' in exp_lower:
                    if any(trait in personality_list for trait in high_maintenance_traits):
                        score -= 30
                    elif not any(trait in personality_list for trait in beginner_friendly_traits):
                        score -= 15
                elif 'some experience' in exp_lower or 'experienced' in exp_lower:
                    if any(trait in personality_list for trait in moderate_traits):
                        score -= 20
                elif 'expert' in exp_lower:
                    if any(trait in personality_list for trait in high_maintenance_traits):
                        score += 20

            final_percentage = max(min(score, 100), 30)
            pet['matchPercentage'] = final_percentage
            matched_pets.append(pet)
            
        matched_pets.sort(key=lambda x: x['matchPercentage'], reverse=True)

        if uid:
            log_audit_event(
                action="Calculated Pet Matches",
                details=f"{user_name} completed the pet matching assessment and generated {len(matched_pets)} compatible pet recommendations.",
                user_id=uid,
                user_name=user_name
            )

        return jsonify({"success": True, "matches": matched_pets}), 200
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500