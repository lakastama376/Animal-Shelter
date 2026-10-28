from flask import Blueprint, render_template, request, jsonify, session
from firebase_admin import firestore
import firebase_admin
from datetime import datetime
from Data.audit import log_audit_event

about_bp = Blueprint('about_bp', __name__)
    
def get_db():
    return firestore.client()

def is_admin():
    if 'user' not in session:
        return False
    uid = session['user'].get('uid') or session['user'].get('user_id')
    if not uid:
        return False
    db = get_db()
    user_doc = db.collection('Users').document(uid).get()
    return user_doc.exists and user_doc.to_dict().get('role') == 'Admin'

def get_admin_info():
    db = get_db()
    uid = session['user'].get('uid') or session['user'].get('user_id') or "Unknown"
    admin_name = "Admin"
    if uid != "Unknown":
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict()
            admin_name = u_data.get('fullName') or f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or admin_name
    return uid, admin_name

def get_shelter_settings(db):
    settings_ref = db.collection('Settings').document('shelter_config')
    doc = settings_ref.get()
    
    if doc.exists:
        return doc.to_dict()
    else:
        default_settings = {
            "maxCapacity": 50,
            "isAccepting": True,
            "enforceBoundary": True,
            "showShelterPin": True
        }
        settings_ref.set(default_settings)
        return default_settings

DEFAULT_JOURNEY = [
    {"year": "", "title": "", "description": ""}
]

DEFAULT_MISSION = {
    "subtitle": "",
    "title": "",
    "paragraph": ""
}

# About
@about_bp.route('/about', methods=['GET'])
def about_page():
    db = get_db()
    user_session = session.get('user', {})
    uid = user_session.get('uid') or user_session.get('user_id')
    user_data = {}
    role = 'Guest'
    admin_status = False

    if uid:
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            user_data['uid'] = uid  
            role = user_data.get('role', 'Adopter')
            admin_status = (role == 'Admin')
        else:
            user_data = user_session

    mission_ref = db.collection('About').document('mission')
    mission_doc = mission_ref.get()
    
    if not mission_doc.exists:
        mission_ref.set(DEFAULT_MISSION)
        mission_data = DEFAULT_MISSION
    else:
        mission_data = mission_doc.to_dict()

    try:
        rehomed_count = len(list(db.collection('Pets').where('status', '==', 'Adopted').stream()))
        active_pets = db.collection('Pets').where('status', '!=', 'Adopted').stream()
        occupied_spaces = sum(1 for _ in active_pets)
        resolved_reports = db.collection('RescueReports').where('status', '==', 'Resolved').stream()
        rescues_by_year = {}
        for report in resolved_reports:
            data = report.to_dict()
            ts = data.get('submittedAt')

            if ts and hasattr(ts, 'year'):
                year = ts.year
            else:
                year = datetime.now().year
            rescues_by_year[year] = rescues_by_year.get(year, 0) + 1

        rescued_count = max(rescues_by_year.values()) if rescues_by_year else 0
        shelter_config = get_shelter_settings(db)
        max_capacity = shelter_config.get('maxCapacity', 50)
        shelter_capacity_str = f"{occupied_spaces}/{max_capacity}"
        active_users_count = len(list(db.collection('Users').where('status', '==', 'active').stream()))

    except Exception as e:
        print(f"Error fetching dynamic stats: {e}")
        rehomed_count = 0
        rescued_count = 0
        shelter_capacity_str = "0/50"
        active_users_count = 0

    stats = {
        "rehomed": f"{rehomed_count}+" if rehomed_count > 0 else "0",
        "stat1_val": "2019",
        "stat1_label": "Year Founded",
        "stat2_val": str(rescued_count),
        "stat2_label": "Rescue this year",
        "stat3_val": shelter_capacity_str,
        "stat3_label": "Shelter Capacity",
        "stat4_val": str(active_users_count),
        "stat4_label": "Active Users"
    }

    journey_ref = db.collection('About')
    docs = journey_ref.order_by('year').get()
    
    journey_items = []
    if not docs:
        for item in DEFAULT_JOURNEY:
            doc_ref = journey_ref.document()
            item_data = {
                'id': doc_ref.id,
                'year': item['year'],
                'title': item['title'],
                'description': item['description'],
                'createdAt': firestore.SERVER_TIMESTAMP
            }
            doc_ref.set(item_data)
            journey_items.append(item_data)
    else:
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            journey_items.append(data)

    return render_template(
        'About.html', 
        journey_items=journey_items,
        mission=mission_data,
        stats=stats,
        user=user_data,        
        user_profile=user_data,    
        role=role,                  
        is_admin=admin_status,       
        active_page='about'          
    )

# Edit Mission
@about_bp.route('/api/mission/edit', methods=['PUT', 'POST'])
def edit_mission():
    if not is_admin():
        return jsonify({"status": "error", "message": "Unauthorized access. Admin privileges required."}), 403

    data = request.get_json() or {}
    
    subtitle = data.get('subtitle', '').strip()
    title = data.get('title', '').strip()
    paragraph = data.get('paragraph', '').strip()

    if not title or not paragraph:
        return jsonify({"status": "error", "message": "Title and main paragraph are required."}), 400

    try:
        db = get_db()
        mission_ref = db.collection('About').document('mission')
        payload = {
            'subtitle': subtitle or "Our Mission",
            'title': title,
            'paragraph': paragraph,
            'updatedAt': firestore.SERVER_TIMESTAMP
        }
        
        mission_ref.set(payload, merge=True)
        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated About Mission",
            details=f"Admin {admin_name} updated the Mission",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "Mission section updated successfully!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Adding Timeline
@about_bp.route('/api/journey/add', methods=['POST'])
def add_journey_milestone():
    if not is_admin():
        return jsonify({"status": "error", "message": "Unauthorized access. Admin privileges required."}), 403

    data = request.get_json() or {}
    year = data.get('year', '').strip()
    title = data.get('title', '').strip()
    description = data.get('description', '').strip()

    if not year or not title or not description:
        return jsonify({"status": "error", "message": "All fields (year, title, description) are required."}), 400

    try:
        db = get_db()
        doc_ref = db.collection('About').document()
        payload = {
            'id': doc_ref.id,
            'year': year,
            'title': title,
            'description': description,
            'createdAt': firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(payload)
        
        response_item = {
            'id': doc_ref.id,
            'year': year,
            'title': title,
            'description': description
        }

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Added Journey Milestone",
            details=f"Admin {admin_name} added a new journey milestone for year {year}: '{title}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "Milestone added successfully!", "item": response_item}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Edit Timeline
@about_bp.route('/api/journey/edit/<item_id>', methods=['PUT', 'POST'])
def edit_journey_milestone(item_id):
    if not is_admin():
        return jsonify({"status": "error", "message": "Unauthorized access. Admin privileges required."}), 403

    data = request.get_json() or {}
    year = data.get('year', '').strip()
    title = data.get('title', '').strip()
    description = data.get('description', '').strip()

    if not year or not title or not description:
        return jsonify({"status": "error", "message": "All fields (year, title, description) are required."}), 400

    try:
        db = get_db()
        doc_ref = db.collection('About').document(item_id)
        if not doc_ref.get().exists:
            return jsonify({"status": "error", "message": "Milestone not found."}), 404

        doc_ref.update({
            'year': year,
            'title': title,
            'description': description,
            'updatedAt': firestore.SERVER_TIMESTAMP
        })

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated Journey Milestone",
            details=f"Admin {admin_name} updated journey milestone for year {year}: '{title}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "Milestone updated successfully!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Delete
@about_bp.route('/api/journey/delete/<item_id>', methods=['DELETE', 'POST'])
def delete_journey_milestone(item_id):
    if not is_admin():
        return jsonify({"status": "error", "message": "Unauthorized access. Admin privileges required."}), 403

    try:
        db = get_db()
        doc_ref = db.collection('About').document(item_id)
        doc_snapshot = doc_ref.get()

        if not doc_snapshot.exists:
            return jsonify({"status": "error", "message": "Milestone not found."}), 404

        old_data = doc_snapshot.to_dict() or {}
        old_title = old_data.get('title', 'Unknown Title')
        old_year = old_data.get('year', 'Unknown Year')
        doc_ref.delete()
        admin_uid, admin_name = get_admin_info()

        log_audit_event(
            action="Deleted Journey Milestone",
            details=f"Admin {admin_name} deleted journey milestone for year {old_year}: '{old_title}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"status": "success", "message": "Milestone deleted successfully!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500