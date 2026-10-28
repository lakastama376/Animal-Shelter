from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from firebase_admin import firestore
import os
import uuid
import json
import requests
from functools import wraps
from datetime import datetime, timezone
from Data.audit import log_audit_event
from appwrite.client import Client # type: ignore
from appwrite.services.storage import Storage # type: ignore
from appwrite.input_file import InputFile # type: ignore

content_management_bp = Blueprint('content_management_bp', __name__)

FACEBOOK_PAGE_ID_KEY = os.getenv('FACEBOOK_PAGE_ID_2')
INSTAGRAM_ID_KEY = os.getenv('INSTAGRAM_ID')
FB_PAGE_ACCESS_TOKEN_KEY = "EAAeRZCceQMi4BS9GSZAXwNXdxb7Lg2wX8BIIgZCc6z84aRhsZBggjijcf7micP2hvgiYs0Ov2IkZAuVEiRLKPGmWxiqXeIcYDrg9Msd7ADhIBUWnfrCb0MulDrZChWhUgbDuKstfGA5IGEyZAMffMb7C83LZAGjsCb4Q2XtbNyi8myIZB4zOpoNctlP0KBQfQvCvPQSmIB4op"
IG_PAGE_ACCESS_TOKEN_KEY = "EAAOZC2QFMz70BSr2WEYGOZBtury6c0qRgu3n9tZCANxC88lB41ZBVIE9CiPruki1B0nzWYuImse5UXFLEUM2HiOkOoiYqCHNZBbQB4TSrI2JHuxFg0FkP5iLKEr660p4mCqpWhZC6ZCqEMibQMDqk1wIDtKOMmWG615fwVzzdeZAd3OJ5gqgJWoBZCy280YoxzyIo"

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
            uid = session['user'].get('uid') or session['user'].get('user_id')
            user_doc = db.collection('Users').document(uid).get()
            if not user_doc.exists or user_doc.to_dict().get('role') != 'Admin':
                return jsonify({"status": "error", "message": "Access denied. Admins only."}), 403
            return f(*args, **kwargs)
        
        except Exception as e:
            return jsonify({"status": "error", "message": f"Auth engine error: {str(e)}"}), 500
    return decorated_function

def get_admin_info():
    db = firestore.client()
    uid = session['user'].get('uid') or session['user'].get('user_id') or "Unknown"
    admin_name = "Admin"
    if uid != "Unknown":
        user_doc = db.collection('Users').document(uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict()
            admin_name = u_data.get('fullName') or f"{u_data.get('firstName', '')} {u_data.get('lastName', '')}".strip() or admin_name
    return uid, admin_name

@content_management_bp.route('/content-management', methods=['GET'])
@admin_required
def index():
    db = firestore.client()
    articles_ref = db.collection('Articles').order_by('createdAt', direction=firestore.Query.DESCENDING).stream()

    articles = []
    for doc in articles_ref:
        data = doc.to_dict()
        data['id'] = doc.id
        articles.append(data)

    matching_ref = db.collection('PetMatchingQuestions').order_by('createdAt').stream()
    matching_questions = [dict(doc.to_dict(), id=doc.id) for doc in matching_ref]
    eligibility_ref = db.collection('EligibilityQuestions').order_by('createdAt').stream()
    eligibility_questions = [dict(doc.to_dict(), id=doc.id) for doc in eligibility_ref]
    
    posts_ref = db.collection('PostContent').order_by('createdAt').stream()
    social_posts = []
    for doc in posts_ref:
        data = doc.to_dict()
        data['id'] = doc.id

        created_at = data.get('createdAt')
        if created_at and hasattr(created_at, 'strftime'):
            if getattr(created_at, 'tzinfo', None) is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            local_time = created_at.astimezone()
            data['formatted_date'] = local_time.strftime("%b %d, %Y - %I:%M %p")
        elif isinstance(created_at, str):
            data['formatted_date'] = created_at
        else:
            data['formatted_date'] = "Just now"

        social_posts.append(data)

    return render_template(
        'ContentManagement.html', 
        articles=articles, 
        matching_questions=matching_questions,
        eligibility_questions=eligibility_questions,
        social_posts=social_posts
    )

@content_management_bp.route("/content-management/post-content", methods=["POST"])
@admin_required
def post_content():
    try:
        message = request.form.get("message")
        platforms = request.form.getlist("platforms")

        storage = get_storage()
        bucket_id = os.getenv('APPWRITE_BUCKET_ID', '6adf65220035a8df9645') 
        project_id = os.getenv('APPWRITE_PROJECT_ID', '6a9e899400185f330d63')
        uploaded_media_urls = []

        media_files = request.files.getlist('media_files')
        for file in media_files:
            if file and file.filename:
                file_bytes = file.read()
                filename = f"post_{uuid.uuid4().hex[:8]}_{file.filename}"
                
                result = storage.create_file(
                    bucket_id=bucket_id,
                    file_id='unique()',
                    file=InputFile.from_bytes(file_bytes, filename)
                )
                file_id = result.id
                file_url = f"https://sgp.cloud.appwrite.io/v1/storage/buckets/{bucket_id}/files/{file_id}/view?project={project_id}"
                uploaded_media_urls.append(file_url)

        primary_image_url = uploaded_media_urls[0] if uploaded_media_urls else None
        fb_post_id = None
        ig_post_id = None
        errors = []

        if "facebook" in platforms:
            if not uploaded_media_urls:
                fb_url = f"https://graph.facebook.com/v18.0/{FACEBOOK_PAGE_ID_KEY}/feed"
                payload = {
                    "message": message,
                    "access_token": FB_PAGE_ACCESS_TOKEN_KEY
                }
                fb_res = requests.post(fb_url, data=payload).json()
                if "id" in fb_res:
                    fb_post_id = fb_res.get("post_id", fb_res["id"])
                else:
                    errors.append(f"FB Error: {fb_res.get('error', {}).get('message', 'Failed to post on Facebook')}")
            elif len(uploaded_media_urls) == 1:
                fb_url = f"https://graph.facebook.com/v18.0/{FACEBOOK_PAGE_ID_KEY}/photos"
                payload = {
                    "caption": message,
                    "url": uploaded_media_urls[0],
                    "access_token": FB_PAGE_ACCESS_TOKEN_KEY
                }
                fb_res = requests.post(fb_url, data=payload).json()
                if "id" in fb_res:
                    fb_post_id = fb_res.get("post_id", fb_res["id"])
                else:
                    errors.append(f"FB Error: {fb_res.get('error', {}).get('message', 'Failed to post on Facebook')}")
            else:
                attached_media = []
                upload_failed = False
                for url in uploaded_media_urls:
                    photo_url = f"https://graph.facebook.com/v18.0/{FACEBOOK_PAGE_ID_KEY}/photos"
                    photo_payload = {
                        "url": url,
                        "published": "false",
                        "access_token": FB_PAGE_ACCESS_TOKEN_KEY
                    }
                    p_res = requests.post(photo_url, data=photo_payload).json()
                    if "id" in p_res:
                        attached_media.append({"media_fbid": p_res["id"]})
                    else:
                        errors.append(f"FB Error (multi-photo): {p_res.get('error', {}).get('message', 'Failed to upload photo')}")
                        upload_failed = True
                        break
                
                if not upload_failed and attached_media:
                    feed_url = f"https://graph.facebook.com/v18.0/{FACEBOOK_PAGE_ID_KEY}/feed"
                    feed_payload = {
                        "message": message,
                        "attached_media": json.dumps(attached_media),
                        "access_token": FB_PAGE_ACCESS_TOKEN_KEY
                    }
                    feed_res = requests.post(feed_url, data=feed_payload).json()
                    if "id" in feed_res:
                        fb_post_id = feed_res.get("post_id", feed_res["id"])
                    else:
                        errors.append(f"FB Error: {feed_res.get('error', {}).get('message', 'Failed to publish multi-photo post')}")

        if "instagram" in platforms:
            if not uploaded_media_urls:
                errors.append("IG Error: An attachment image/video is required for Instagram posts.")
            elif not INSTAGRAM_ID_KEY or INSTAGRAM_ID_KEY == "INSTAGRAM_BUSINESS_ACCOUNT_ID":
                errors.append("IG Error: Instagram Account ID is not configured.")
            elif len(uploaded_media_urls) == 1:
                # Single media container
                container_url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ID_KEY}/media"
                container_payload = {
                    "caption": message,
                    "access_token": IG_PAGE_ACCESS_TOKEN_KEY
                }
                if any(uploaded_media_urls[0].lower().endswith(ext) for ext in ['.mp4', '.mov', '.avi', '.webm']):
                    container_payload["media_type"] = "VIDEO"
                    container_payload["video_url"] = uploaded_media_urls[0]
                else:
                    container_payload["image_url"] = uploaded_media_urls[0]

                container_res = requests.post(container_url, data=container_payload).json()
                if "id" in container_res:
                    creation_id = container_res["id"]
                    publish_url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ID_KEY}/media_publish"
                    publish_payload = {
                        "creation_id": creation_id,
                        "access_token": IG_PAGE_ACCESS_TOKEN_KEY
                    }
                    pub_res = requests.post(publish_url, data=publish_payload).json()
                    if "id" in pub_res:
                        ig_post_id = pub_res["id"]
                    else:
                        errors.append(f"IG Error: {pub_res.get('error', {}).get('message', 'Failed to publish on Instagram')}")
                else:
                    errors.append(f"IG Error: {container_res.get('error', {}).get('message', 'Failed to create Instagram container')}")
            else:
                children_ids = []
                carousel_failed = False
                for url in uploaded_media_urls:
                    item_url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ID_KEY}/media"
                    item_payload = {
                        "is_carousel_item": "true",
                        "access_token": IG_PAGE_ACCESS_TOKEN_KEY
                    }
                    if any(url.lower().endswith(ext) for ext in ['.mp4', '.mov', '.avi', '.webm']):
                        item_payload["media_type"] = "VIDEO"
                        item_payload["video_url"] = url
                    else:
                        item_payload["image_url"] = url

                    item_res = requests.post(item_url, data=item_payload).json()
                    if "id" in item_res:
                        children_ids.append(item_res["id"])
                    else:
                        errors.append(f"IG Error (carousel item): {item_res.get('error', {}).get('message', 'Failed to create carousel item')}")
                        carousel_failed = True
                        break
                
                if not carousel_failed and children_ids:
                    carousel_url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ID_KEY}/media"
                    carousel_payload = {
                        "media_type": "CAROUSEL",
                        "children": json.dumps(children_ids),
                        "caption": message,
                        "access_token": IG_PAGE_ACCESS_TOKEN_KEY
                    }
                    carousel_res = requests.post(carousel_url, data=carousel_payload).json()
                    if "id" in carousel_res:
                        creation_id = carousel_res["id"]
                        publish_url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ID_KEY}/media_publish"
                        publish_payload = {
                            "creation_id": creation_id,
                            "access_token": IG_PAGE_ACCESS_TOKEN_KEY
                        }
                        pub_res = requests.post(publish_url, data=publish_payload).json()
                        if "id" in pub_res:
                            ig_post_id = pub_res["id"]
                        else:
                            errors.append(f"IG Error: {pub_res.get('error', {}).get('message', 'Failed to publish Instagram carousel')}")
                    else:
                        errors.append(f"IG Error: {carousel_res.get('error', {}).get('message', 'Failed to create Instagram carousel container')}")

        if fb_post_id or ig_post_id:
            db = firestore.client()
            post_id = str(uuid.uuid4())
            db.collection("PostContent").document(post_id).set({
                "PostId": fb_post_id,
                "IgPostId": ig_post_id,
                "message": message,
                "imageUrl": primary_image_url,
                "mediaUrls": uploaded_media_urls,
                "platforms": platforms,
                "createdAt": datetime.now(timezone.utc)
            })

            admin_uid, admin_name = get_admin_info()
            log_audit_event(
                action="Published Social Post",
                details=f"Admin {admin_name} posted content across platforms: {', '.join(platforms)}",
                user_id=admin_uid,
                user_name=admin_name
            )

            msg = "Content posted successfully!"
            if errors:
                msg += f" (Partial issues: {'; '.join(errors)})"

            return jsonify({
                "success": True,
                "message": msg,
                "PostId": fb_post_id,
                "IgPostId": ig_post_id
            })
        else:
            return jsonify({"success": False, "message": " | ".join(errors) if errors else "Failed to post content."}), 400

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# Adding Articles
@content_management_bp.route('/content-management/article/add', methods=['POST'])
@admin_required
def add_article():
    try:
        db = firestore.client()
        data = request.json
        article_id = str(uuid.uuid4())
        
        article_payload = {
            "title": data.get('title'),
            "category": data.get('category', 'Pet Care'),
            "author": data.get('author', 'Admin'),
            "readTime": data.get('readTime', '5 min read'),
            "imageUrl": data.get('imageUrl') or "https://via.placeholder.com/150",
            "content": data.get('content', ''),
            "createdAt": firestore.SERVER_TIMESTAMP
        }
        
        db.collection('Articles').document(article_id).set(article_payload)

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Published Article",
            details=f"Admin {admin_name} published article titled '{data.get('title')}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"success": True, "message": "Article published safely!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

# Editing Articles
@content_management_bp.route('/content-management/article/edit/<doc_id>', methods=['POST'])
@admin_required
def edit_article(doc_id):
    try:
        db = firestore.client()
        data = request.json
        update_payload = {
            "title": data.get('title'),
            "category": data.get('category'),
            "author": data.get('author'),
            "readTime": data.get('readTime'),
            "imageUrl": data.get('imageUrl'),
            "content": data.get('content'),
            "updatedAt": firestore.SERVER_TIMESTAMP
        }
        
        db.collection('Articles').document(doc_id).update(update_payload)

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated Article",
            details=f"Admin {admin_name} updated article titled '{data.get('title')}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"success": True, "message": "Article updated successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

# Adding Questions
@content_management_bp.route('/content-management/question/add/<target_collection>', methods=['POST'])
@admin_required
def add_question(target_collection):
    try:
        db = firestore.client()
        if target_collection not in ['PetMatchingQuestions', 'EligibilityQuestions']:
            return jsonify({"success": False, "message": "Invalid configuration target."}), 400

        data = request.json
        q_id = str(uuid.uuid4())
        raw_choices = data.get('choices', [])

        q_choices = []
        for c in raw_choices:
            if isinstance(c, dict) and c.get('text'):
                q_choices.append({
                    "text": c.get('text').strip(),
                    "status": c.get('status', 'correct').strip()
                })
        
        question_payload = {
            "questionText": data.get('questionText'),
            "type": data.get('type', 'default'), 
            "choices": q_choices,
            "articleId": data.get('articleId') if data.get('type') == 'random' else None,
            "createdAt": firestore.SERVER_TIMESTAMP
        }
        
        db.collection(target_collection).document(q_id).set(question_payload)

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Added Question",
            details=f"Admin {admin_name} added a question to {target_collection}: '{data.get('questionText')}'",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"success": True, "message": "Question successfully added!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400
    
# Editing Questions
@content_management_bp.route('/content-management/question/edit/<collection>/<doc_id>', methods=['POST'])
@admin_required
def edit_question(collection, doc_id):
    try:
        db = firestore.client()
        if collection not in ['PetMatchingQuestions', 'EligibilityQuestions']:
            return jsonify({"success": False, "message": "Invalid configuration target."}), 400
            
        data = request.json
        raw_choices = data.get('choices', [])

        q_choices = []
        for c in raw_choices:
            if isinstance(c, dict) and c.get('text'):
                q_choices.append({
                    "text": c.get('text').strip(),
                    "status": c.get('status', 'correct').strip()
                })
            elif isinstance(c, str) and c.strip(): 
                q_choices.append({
                    "text": c.strip(),
                    "status": "correct"
                })
                
        update_payload = {
            "questionText": data.get('questionText'),
            "type": data.get('type', 'default'),
            "choices": q_choices,
            "articleId": data.get('articleId') if data.get('type') == 'random' else None,
            "updatedAt": firestore.SERVER_TIMESTAMP
        }
        
        db.collection(collection).document(doc_id).update(update_payload)

        admin_uid, admin_name = get_admin_info()
        log_audit_event(
            action="Updated Question",
            details=f"Admin {admin_name} updated a question in {collection}",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"success": True, "message": "Question successfully updated!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

# Delete
@content_management_bp.route('/content-management/delete/<collection>/<doc_id>', methods=['DELETE'])
@admin_required
def delete_document(collection, doc_id):
    try:
        db = firestore.client()
        admin_uid, admin_name = get_admin_info()

        if collection in ['PostContent', 'SocialPosts']:
            post_ref = db.collection('PostContent').document(doc_id)
            post_doc = post_ref.get()

            if post_doc.exists:
                data = post_doc.to_dict()
                fb_post_id = data.get('PostId')
                ig_post_id = data.get('IgPostId')

                if fb_post_id:
                    fb_url = f"https://graph.facebook.com/v18.0/{fb_post_id}"
                    requests.delete(fb_url, params={"access_token": FB_PAGE_ACCESS_TOKEN_KEY})

                if ig_post_id:
                    ig_url = f"https://graph.facebook.com/v18.0/{ig_post_id}"
                    requests.delete(ig_url, params={"access_token": IG_PAGE_ACCESS_TOKEN_KEY})

                post_ref.delete()
                log_audit_event(
                    action="Deleted Social Post",
                    details=f"Admin {admin_name} deleted social post",
                    user_id=admin_uid,
                    user_name=admin_name
                )

                return jsonify({"success": True, "message": "Post deleted successfully from Platforms and Database."})
            else:
                return jsonify({"success": False, "message": "Post not found."}), 404
            
        if collection not in ['Articles', 'PetMatchingQuestions', 'EligibilityQuestions']:
            return jsonify({"success": False, "message": "Action Restricted"}), 400
            
        db.collection(collection).document(doc_id).delete()
        log_audit_event(
            action="Deleted Content Management Document",
            details=f"Admin {admin_name} deleted document from {collection}",
            user_id=admin_uid,
            user_name=admin_name
        )

        return jsonify({"success": True, "message": "Deleted successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400