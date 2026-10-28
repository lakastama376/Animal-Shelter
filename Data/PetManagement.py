from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, abort
from firebase_admin import firestore
from datetime import datetime
from functools import wraps
import uuid
import os
import json
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

pet_management_bp = Blueprint('pet_management_bp', __name__)

def get_storage():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT', 'https://sgp.cloud.appwrite.io/v1'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63'))
    client.set_key(os.getenv('APPWRITE_API_KEY', 'standard_36d0a70b8164a32ef4e013f4137f1b37511b2d99c1064cf9fa4e8feeea1bb8f49a8ddb42ec5fcf8a33eeca64ca1cf77dc1e5537a5ca396882cd97214978482fe74adae5c0614b6d9a4c866106549d484fff2b3767b1ea9d5383ae518a91c3321cfc79a78f540bda830c6a0e457075f78446e3884ce157976fe18e4335536672f'))
    return Storage(client)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        
        try:
            db = firestore.client()
            user_data = session.get('user', {})
            uid = user_data.get('uid') or user_data.get('user_id')
            if not uid:
                return redirect(url_for('login'))

            user_ref = db.collection('Users').document(uid)
            user_doc = user_ref.get()
            if user_doc.exists:
                user_profile = user_doc.to_dict()
                if user_profile.get('role') == 'Admin':
                    return f(*args, **kwargs)

            abort(403)
        except Exception as e:
            print(f"Authorization internal engine failure: {e}")
            abort(500)
            
    return decorated_function

def get_admin_info():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id') or "Unknown"
    admin_name = "Admin"
    if uid != "Unknown":
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict()
            admin_name = u_data.get('fullName') or f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or admin_name
    return uid, admin_name

@pet_management_bp.route('/', methods=['GET'])
@admin_required
def index():
    try:
        db = firestore.client()
        pets_ref = db.collection('Pets')
        docs = pets_ref.stream()
        
        pets_list = []
        for d in docs:
            p_data = d.to_dict()
            p_data['id'] = d.id  

            raw_date = p_data.get('rescueDate')
            if raw_date:
                try:
                    parsed_date = datetime.strptime(raw_date, '%Y-%m-%d')
                    p_data['rescueDate'] = parsed_date.strftime('%B %d, %Y')
                except ValueError:
                    pass
            
            pets_list.append(p_data)
            
        return render_template('PetManagement.html', pets=pets_list)
    except Exception as e:
        print(f"Error reading pet system databases entries: {e}")
        flash("Could not populate the tracked shelter listings.", "error")
        return render_template('PetManagement.html', pets=[])

@pet_management_bp.route('/api/<id>', methods=['GET'])
@admin_required
def get_pet_json(id):
    try:
        db = firestore.client()
        doc = db.collection('Pets').document(id).get()
        if doc.exists:
            return jsonify(doc.to_dict()), 200
        return jsonify({"error": "Target object key structure missing."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Adding Pets
@pet_management_bp.route('/add', methods=['POST'])
@admin_required
def add_pet():
    try:
        db = firestore.client()
        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645')
        project_id = os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63')
        p_traits = [t.strip() for t in request.form.get('personality', '').split(',') if t.strip()]
        c_traits = [c.strip() for c in request.form.get('compatibility', '').split(',') if c.strip()]
        h_records = [h.strip() for h in request.form.get('healthRecords', '').split(',') if h.strip()]
        
        raw_vax = request.form.get('vaccinationHistory', '[]')
        try:
            vax_history = json.loads(raw_vax)
        except Exception:
            vax_history = []

        images_list = []
        if 'images' in request.files:
            for file in request.files.getlist('images'):
                if file and file.filename != '':
                    file_bytes = file.read()
                    filename = f"pet_img_{uuid.uuid4().hex[:8]}_{file.filename}"
                    result = storage.create_file(
                        bucket_id=bucket_id,
                        file_id='unique()',
                        file=InputFile.from_bytes(file_bytes, filename)
                    )
                    file_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{result.id}/view?project={project_id}"
                    images_list.append(file_url)

        videos_list = []
        if 'storyVideos' in request.files:
            for file in request.files.getlist('storyVideos'):
                if file and file.filename != '':
                    file_bytes = file.read()
                    filename = f"pet_vid_{uuid.uuid4().hex[:8]}_{file.filename}"
                    result = storage.create_file(
                        bucket_id=bucket_id,
                        file_id='unique()',
                        file=InputFile.from_bytes(file_bytes, filename)
                    )
                    file_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{result.id}/view?project={project_id}"
                    videos_list.append(file_url)

        pet_name = request.form.get('petName', '').strip()
        pet_payload = {
            "petName": pet_name,
            "type": request.form.get('type', '').strip(), 
            "breed": request.form.get('breed', '').strip(),
            "age": request.form.get('age', '').strip(),
            "gender": request.form.get('gender', ''),
            "status": request.form.get('status', 'Available'),
            "personality": p_traits,
            "compatibility": c_traits,
            "activityLevel": request.form.get('activityLevel', 'Medium'),
            "healthRecords": h_records,
            "healthDescription": request.form.get('healthDescription', '').strip(),
            "vaccinationHistory": vax_history,
            "rescueDate": request.form.get('rescueDate', ''),
            "images": images_list,
            "storyVideos": videos_list,
            "description": request.form.get('description', '').strip(),
            "createdAt": firestore.SERVER_TIMESTAMP
        }

        db.collection('Pets').add(pet_payload)
        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Added Pet Profile",
            details=f"Admin {admin_name} added a new pet profile for '{pet_name}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        flash("New animal profile saved successfully!", "success")
    except Exception as e:
        print(f"Failed handling deployment transaction parameters: {e}")
        flash("An error occurred while saving the profile.", "error")

    return redirect(url_for('pet_management_bp.index'))

# Editing Pets Info
@pet_management_bp.route('/update', methods=['POST'])
@admin_required
def update_pet():
    try:
        db = firestore.client()
        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645')
        project_id = os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63')

        pet_id = request.form.get('petId')
        if not pet_id:
            flash("Operation targeted invalid document keys.", "error")
            return redirect(url_for('pet_management_bp.index'))

        pet_ref = db.collection('Pets').document(pet_id)
        current_pet = pet_ref.get().to_dict()
        existing_images = current_pet.get('images', []) if current_pet else []
        existing_videos = current_pet.get('storyVideos', []) if current_pet else []
        deleted_images = [i.strip() for i in request.form.get('deletedImages', '').split('|||') if i.strip()]
        deleted_videos = [v.strip() for v in request.form.get('deletedVideos', '').split('|||') if v.strip()]
        filtered_images = [img for img in existing_images if img not in deleted_images]
        filtered_videos = [vid for vid in existing_videos if vid not in deleted_videos]

        if 'images' in request.files:
            for file in request.files.getlist('images'):
                if file and file.filename != '':
                    file_bytes = file.read()
                    filename = f"pet_img_{uuid.uuid4().hex[:8]}_{file.filename}"
                    result = storage.create_file(
                        bucket_id=bucket_id,
                        file_id='unique()',
                        file=InputFile.from_bytes(file_bytes, filename)
                    )
                    file_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{result.id}/view?project={project_id}"
                    filtered_images.append(file_url)

        if 'storyVideos' in request.files:
            for file in request.files.getlist('storyVideos'):
                if file and file.filename != '':
                    file_bytes = file.read()
                    filename = f"pet_vid_{uuid.uuid4().hex[:8]}_{file.filename}"
                    result = storage.create_file(
                        bucket_id=bucket_id,
                        file_id='unique()',
                        file=InputFile.from_bytes(file_bytes, filename)
                    )
                    file_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{result.id}/view?project={project_id}"
                    filtered_videos.append(file_url)

        p_traits = [t.strip() for t in request.form.get('personality', '').split(',') if t.strip()]
        c_traits = [c.strip() for c in request.form.get('compatibility', '').split(',') if c.strip()]
        h_records = [h.strip() for h in request.form.get('healthRecords', '').split(',') if h.strip()]

        raw_vax = request.form.get('vaccinationHistory', '[]')
        try:
            vax_history = json.loads(raw_vax)
        except Exception:
            vax_history = []

        pet_name = request.form.get('petName', '').strip()
        update_payload = {
            "petName": pet_name,
            "type": request.form.get('type', '').strip(),
            "breed": request.form.get('breed', '').strip(),
            "age": request.form.get('age', '').strip(),
            "gender": request.form.get('gender', ''),
            "status": request.form.get('status', 'Available'),
            "personality": p_traits,
            "compatibility": c_traits,
            "activityLevel": request.form.get('activityLevel', 'Medium'),
            "healthRecords": h_records,
            "healthDescription": request.form.get('healthDescription', '').strip(),
            "vaccinationHistory": vax_history,
            "rescueDate": request.form.get('rescueDate', ''),
            "description": request.form.get('description', '').strip(),
            "images": filtered_images,
            "storyVideos": filtered_videos
        }

        pet_ref.update(update_payload)
        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated Pet Profile",
            details=f"Admin {admin_name} updated pet profile for '{pet_name}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        flash("Record modified successfully.", "success")
    except Exception as e:
        print(f"Error running database document reference edit workflows: {e}")
        flash("Failed processing modifications smoothly.", "error")

    return redirect(url_for('pet_management_bp.index'))

# Delete
@pet_management_bp.route('/delete/<id>', methods=['GET'])
@admin_required
def delete_pet(id):
    try:
        db = firestore.client()
        pet_ref = db.collection('Pets').document(id)
        doc = pet_ref.get()
        pet_name = "Unknown Pet"
        if doc.exists:
            pet_data = doc.to_dict()
            pet_name = pet_data.get('petName') or pet_data.get('name') or pet_name

        pet_ref.delete()
        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Deleted Pet Profile",
            details=f"Admin {admin_name} deleted pet profile for '{pet_name}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        flash("Animal database instance discarded cleanly.", "success")
    except Exception as e:
        print(f"Error purging entity sequence records targets: {e}")
        flash("Failed to drop record entry instance references.", "error")
        
    return redirect(url_for('pet_management_bp.index'))