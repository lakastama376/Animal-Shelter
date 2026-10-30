from flask import Flask, redirect, render_template, request, make_response, session, abort, jsonify, url_for
from firebase_admin import credentials, firestore, auth
import firebase_admin 
from functools import wraps
from datetime import timedelta
import secrets
import os
import uuid 
import json
from Data.audit import log_audit_event
from dotenv import load_dotenv


from Data.Home import home_bp
from Data.Dashboard import dashboard_bp
from Data.About import about_bp
from Data.Contact import contact_bp
from Data.Profile import profile_bp
from Data.PetWishlist import wishlist_bp
from Data.PetProfile import pet_profile_bp
from Data.PetList import pet_public_bp
from Data.PetManagement import pet_management_bp
from Data.ContentManagement import content_management_bp
from Data.ArticleContent import article_content_bp
from Data.EligibilityChecker import eligibility_checker_bp
from Data.PetMatching import pet_matching_bp
from Data.ApplicationForm import adoption_form_bp
from Data.Adoption import adoption_management_bp
from Data.Monitoring import monitoring_bp
from Data.ArticleList import article_list_bp
from Data.RescueReport import rescue_report_bp
from Data.Message import message_bp
from Data.Settings import settings_bp
from Data.UserManagement import user_management_bp
from Data.Donation import donation_bp

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') == 'production' 
app.config['SESSION_COOKIE_HTTPONLY'] = True  
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)  
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' 

firebase_creds_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')

if firebase_creds_json:
    cred_dict = json.loads(firebase_creds_json)
    cred = credentials.Certificate(cred_dict)
else:
    cred_path = "firebase-auth.json"
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
    else:
        raise FileNotFoundError("Firebase credentials not found. Please set FIREBASE_SERVICE_ACCOUNT_JSON.")

firebase_admin.initialize_app(cred)
db = firestore.client()

@app.context_processor
def inject_user_profile():
    if 'user' in session:
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')
        if uid:
            try:
                user_ref = db.collection('Users').document(uid)
                user_doc = user_ref.get()
                if user_doc.exists:
                    return dict(user_profile=user_doc.to_dict())
            except Exception as e:
                print(f"Error injecting template context: {e}")
    return dict(user_profile=None)

########################################
""" Authentication and Authorization """

@app.before_request
def validate_user_session():
    public_endpoints = [
        'login', 'signup', 'authorize', 'reset_password', 
        'terms', 'privacy', 'static',
        'home_bp.home', 'about_bp.about_page', 'contact_bp.contact_page',
        'pet_public_bp.pet_list', 'donation_bp.index'      
    ]
    
    if request.endpoint in public_endpoints or (request.endpoint and 'static' in request.endpoint):
        return

    if 'user' in session:
        uid = session['user'].get('uid')
        client_session_token = session.get('session_token')

        if uid:
            try:
                user_doc = db.collection('Users').document(uid).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()

                    if user_data.get('status') == 'blocked':
                        session.clear()
                        if request.is_json or request.path.startswith('/api/'):
                            return jsonify({
                                "status": "blocked", 
                                "message": "Your account has been blocked by an administrator. Please contact support."
                            }), 403
                        return render_template('Login.html', error_message="Your account has been blocked by an administrator.")
                    
                    active_server_token = user_data.get('currentSessionToken')
                    if active_server_token and client_session_token != active_server_token:
                        session.clear() 
                        if request.is_json or request.path.startswith('/api/'):
                            return jsonify({
                                "status": "session_expired", 
                                "message": "You have been logged out because your account was logged into from another device."
                            }), 401
                        return render_template('Login.html', error_message="Your account was logged in from another device.")
            except Exception as e:
                print(f"Error checking session token: {e}")
    else:
        if request.is_json or request.path.startswith('/api/'):
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return redirect(url_for('login'))

def auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/auth', methods=['POST'])
def authorize():
    token = request.headers.get('Authorization')
    if not token or not token.startswith('Bearer '):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    token = token[7:] 
    data = request.json or {}
    try:
        decoded_token = auth.verify_id_token(token, check_revoked=True, clock_skew_seconds=60)
        uid = decoded_token['uid']
        user_ref = db.collection('Users').document(uid)
        user_doc = user_ref.get()

        if user_doc.exists:
            user_data = user_doc.to_dict()
            if user_data.get('status') == 'blocked':
                return jsonify({
                    "status": "blocked",
                    "message": "Your account has been blocked by an administrator. Please contact support."
                }), 403

        new_session_token = str(uuid.uuid4())
        if not decoded_token.get('email_verified', False):
            if not user_doc.exists:
                first_name = data.get('first_name', '')
                last_name = data.get('last_name', '')
                user_profile = {
                    'uid': uid,
                    'email': decoded_token.get('email', data.get('email', '')),
                    'username': data.get('username', ''),
                    'firstName': first_name,
                    'lastName': last_name,
                    'fullName': f"{first_name} {last_name}".strip(),
                    'phone': data.get('contact', ''),
                    'address': data.get('address', ''),
                    'role': data.get('role', 'Adopter'),
                    'status': "active",
                    'emailVerified': False,
                    'photoURL': data.get('photo_url', ''),  
                    'joinedAt': firestore.SERVER_TIMESTAMP,
                    'lastLogin': firestore.SERVER_TIMESTAMP,
                    'currentSessionToken': new_session_token, 
                    'adoptionCount': 0,
                    'rescueReportsSubmitted': 0,
                    'wishlist': [],
                    'applications': []
                }
                user_ref.set(user_profile)
            return jsonify({"status": "unverified", "message": "Email verification required."}), 403

        session['user'] = decoded_token 
        session['session_token'] = new_session_token
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')
        
        if not user_doc.exists:
            user_profile = {
                'uid': uid,
                'email': decoded_token.get('email', data.get('email', '')),
                'username': data.get('username', ''),
                'firstName': first_name,
                'lastName': last_name,
                'fullName': f"{first_name} {last_name}".strip(),
                'phone': data.get('contact', ''),
                'address': data.get('address', ''),
                'role': data.get('role', 'Adopter'),
                'status': "active",
                'emailVerified': True,
                'photoURL': data.get('photo_url', ''), 
                'joinedAt': firestore.SERVER_TIMESTAMP,
                'lastLogin': firestore.SERVER_TIMESTAMP,
                'currentSessionToken': new_session_token,  
                'adoptionCount': 0,
                'rescueReportsSubmitted': 0,
                'wishlist': [],
                'applications': []
            }
            user_ref.set(user_profile)
            full_name = user_profile['fullName'] or decoded_token.get('email', 'User')
        else:
            update_payload = {
                'lastLogin': firestore.SERVER_TIMESTAMP,
                'emailVerified': True,
                'currentSessionToken': new_session_token 
            }
            
            photo_url = data.get('photo_url')
            if first_name:
                update_payload['firstName'] = first_name
                update_payload['lastName'] = last_name or ''
                update_payload['fullName'] = f"{first_name} {last_name}".strip()
            if photo_url:
                update_payload['photoURL'] = photo_url 

            user_ref.update(update_payload)
            updated_doc = user_ref.get().to_dict()
            full_name = updated_doc.get('fullName') or decoded_token.get('email', 'User')

        log_audit_event(
            action="User Login",
            details=f"{full_name} logged into the system successfully.",
            user_id=uid,
            user_name=full_name
        )

        return jsonify({"status": "success", "redirect": url_for('dashboard_bp.dashboard_view')}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 401

@app.route('/api/audit/delete/<log_id>', methods=['DELETE'])
@auth_required
def delete_audit_log(log_id):
    try:
        uid = session['user']['uid']
        user_doc = db.collection('Users').document(uid).get()

        if not user_doc.exists or str(user_doc.to_dict().get('role', '')).strip().lower() != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized: Admin privileges required."}), 403

        db.collection('AuditLogs').document(log_id).delete()
        return jsonify({"status": "success", "message": "Audit log deleted successfully."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


####################
""" Auth Routes """

@app.route('/login')
def login():
    if 'user' in session:
        uid = session['user'].get('uid')
        client_token = session.get('session_token')
        
        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                active_token = user_doc.to_dict().get('currentSessionToken')
                if active_token and client_token == active_token:
                    return redirect(url_for('dashboard_bp.dashboard_view'))

        session.clear()
    return render_template('Login.html')

@app.route('/register')
def signup():
    if 'user' in session:
        return redirect(url_for('dashboard_bp.dashboard_view'))
    return render_template('Register.html')

@app.route('/reset-password')
def reset_password():
    if 'user' in session:
        return redirect(url_for('dashboard_bp.dashboard_view'))
    return render_template('Forgot_Password.html')

@app.route('/terms')
def terms():
    return render_template('Terms.html')

@app.route('/privacy')
def privacy():
    return render_template('Privacy.html')

@app.route('/logout')
def logout():
    session.clear()
    response = make_response(redirect(url_for('login')))
    response.set_cookie('session', '', expires=0)  
    return response

########################
""" Blueprint Routes """

app.register_blueprint(home_bp, name='home_bp')
app.register_blueprint(dashboard_bp, name='dashboard_bp')
app.register_blueprint(about_bp, name='about_bp')
app.register_blueprint(contact_bp, name='contact_bp')
app.register_blueprint(profile_bp, name='profile_bp')
app.register_blueprint(wishlist_bp, name='wishlist_bp')
app.register_blueprint(pet_management_bp, name='pet_management_bp', url_prefix='/pet-management')
app.register_blueprint(pet_profile_bp, name='pet_profile_bp', url_prefix='/pet-management')
app.register_blueprint(pet_public_bp, name='pet_public_bp', url_prefix='/pet-management')
app.register_blueprint(content_management_bp, name='content_management_bp')
app.register_blueprint(article_content_bp, name='article_content_bp')
app.register_blueprint(eligibility_checker_bp, name='eligibility_checker_bp')
app.register_blueprint(pet_matching_bp, name='pet_matching_bp')
app.register_blueprint(adoption_form_bp, name='adoption_form_bp')
app.register_blueprint(adoption_management_bp, name='adoption_management_bp')
app.register_blueprint(monitoring_bp, name='monitoring_bp')
app.register_blueprint(article_list_bp, name='article_list_bp')
app.register_blueprint(rescue_report_bp, name='rescue_report_bp')
app.register_blueprint(message_bp, name='message_bp')
app.register_blueprint(settings_bp, name='settings_bp')
app.register_blueprint(user_management_bp, name='user_management_bp')
app.register_blueprint(donation_bp, name='donation_bp')

if __name__ == '__main__':
    app.run(debug=True)