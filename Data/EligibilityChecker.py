from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from firebase_admin import firestore
import random
from datetime import datetime, timedelta
from Data.audit import log_audit_event

eligibility_checker_bp = Blueprint('eligibility_checker_bp', __name__)

def auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Eligibility Questions
@eligibility_checker_bp.route('/eligibility', methods=['GET'])
@eligibility_checker_bp.route('/eligibility/<pet_id>', methods=['GET'])
@auth_required
def index(pet_id=None):
    db = firestore.client()
    pet_data = None
    is_locked_out = False
    lockout_time_str = ""

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

    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    if uid:
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            user_dict = user_doc.to_dict()
            lockout_until = user_dict.get('retake_lockout_until')

            if lockout_until:
                lockout_dt = lockout_until.datetime() if hasattr(lockout_until, 'datetime') else lockout_until
                if lockout_dt and datetime.now(lockout_dt.tzinfo) < lockout_dt:
                    is_locked_out = True
                    lockout_time_str = lockout_dt.strftime('%B %d, %Y at %I:%M %p')

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

# Eligibility Checker
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

                if lockout_until:
                    lockout_dt = lockout_until.datetime() if hasattr(lockout_until, 'datetime') else lockout_until
                    
                    if datetime.now(lockout_dt.tzinfo) < lockout_dt:
                        formatted_time = lockout_dt.strftime('%B %d, %Y at %I:%M %p')
                        return jsonify({
                            "success": False,
                            "message": f"You are locked out. Try again after {formatted_time}."
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
                redirect_url = url_for('adoption_form_bp.apply', pet_id=pet_id)
            else:
                redirect_url = url_for('pet_matching_bp.index')
        else:
            if is_retake_submission:
                session['is_eligible'] = False
                tomorrow = datetime.now() + timedelta(days=1)
                lockout_until_str = tomorrow.strftime('%B %d, %Y at %I:%M %p')
                
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