from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from firebase_admin import firestore
import os
import random
from datetime import datetime, timedelta, timezone
from itsdangerous import URLSafeTimedSerializer, BadSignature
from Data.audit import log_audit_event

eligibility_checker_bp = Blueprint('eligibility_checker_bp', __name__)

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

def get_utc_now():
    return datetime.now(timezone.utc)

def create_lockout():
    return get_utc_now() + timedelta(hours=24)

def normalize_firestore_datetime(value):
    if not value:
        return None

    if hasattr(value, 'datetime'):
        value = value.datetime()

    if not isinstance(value, datetime):
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)

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

def get_pet_reservation_block(db, pet_id, pet_data=None):
    if not pet_id:
        return False, ''

    if pet_data is None:
        pet_doc = db.collection('Pets').document(pet_id).get()
        if not pet_doc.exists:
            return False, ''
        pet_data = pet_doc.to_dict() or {}

    raw_status = str(pet_data.get('status') or '').strip()
    if raw_status.lower() == 'reserved':
        return True, 'Reserved'

    if raw_status and raw_status.lower() != 'available':
        return True, raw_status

    reserved_apps = db.collection('Applications').where('petId', '==', pet_id).stream()
    for app_doc in reserved_apps:
        app_status = str((app_doc.to_dict() or {}).get('status') or '').strip().lower()
        if app_status in ['approved', 'visit scheduled']:
            return True, 'Reserved'

    return False, ''

# Eligibility Questions
@eligibility_checker_bp.route('/eligibility', methods=['GET'])
@eligibility_checker_bp.route('/eligibility/<pet_id>', methods=['GET'])
@auth_required
def index(pet_id=None):
    db = firestore.client()
    pet_data = None
    is_locked_out = False
    lockout_time_str = ""

    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    if uid:
        has_active, _, active_detail = get_active_adoption_block(db, uid)
        if has_active:
            session.pop('pending_pet_id', None)
            flash(
                f"You currently have {active_detail}. Complete your adoption and monitoring first before starting a new application.",
                "error"
            )
            return redirect(url_for('adoption_management_bp.management_view'))

    if not pet_id:
        pet_id = request.args.get('pet_id') or request.args.get('petId')

    if pet_id:
        session['pending_pet_id'] = pet_id
    else:
        pet_id = session.get('pending_pet_id')

    if pet_id:
        pet_doc = db.collection('Pets').document(pet_id).get()
        if pet_doc.exists:
            pet_data = pet_doc.to_dict()
            pet_data['id'] = pet_doc.id

            if pet_data.get('images'):
                pet_data['images'] = [get_file_url(img) for img in pet_data['images'] if img]

            is_blocked, block_status = get_pet_reservation_block(db, pet_id, pet_data)
            if is_blocked:
                if session.get('pending_pet_id') == pet_id:
                    session.pop('pending_pet_id', None)
                if block_status.lower() == 'reserved':
                    flash("This pet is currently reserved and cannot proceed to the eligibility checker.", "error")
                else:
                    flash(f"This pet is currently {block_status} and cannot receive adoption applications.", "error")
                return redirect(url_for('pet_profile_bp.pet_profile', id=pet_id))

    if uid:
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            user_dict = user_doc.to_dict()

            if user_dict.get('is_eligible') or session.get('is_eligible'):
                session.pop('pending_pet_id', None)
                if pet_id:
                    return redirect(url_for('adoption_form_bp.form_view', pet_id=pet_id))
                else:
                    return redirect(url_for('pet_matching_bp.index'))

            lockout_until = user_dict.get('retake_lockout_until')
            lockout_dt = normalize_firestore_datetime(lockout_until)
            if lockout_dt:
                if get_utc_now() < lockout_dt:
                    is_locked_out = True
                    philippines_time = lockout_dt.astimezone(timezone(timedelta(hours=8)))
                    lockout_time_str = philippines_time.strftime('%B %d, %Y at %I:%M %p')

    try:
        questions_ref = db.collection('EligibilityQuestions').order_by('createdAt', direction=firestore.Query.DESCENDING).stream()
        all_questions = [dict(doc.to_dict(), id=doc.id) for doc in questions_ref]
        default_questions = [q for q in all_questions if q.get('type') == 'default']

        for q in default_questions:
            if 'choices' in q and isinstance(q['choices'], list):
                shuffled_choices = list(q['choices'])
                random.shuffle(shuffled_choices)
                q['choices'] = shuffled_choices

    except Exception as e:
        print(f"Error compiling quiz parameters: {str(e)}")
        default_questions = []

    return render_template(
        'EligibilityChecker.html',
        pet=pet_data,
        questions=default_questions,
        is_locked_out=is_locked_out,
        lockout_time=lockout_time_str
    )

# Eligibility Checker Submit
@eligibility_checker_bp.route('/eligibility/submit', methods=['POST'])
@auth_required
def submit_evaluation():
    try:
        db = firestore.client()
        data = request.json or {}
        answers = data.get('answers', {})
        pet_id = data.get('petId') or session.get('pending_pet_id')

        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')

        if uid:
            has_active, _, active_detail = get_active_adoption_block(db, uid)
            if has_active:
                session.pop('pending_pet_id', None)
                flash(
                    f"You currently have {active_detail}. Complete your adoption and monitoring first before starting a new application.",
                    "error"
                )
                return jsonify({
                    "success": True,
                    "passed": False,
                    "blocked_active_adoption": True,
                    "redirect_url": url_for('adoption_management_bp.management_view')
                })

        if pet_id:
            pet_doc = db.collection('Pets').document(pet_id).get()
            if pet_doc.exists:
                pet_dict = pet_doc.to_dict() or {}
                is_blocked, block_status = get_pet_reservation_block(db, pet_id, pet_dict)
                if is_blocked:
                    msg = "This pet is currently reserved and cannot accept adoption applications." if block_status.lower() == 'reserved' else "This pet is no longer available for adoption."
                    return jsonify({"success": False, "message": msg}), 400

        user_name = "User"
        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                user_dict = user_doc.to_dict()
                user_name = user_dict.get('name') or user_dict.get('fullName') or user_dict.get('email') or user_data.get('name') or user_data.get('email') or "User"

        if not answers:
            return jsonify({"success": False, "message": "Please answer all questions."}), 400

        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                user_dict = user_doc.to_dict()
                lockout_until = user_dict.get('retake_lockout_until')
                lockout_dt = normalize_firestore_datetime(lockout_until)
                if lockout_dt and get_utc_now() < lockout_dt:
                    philippines_time = lockout_dt.astimezone(timezone(timedelta(hours=8)))
                    formatted_time = philippines_time.strftime('%B %d, %Y at %I:%M %p')
                    return jsonify({
                        "success": False,
                        "locked_out": True,
                        "message": (
                            f"You are locked out. You can retake the quiz "
                            f"after {formatted_time}."
                        ),
                        "lockoutUntil": formatted_time
                    }), 403

        sample_q_id = list(answers.keys())[0]
        sample_q_doc = db.collection('EligibilityQuestions').document(sample_q_id).get()
        if not sample_q_doc.exists:
            return jsonify({"success": False, "message": "Invalid questions submitted."}), 400
            
        is_retake_submission = sample_q_doc.to_dict().get('type') == 'random'
        if is_retake_submission:
            article_id = sample_q_doc.to_dict().get('articleId')
            questions_ref = db.collection('EligibilityQuestions') \
                              .where('type', '==', 'random') \
                              .where('articleId', '==', article_id) \
                              .stream()
        else:
            questions_ref = db.collection('EligibilityQuestions') \
                              .where('type', '==', 'default') \
                              .stream()

        db_questions = [doc.id for doc in questions_ref]
        total_questions = len(db_questions)
        if len(answers) < total_questions:
            return jsonify({"success": False, "message": "Please answer all questions before submitting."}), 400

        earned_score = 0.0
        for q_id, selected_text in answers.items():
            q_doc = db.collection('EligibilityQuestions').document(q_id).get()
            if q_doc.exists:
                choices = q_doc.to_dict().get('choices', [])
                for choice in choices:
                    if isinstance(choice, dict) and choice.get('text') == selected_text:
                        status = choice.get('status')
                        if status == 'correct':
                            earned_score += 1.0 
                        elif status == 'good':
                            earned_score += 0.5 
                        elif status == 'incorrect':
                            earned_score += 0.0
                        break

        score_percentage = round((earned_score / total_questions) * 100) if total_questions > 0 else 0
        passed = (score_percentage >= 80)

        redirect_url = ""
        lockout_until_str = ""
        if passed:
            session['is_eligible'] = True
            if uid:
                db.collection('Users').document(uid).update({
                    'is_eligible': True,
                    'retake_lockout_until': None
                })
                log_audit_event(
                    action="Eligibility Quiz Passed",
                    details=f"{user_name} passed the eligibility test with a score of {score_percentage}%.",
                    user_id=uid,
                    user_name=user_name
                )

            session.pop('pending_pet_id', None)
            if pet_id:
                redirect_url = url_for('adoption_form_bp.form_view', pet_id=pet_id)
            else:
                redirect_url = url_for('pet_matching_bp.index')
        else:
            if is_retake_submission:
                session['is_eligible'] = False
                tomorrow = create_lockout()
                philippines_time = tomorrow.astimezone(timezone(timedelta(hours=8)))
                lockout_until_str = philippines_time.strftime('%B %d, %Y at %I:%M %p')
                
                if uid:
                    db.collection('Users').document(uid).update({
                        'is_eligible': False,
                        'retake_lockout_until': tomorrow
                    })
                    log_audit_event(
                        action="Article Quiz Failed & Locked Out",
                        details=f"{user_name} failed the article retake quiz with {score_percentage}% score and was locked out until {lockout_until_str}.",
                        user_id=uid,
                        user_name=user_name
                    )
                
                redirect_url = url_for('article_content_bp.view_article', article_id=article_id, pet_id=pet_id, retake='true')
            else:
                if uid:
                    log_audit_event(
                        action="Eligibility Quiz Failed",
                        details=f"{user_name} failed the baseline eligibility check with a score of {score_percentage}%.",
                        user_id=uid,
                        user_name=user_name
                    )
                try:
                    articles_ref = db.collection('Articles').stream()
                    all_articles = [doc.id for doc in articles_ref]
                    if all_articles:
                        random_article_id = random.choice(all_articles)
                        redirect_url = url_for(
                            'article_content_bp.view_article',
                            article_id=random_article_id,
                            pet_id=pet_id,
                            retake='true'
                        )
                    else:
                        redirect_url = url_for('eligibility_checker_bp.index', pet_id=pet_id) if pet_id else url_for('eligibility_checker_bp.index')
                except Exception as e:
                    print(f"Error fetching retake article: {str(e)}")
                    redirect_url = url_for('eligibility_checker_bp.index', pet_id=pet_id) if pet_id else url_for('eligibility_checker_bp.index')

        return jsonify({
            "success": True,
            "passed": passed,
            "score_percentage": score_percentage,
            "redirect_url": redirect_url,
            "lockoutUntil": lockout_until_str
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@eligibility_checker_bp.route('/eligibility/cheat-violation', methods=['POST'])
@auth_required
def cheat_violation():
    try:
        db = firestore.client()
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')

        user_name = "User"
        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                user_dict = user_doc.to_dict()
                user_name = user_dict.get('name') or user_dict.get('fullName') or user_dict.get('email') or "User"

                tomorrow = create_lockout()
                philippines_time = tomorrow.astimezone(timezone(timedelta(hours=8)))
                lockout_until_str = philippines_time.strftime('%B %d, %Y at %I:%M %p')
                db.collection('Users').document(uid).update({
                    'is_eligible': False,
                    'retake_lockout_until': tomorrow
                })

                log_audit_event(
                    action="Article Quiz Cheating Detected & Locked Out",
                    details=f"{user_name} violated article quiz anti-cheat rules (switched tabs or time expired) and was locked out until {lockout_until_str}.",
                    user_id=uid,
                    user_name=user_name
                )

        return jsonify({"success": True, "message": "Violation logged. User locked out."}), 200
    except Exception as e:
        print(f"Error handling article cheat violation: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500