from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from firebase_admin import firestore
import uuid
import os
import re
from itsdangerous import URLSafeTimedSerializer, BadSignature
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

adoption_form_bp = Blueprint('adoption_form_bp', __name__)

def get_storage():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID'))
    client.set_key(os.getenv('APPWRITE_API_KEY'))
    return Storage(client)

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

def auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@adoption_form_bp.route('/apply/<pet_id>', methods=['GET'])
@auth_required
def form_view(pet_id):
    db = firestore.client()
    pet_data = None
    try:
        pet_ref = db.collection('Pets').document(pet_id)
        pet_doc = pet_ref.get()
        if pet_doc.exists:
            pet_data = pet_doc.to_dict()
            pet_data['id'] = pet_doc.id

            if pet_data.get('images'):
                pet_data['images'] = [get_file_url(img) for img in pet_data['images'] if img]
    except Exception as e:
        print(f"Error fetching contextual pet elements: {e}")

    is_submitted = request.args.get('submitted') == 'true'

    return render_template('ApplicationForm.html', pet=pet_data, success=is_submitted)

# Application Submission
@adoption_form_bp.route('/apply/<pet_id>', methods=['POST'])
@auth_required
def apply(pet_id):
    try:
        db = firestore.client()
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')
        
        if not uid:
            return jsonify({"status": "error", "message": "Unauthorized action profile"}), 401
        
        try:
            age = int(request.form.get('age', 0))
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid age format."}), 400

        if age < 18:
            return jsonify({"status": "error", "message": "Applicants must be at least 18 years old to adopt."}), 400

        contact_number = request.form.get('contact', '').strip()
        phone_regex = r'^09\d{9}$'
        if not re.match(phone_regex, contact_number):
            return jsonify({"status": "error", "message": "Invalid contact number format. Must be an 11-digit number starting with 09 (e.g., 09170000000)."}), 400

        MAX_FILE_SIZE = 5 * 1024 * 1024
        gov_id_file = request.files.get('government_id')
        proof_addr_file = request.files.get('proof_address')

        for f in [gov_id_file, proof_addr_file]:
            if f and f.filename != '':
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                f.seek(0) 
                if file_size > MAX_FILE_SIZE:
                    return jsonify({"status": "error", "message": "Each uploaded document must not exceed 5MB."}), 400

        pet_data = None
        pet_name = "Unknown Pet" 
        if pet_id != 'generic':
            pet_doc = db.collection('Pets').document(pet_id).get()
            if pet_doc.exists:
                pet_data = pet_doc.to_dict()
                pet_data['id'] = pet_doc.id
                pet_name = pet_data.get('petName') or pet_data.get('name') or "Unknown Pet"

        first_name = request.form.get('first_name', '')
        last_name = request.form.get('last_name', '')
        full_name = f"{first_name} {last_name}".strip() or "Applicant"

        application_id = str(uuid.uuid4())
        form_payload = {
            "applicationId": application_id,
            "userId": uid,
            "petId": pet_id,
            "petName": pet_name,
            "firstName": first_name,
            "lastName": last_name,
            "age": age,
            "phone": contact_number,
            "email": request.form.get('email'),
            "address": request.form.get('address'),
            "typeOfHome": request.form.get('type_of_home'),
            "homeOwnership": request.form.get('home_ownership'),
            "reasonToAdopt": request.form.get('reason_to_adopt'),
            "dailyRoutine": request.form.get('daily_routine'),
            "status": "Under Review",
            "scheduleVisitation": None,
            "monitoringStatus": "Not Started",
            "submittedAt": firestore.SERVER_TIMESTAMP
        }

        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645') 
        appwrite_endpoint = os.getenv('APPWRITE_ENDPOINT', 'https://sgp.cloud.appwrite.io/v1')
        project_id = os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63')

        if gov_id_file and gov_id_file.filename != '':
            file_bytes = gov_id_file.read()
            filename = f"{uid}_id_{uuid.uuid4().hex[:6]}_{gov_id_file.filename}"
            result = storage.create_file(
                bucket_id=bucket_id,
                file_id='unique()',
                file=InputFile.from_bytes(file_bytes, filename)
            )
            file_id = result.id 
            form_payload['documentGovIdUrl'] = f"{appwrite_endpoint}/storage/buckets/{bucket_id}/files/{file_id}/view?project={project_id}"

        if proof_addr_file and proof_addr_file.filename != '':
            file_bytes = proof_addr_file.read()
            filename = f"{uid}_proof_{uuid.uuid4().hex[:6]}_{proof_addr_file.filename}"
            result = storage.create_file(
                bucket_id=bucket_id,
                file_id='unique()',
                file=InputFile.from_bytes(file_bytes, filename)
            )
            file_id = result.id  
            form_payload['documentProofAddressUrl'] = f"{appwrite_endpoint}/storage/buckets/{bucket_id}/files/{file_id}/view?project={project_id}"

        db.collection('Applications').document(application_id).set(form_payload)
        user_ref = db.collection('Users').document(uid)
        user_ref.update({
            'applications': firestore.ArrayUnion([application_id])
        })

        log_audit_event(
            action="Adoption Application",
            details=f"{full_name} submitted an application for {pet_name}",
            user_id=uid,
            user_name=full_name
        )

        return redirect(url_for('adoption_form_bp.form_view', pet_id=pet_id, submitted='true'))
    except Exception as e:
        print(f"Error executing Appwrite storage upload sequences: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500