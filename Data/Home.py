import os
from flask import Blueprint, render_template, session, jsonify, url_for, Response
from firebase_admin import firestore
from itsdangerous import URLSafeTimedSerializer, BadSignature
import requests

home_bp = Blueprint('home', __name__)


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
    return url_for('home_bp.public_media_proxy', file_id=token, _external=True)

@home_bp.route('/home/media-proxy/<path:file_id>')
def public_media_proxy(file_id):
    try:
        real_file_id = deobfuscate_file_id(file_id)
        if not real_file_id:
            return jsonify({"error": "Not found"}), 404

        bucket_id = os.getenv('APPWRITE_BUCKET_ID')
        project_id = os.getenv('APPWRITE_PROJECT_ID')
        api_key = os.getenv('APPWRITE_API_KEY')
        endpoint = os.getenv('APPWRITE_ENDPOINT')
        appwrite_url = f"{endpoint}/storage/buckets/{bucket_id}/files/{real_file_id}/view?project={project_id}"
        
        headers = {
            'X-Appwrite-Key': api_key,
            'X-Appwrite-Project': project_id
        }
        
        resp = requests.get(appwrite_url, headers=headers, stream=True)
        if resp.status_code == 200:
            return Response(resp.iter_content(chunk_size=1024), content_type=resp.headers.get('content-type', 'image/jpeg'))
        else:
            return jsonify({"error": "File not found or access denied"}), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@home_bp.route('/', methods=['GET'])
def home():
    featured_pets = []
    donors_list = []
    articles_list = []
    posts_list = []
    is_logged_in = False

    try:
        db = firestore.client()
        user_data = session.get('user') or {}
        uid = user_data.get('uid') or user_data.get('user_id') or session.get('user_id') or session.get('uid')
        is_logged_in = True if uid else False
        wishlist = []

        if is_logged_in and uid:
            try:
                user_doc = db.collection('Users').document(uid).get()
                if user_doc.exists:
                    wishlist = user_doc.to_dict().get('wishlist', [])
            except Exception as user_err:
                print(f"User wishlist fetch error: {user_err}")
                
        try:
            pets_ref = db.collection('Pets')
            pets_query = pets_ref.where('status', 'in', ['Available']).limit(10)
            
            all_pets = []
            for d in pets_query.stream():
                p_data = d.to_dict()
                p_data['id'] = d.id
                
                raw_images = p_data.get('images')
                if raw_images and isinstance(raw_images, list):
                    p_data['images'] = [get_file_url(img) for img in raw_images if img]
                elif p_data.get('photoUrl'):
                    p_data['images'] = [get_file_url(p_data.get('photoUrl'))]
                else:
                    p_data['images'] = ['/static/images/placeholder.png']
                    
                p_data['is_favorited'] = d.id in wishlist
                all_pets.append(p_data)

            all_pets.sort(key=lambda x: str(x.get('createdAt', '')), reverse=True)
            recent_pets = all_pets[:5]
            older_pets = all_pets[5:10]
            featured_pets = recent_pets + older_pets
        except Exception as pet_err:
            print(f"Error fetching pets: {pet_err}")

        # Fetch Donors
        try:
            donors_ref = list(db.collection('DonorWall').stream())
            temp_donors = []
            for doc in donors_ref:
                donor_data = doc.to_dict()
                created_at = donor_data.get('createdAt')
                formatted_date = donor_data.get('date') or (created_at.strftime("%B %d, %Y") if created_at and hasattr(created_at, 'strftime') else "N/A")
                raw_photo = donor_data.get('photoURL', '')
                resolved_photo = get_file_url(raw_photo) if raw_photo else ''
                temp_donors.append({
                    'id': doc.id,
                    'donorName': donor_data.get('donorName', 'Anonymous Donor'),
                    'badge': donor_data.get('badge', 'Supporter'),
                    'photoURL': resolved_photo,
                    'description': donor_data.get('description', '') or donor_data.get('notes', ''),
                    'media': donor_data.get('media', []),
                    'date': formatted_date,
                    'raw_date': created_at
                })
            
            temp_donors.sort(key=lambda x: x['raw_date'] if x['raw_date'] is not None else '', reverse=True)
            donors_list = temp_donors[:3]
        except Exception as donor_err:
            print(f"Error fetching donors: {donor_err}")

        # Fetch Articles
        try:
            articles_ref = db.collection('Articles').limit(10)
            for doc in articles_ref.stream():
                art_data = doc.to_dict()
                art_data['id'] = doc.id
                articles_list.append(art_data)
            
            articles_list.sort(key=lambda x: str(x.get('createdAt', '')), reverse=True)
            articles_list = articles_list[:3]
        except Exception as art_err:
            print(f"Error fetching articles: {art_err}")

        # Fetch Post Content
        try:
            posts_ref = db.collection('PostContent').order_by('createdAt', direction=firestore.Query.DESCENDING).limit(15)
            
            for doc in posts_ref.stream():
                post_data = doc.to_dict()
                media_urls = post_data.get('mediaUrls', [])
                raw_image = post_data.get('imageUrl') or (media_urls[0] if media_urls else None)
                image = raw_image if raw_image else ''
                created_at = post_data.get('createdAt')
                formatted_date = "Recent"

                if created_at and hasattr(created_at, 'strftime'):
                    formatted_date = created_at.strftime("%b %d, %Y")
                    
                platforms = post_data.get('platforms', [])
                fb_default_url = 'https://web.facebook.com/people/Test-Purposes/61594027235322/'
                ig_default_url = 'https://www.instagram.com/test.dev000911/'
                fb_url = post_data.get('fbPostUrl') or post_data.get('postUrl') or (fb_default_url if 'facebook' in platforms else '')
                ig_url = post_data.get('igPostUrl') or (ig_default_url if 'instagram' in platforms else '')

                if 'facebook' in platforms and 'instagram' in platforms:
                    source = 'Facebook & Instagram'
                elif 'facebook' in platforms:
                    source = 'facebook.com'
                elif 'instagram' in platforms:
                    source = 'instagram.com'
                else:
                    source = 'Social Media'

                posts_list.append({
                    'id': doc.id,
                    'caption': post_data.get('message', ''),
                    'image': image,
                    'mediaUrls': media_urls, 
                    'has_image': bool(image or media_urls),
                    'source': source,
                    'date': formatted_date,
                    'platforms': platforms,
                    'fb_url': fb_url if 'facebook' in platforms else '',
                    'ig_url': ig_url if 'instagram' in platforms else ''
                })

        except Exception as post_err:
            print(f"Error fetching shelter posts: {post_err}")

    except Exception as e:
        print(f"Critical error on Home Route: {e}")

    return render_template(
        'Home.html', 
        pets=featured_pets, 
        donors=donors_list, 
        articles=articles_list, 
        posts=posts_list,
        is_logged_in=is_logged_in
    )