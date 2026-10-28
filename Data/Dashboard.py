from flask import Blueprint, render_template, session, redirect, request, url_for
from firebase_admin import firestore
import functools
from flask import jsonify
from datetime import datetime, timedelta

dashboard_bp = Blueprint('dashboard_bp', __name__)

def auth_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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


@dashboard_bp.route('/dashboard', methods=['GET'])
@auth_required
def dashboard_view():
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    user_doc = db.collection('Users').document(uid).get()
    user_info = user_doc.to_dict() if user_doc.exists else {}
    role = user_info.get('role', 'user').lower()
    is_admin = role == 'admin' or user_data.get('role') == 'admin'

    full_name = (
        user_info.get('fullName') or 
        f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip() or 
        user_data.get('displayName') or 
        "User"
    )

    # Admin Dashboard
    if is_admin:
        current_date = datetime.now()
        current_year = current_date.year
        selected_year = request.args.get('year', type=int) or datetime.now().year
        selected_range = request.args.get('range', default='H1' if datetime.now().month <= 6 else 'H2')
        current_date_str = current_date.strftime("%B %d, %Y")
        shelter_config = get_shelter_settings(db)
        max_capacity = shelter_config.get('maxCapacity', 50)
        is_accepting = shelter_config.get('isAccepting', True)
        all_pets_docs = list(db.collection('Pets').stream())
        total_animals = len(all_pets_docs)

        animals_added_month = 0
        for p in all_pets_docs:
            p_data = p.to_dict()
            created_at = p_data.get('createdAt')
            rescue_date = p_data.get('rescueDate')

            if created_at and hasattr(created_at, 'year') and hasattr(created_at, 'month'):
                if created_at.year == current_year and created_at.month == current_date.month:
                    animals_added_month += 1
            elif rescue_date:
                try:
                    parsed_date = datetime.strptime(rescue_date, '%Y-%m-%d')
                    if parsed_date.year == current_year and parsed_date.month == current_date.month:
                        animals_added_month += 1
                except (ValueError, TypeError):
                    pass

        current_capacity = sum(1 for p in all_pets_docs if p.to_dict().get('status') in ['Available', 'Pending'])
        capacity_pct = round((current_capacity / max_capacity) * 100) if max_capacity > 0 else 0
        all_apps_docs = list(db.collection('Applications').stream())
        total_applications = len(all_apps_docs)
        pending_apps = sum(1 for a in all_apps_docs if a.to_dict().get('status') in ['Pending', 'Under Review'])

        total_adoptions = 0
        finalized_adoptions = 0
        adoptions_this_month = 0
        for app in all_apps_docs:
            app_data = app.to_dict()
            status = app_data.get('status')
            
            if status in ['Adopted', 'Finalized']:
                total_adoptions += 1
                if status == 'Finalized':
                    finalized_adoptions += 1

                date_val = app_data.get('updatedAt') or app_data.get('submittedAt')
                if date_val:
                    try:
                        if isinstance(date_val, str):
                            date_obj = datetime.fromisoformat(date_val.replace(" ", "T"))
                        elif hasattr(date_val, 'to_datetime'):
                            date_obj = date_val.to_datetime()
                        else:
                            date_obj = date_val

                        if date_obj.year == current_year and date_obj.month == current_date.month:
                            adoptions_this_month += 1
                    except (ValueError, TypeError, AttributeError):
                        pass

        donations_docs = list(db.collection('Donations').stream())
        total_donations = 0.0
        donations_month = 0.0
        for don in donations_docs:
            d_data = don.to_dict()
            if d_data.get('type') == 'cash' and d_data.get('status') == 'succeeded':
                amount = float(d_data.get('amount', 0))
                total_donations += amount
                created_at = d_data.get('createdAt')
                if created_at and hasattr(created_at, 'year') and hasattr(created_at, 'month'):
                    if created_at.year == current_year and created_at.month == current_date.month:
                        donations_month += amount

        formatted_donations = f"₱{total_donations / 1000:.1f}k" if total_donations >= 1000 else f"₱{total_donations:,.0f}"
        formatted_donations_month = f"₱{donations_month:,.0f}"

        rescues_docs = list(db.collection('RescueReports').stream())
        pending_rescues = 0
        urgent_rescues = 0
        for r in rescues_docs:
            r_data = r.to_dict()
            status_str = str(r_data.get('status', 'Pending')).strip().lower()

            if status_str in ['pending', 'under review']:
                pending_rescues += 1
                
                condition = str(
                    r_data.get('animalCondition') or 
                    r_data.get('condition') or 
                    r_data.get('urgency') or ''
                ).strip().lower()

                if condition in ['in danger', 'injured']:
                    urgent_rescues += 1

        adopters_docs = list(db.collection('Users').where('role', '==', 'Adopter').stream())
        adopters_count = len(adopters_docs) if adopters_docs else 0 
        active_adopters_count = sum(1 for doc in adopters_docs if doc.to_dict().get('status', 'active') == 'active')

        metrics = {
            'total_animals': total_animals,
            'animals_added_month': animals_added_month,
            'applications': total_applications,
            'pending_apps': pending_apps,
            'adoptions_month': total_adoptions,
            'finalized_adoptions': finalized_adoptions,
            'adoptions_this_month': adoptions_this_month,
            'donations': formatted_donations if total_donations > 0 else "₱0.0k",
            'donations_month': formatted_donations_month,
            'rescue_reports': pending_rescues,
            'urgent_rescues': urgent_rescues,
            'adopters': adopters_count,
            'active': active_adopters_count,
            'current_capacity': current_capacity,
            'max_capacity': max_capacity,
            'capacity_pct': capacity_pct,
            'is_accepting': is_accepting
        }

        yearly_chart_data = {}
        def ensure_year(yr):
            if yr not in yearly_chart_data:
                yearly_chart_data[yr] = {
                    'adoptions': [0] * 12,
                    'rescues': [0] * 12,
                    'donations': [0.0] * 12
                }

        ensure_year(current_year)
        for app in all_apps_docs:
            app_data = app.to_dict()
            if app_data.get('status') in ['Adopted', 'Finalized']:
                submitted_at = app_data.get('submittedAt')
                if submitted_at and hasattr(submitted_at, 'year') and hasattr(submitted_at, 'month'):
                    ensure_year(submitted_at.year)
                    yearly_chart_data[submitted_at.year]['adoptions'][submitted_at.month - 1] += 1

        for rescue in rescues_docs:
            r_data = rescue.to_dict()
            status_str = str(r_data.get('status', '')).strip().lower()
            if status_str == 'resolved':
                rescue_date = r_data.get('updatedAt') or r_data.get('submittedAt') or r_data.get('createdAt')
                if rescue_date and hasattr(rescue_date, 'year') and hasattr(rescue_date, 'month'):
                    ensure_year(rescue_date.year)
                    yearly_chart_data[rescue_date.year]['rescues'][rescue_date.month - 1] += 1

        for don in donations_docs:
            d_data = don.to_dict()
            if d_data.get('type') == 'cash' and d_data.get('status') == 'succeeded':
                created_at = d_data.get('createdAt')
                amount = float(d_data.get('amount', 0))
                if created_at and hasattr(created_at, 'year') and hasattr(created_at, 'month'):
                    ensure_year(created_at.year)
                    yearly_chart_data[created_at.year]['donations'][created_at.month - 1] += amount / 1000.0

        available_years = sorted(list(yearly_chart_data.keys()), reverse=True)

        dogs_count = 0
        cats_count = 0
        for p in all_pets_docs:
            p_data = p.to_dict()
            if p_data.get('status') not in ['Adopted', 'Finalized']:
                species = str(p_data.get('species') or p_data.get('type') or p_data.get('petType') or '').lower()
                if 'dog' in species:
                    dogs_count += 1
                elif 'cat' in species:
                    cats_count += 1

        species_data = {
            'dogs': dogs_count,
            'cats': cats_count
        }

        audit_logs = []
        try:
            logs_docs = db.collection('AuditLogs').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50).stream()
            for log in logs_docs:
                l_data = log.to_dict()
                ts = l_data.get('timestamp') or l_data.get('createdAt')
                time_str = ''
                
                if ts:
                    if hasattr(ts, 'strftime'):
                        local_dt = ts + timedelta(hours=8)
                        time_str = local_dt.strftime('%b %d, %I:%M %p')
                    elif isinstance(ts, str):
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            local_dt = dt + timedelta(hours=8)
                            time_str = local_dt.strftime('%b %d, %I:%M %p')
                        except (ValueError, TypeError):
                            time_str = ts
                
                audit_logs.append({
                    'id': log.id,
                    'action': l_data.get('action') or l_data.get('event', 'Activity'),
                    'details': l_data.get('details') or l_data.get('description', ''),
                    'timestamp': time_str,
                    'is_default': l_data.get('is_default', False) or l_data.get('isSystem', False)
                })
        except Exception as e:
            print(f"Error fetching audit logs: {e}")

        return render_template(
            'Dashboard.html',
            is_admin=True,
            current_date_str=current_date_str,
            full_name=full_name,
            metrics=metrics,
            yearly_chart_data=yearly_chart_data,
            available_years=available_years,
            current_year=selected_year,
            current_range=selected_range,
            species_data=species_data,
            audit_logs=audit_logs
        )
    
    # Adopters Dashboard
    else:
        wishlist_ids = user_info.get('wishlist', [])
        pets_query = db.collection('Pets').where('status', 'in', ['Available']).stream()
        all_pets = []

        for doc in pets_query:
            pet_data = doc.to_dict()
            pet_data['id'] = doc.id

            images = pet_data.get('images') or pet_data.get('imageUrls') or []
            if isinstance(images, str):
                images = [images]
            if not images:
                single_photo = pet_data.get('photoUrl') or pet_data.get('imageUrl')
                if single_photo:
                    images = [single_photo]
                else:
                    images = ['/static/images/placeholder.png']

            pet_data['images'] = images
            pet_data['petName'] = pet_data.get('petName') or pet_data.get('name') or 'Unnamed Pet'
            pet_data['species'] = (pet_data.get('species') or pet_data.get('type') or '').lower()
            pet_data['is_favorited'] = doc.id in wishlist_ids
            all_pets.append(pet_data)

        all_pets.sort(key=lambda x: str(x.get('createdAt', '')), reverse=True)
        dogs = [p for p in all_pets if 'dog' in p['species']]
        cats = [p for p in all_pets if 'cat' in p['species']]
        other_pets = [p for p in all_pets if p not in dogs and p not in cats]
        
        featured_pets = dogs[:5] + cats[:5]
        if len(featured_pets) < 10:
            remaining_needed = 10 - len(featured_pets)
            unused_pets = [p for p in (all_pets + other_pets) if p not in featured_pets]
            featured_pets.extend(unused_pets[:remaining_needed])

        wishlist_count = len(wishlist_ids)
        apps_query = db.collection('Applications').where('userId', '==', uid).stream()
        applications_list = []
        for doc in apps_query:
            app_data = doc.to_dict()
            app_data['id'] = doc.id
            pet_id = app_data.get('petId')

            if pet_id:
                pet_doc = db.collection('Pets').document(pet_id).get()
                if pet_doc.exists:
                    p_data = pet_doc.to_dict()
                    app_data['pet_name'] = p_data.get('name') or p_data.get('petName', 'Unknown Pet')
                    app_data['pet_photo'] = (p_data.get('images') or [None])[0] or p_data.get('photoUrl') or p_data.get('imageUrl', '')
                else:
                    app_data['pet_name'] = app_data.get('petName', 'Unknown Pet')
                    app_data['pet_photo'] = ''
            else:
                app_data['pet_name'] = app_data.get('petName', 'Unknown Pet')
                app_data['pet_photo'] = ''

            applications_list.append(app_data)

        rescue_query = db.collection('RescueReports').where('reporterUid', '==', uid).stream()
        rescue_count = sum(1 for _ in rescue_query)

        total_donations = 0
        donations_query = db.collection('Donations').where('uid', '==', uid).stream()
        for don in donations_query:
            don_data = don.to_dict()
            if don_data.get('type') == 'cash' and don_data.get('status') == 'succeeded':
                total_donations += float(don_data.get('amount', 0))

        formatted_donations = f"₱{total_donations:,.0f}"
        convs_query = db.collection('Conversations').where('participants', 'array_contains', uid).stream()
        conversations = []
        unread_messages_count = 0

        for doc in convs_query:
            c_data = doc.to_dict()
            c_data['id'] = doc.id
            if c_data.get('unread'):
                unread_messages_count += 1

            updated_at = c_data.get('updatedAt')
            if updated_at and hasattr(updated_at, 'strftime'):
                c_data['formatted_date'] = updated_at.strftime('%B %d, %Y')
            else:
                c_data['formatted_date'] = ''
                
            conversations.append(c_data)

        conversations.sort(key=lambda x: x.get('updatedAt', 0), reverse=True)
        metrics = {
            'applications': len(applications_list),
            'wishlist': wishlist_count,
            'donations': formatted_donations,
            'rescue_reports': rescue_count,
            'unread_messages': unread_messages_count
        }

        return render_template(
            'Dashboard.html',
            is_admin=False,
            full_name=full_name,
            metrics=metrics,
            featured_pets=featured_pets,
            recent_applications=applications_list[:3],
            recent_conversations=conversations[:2]
        )

# Delete Logs
@dashboard_bp.route('/api/audit-logs/<log_id>', methods=['DELETE'])
@auth_required
def delete_audit_log(log_id):
    db = firestore.client()
    user_data = session.get('user', {})
    uid = user_data.get('uid') or user_data.get('user_id')
    user_doc = db.collection('Users').document(uid).get()
    user_info = user_doc.to_dict() if user_doc.exists else {}
    role = user_info.get('role', 'user').lower()
    is_admin = role == 'admin' or user_data.get('role') == 'admin'

    if not is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    try:
        doc_ref = db.collection('AuditLogs').document(log_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return jsonify({'success': False, 'message': 'Log entry not found'}), 404
        
        log_data = doc.to_dict()
        if log_data.get('is_default') or log_data.get('isSystem'):
            return jsonify({'success': False, 'message': 'Default system logs cannot be deleted'}), 400

        doc_ref.delete()
        return jsonify({'success': True, 'message': 'Audit log deleted successfully'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500