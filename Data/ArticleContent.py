from flask import Blueprint, render_template, abort, request, session
from firebase_admin import firestore
import random
from datetime import datetime

article_content_bp = Blueprint('article_content_bp', __name__)

# Viewing Article
@article_content_bp.route('/education/article/<article_id>', methods=['GET'])
def view_article(article_id):
    try:
        db = firestore.client()
        doc_ref = db.collection('Articles').document(article_id).get()

        if not doc_ref.exists:
            abort(404)
            
        article_data = doc_ref.to_dict()
        article_data['id'] = doc_ref.id

        if 'createdAt' in article_data and article_data['createdAt'] is not None:
            try:
                article_data['createdAt'] = article_data['createdAt'].datetime
            except AttributeError:
                pass
        else:
            article_data['createdAt'] = None

        pet_id = request.args.get('pet_id') or request.args.get('petId')
        is_retake = request.args.get('retake') == 'true'
        is_locked_out = False
        lockout_time_str = ""
        user_data = session.get('user', {})
        uid = user_data.get('uid') or user_data.get('user_id')
        
        if uid:
            user_doc = db.collection('Users').document(uid).get()
            if user_doc.exists:
                user_dict = user_doc.to_dict()
                lockout_until = user_dict.get('retake_lockout_until')
                if lockout_until:
                    if hasattr(lockout_until, 'datetime'):
                        lockout_dt = lockout_until.datetime()
                    else:
                        lockout_dt = lockout_until
                    
                    if datetime.now(lockout_dt.tzinfo) < lockout_dt:
                        is_locked_out = True
                        lockout_time_str = lockout_dt.strftime('%B %d, %Y at %I:%M %p')

        all_questions = []
        if is_retake and not is_locked_out:
            q_ref = db.collection('EligibilityQuestions') \
                      .where('type', '==', 'random') \
                      .where('articleId', '==', article_id) \
                      .stream()
            
            for doc in q_ref:
                q_data = doc.to_dict()
                q_data['id'] = doc.id
                
                if 'choices' in q_data and isinstance(q_data['choices'], list):
                    shuffled_choices = list(q_data['choices'])
                    random.shuffle(shuffled_choices)
                    q_data['choices'] = shuffled_choices
                all_questions.append(q_data)

        return render_template(
            'ArticleContent.html', 
            article=article_data, 
            is_retake=is_retake, 
            questions=all_questions, 
            pet_id=pet_id,
            is_locked_out=is_locked_out,
            lockout_time=lockout_time_str
        )

    except Exception as e:
        print(f"Error serving article ID {article_id}: {str(e)}")
        abort(500)