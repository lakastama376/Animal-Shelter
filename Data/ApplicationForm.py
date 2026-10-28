from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from firebase_admin import firestore
import uuid
import os
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

adoption_form_bp = Blueprint('adoption_form_bp', __name__)

def get_storage():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT', 'https://sgp.cloud.appwrite.io/v1'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63'))
    client.set_key(os.getenv('APPWRITE_API_KEY', 'standard_36d0a70b8164a32ef4e013f4137f1b37511b2d99c1064cf9fa4e8feeea1bb8f49a8ddb42ec5fcf8a33eeca64ca1cf77dc1e5537a5ca396882cd97214978482fe74adae5c0614b6d9a4c866106549d484fff2b3767b1ea9d5383ae518a91c3321cfc79a78f540bda830c6a0e457075f78446e3884ce157976fe18e4335536672f'))
    return Storage(client)

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
            "age": int(request.form.get('age', 0)),
            "phone": request.form.get('contact'),
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

        gov_id_file = request.files.get('government_id')
        proof_addr_file = request.files.get('proof_address')
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