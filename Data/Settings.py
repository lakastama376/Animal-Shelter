from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash
from firebase_admin import firestore
from functools import wraps
from Data.audit import log_audit_event
import os

settings_bp = Blueprint('settings_bp', __name__)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        
        db = firestore.client()
        uid = session['user'].get('uid') or session['user'].get('user_id')
        user_doc = db.collection('Users').document(uid).get()
        
        if not user_doc.exists or user_doc.to_dict().get('role') != 'Admin':
            return jsonify({"status": "error", "message": "Access Denied. Admins only."}), 403
        return f(*args, **kwargs)
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

DEFAULT_PAYMENT_INFO = {
    "gcashNumber": os.getenv("DEFAULT_GCASH_NUMBER", ""),
    "mayaNumber": os.getenv("DEFAULT_MAYA_NUMBER", ""),
    "bpiAccount": os.getenv("DEFAULT_BPI_ACCOUNT", "")
}

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
            "showShelterPin": True,
            "acceptingCashDonations": True,
            "acceptingInKindDonations": True,
            **DEFAULT_PAYMENT_INFO
        }
        settings_ref.set(default_settings)
        return default_settings

@settings_bp.route('/settings', methods=['GET'])
@admin_required
def settings_view():
    try:
        db = firestore.client()
        settings_data = get_shelter_settings(db)
        pets = db.collection('Pets').where('status', '!=', 'Adopted').stream()
        dogs_count = 0
        cats_count = 0
        total_occupied = 0

        for pet in pets:
            p_data = pet.to_dict()
            total_occupied += 1
            pet_type = (p_data.get('type') or p_data.get('petType') or '').lower()
            if 'dog' in pet_type:
                dogs_count += 1
            elif 'cat' in pet_type:
                cats_count += 1

        max_cap = settings_data.get('maxCapacity', 50)
        percentage = round((total_occupied / max_cap) * 100, 1) if max_cap > 0 else 0

        legacy_donations = settings_data.get('acceptingDonations', True)
        cash_donations = settings_data.get('acceptingCashDonations', legacy_donations)
        inkind_donations = settings_data.get('acceptingInKindDonations', legacy_donations)

        capacity_info = {
            "maxCapacity": max_cap,
            "occupied": total_occupied,
            "dogs": dogs_count,
            "cats": cats_count,
            "percentage": min(percentage, 100),
            "isAccepting": settings_data.get('isAccepting', True),
            "enforceBoundary": settings_data.get('enforceBoundary', True),
            "showShelterPin": settings_data.get('showShelterPin', True),
            "acceptingCashDonations": cash_donations,
            "acceptingInKindDonations": inkind_donations,
            "gcashNumber": settings_data.get('gcashNumber', DEFAULT_PAYMENT_INFO["gcashNumber"]),
            "mayaNumber": settings_data.get('mayaNumber', DEFAULT_PAYMENT_INFO["mayaNumber"]),
            "bpiAccount": settings_data.get('bpiAccount', DEFAULT_PAYMENT_INFO["bpiAccount"])
        }

        return render_template('Settings.html', config=capacity_info)
    except Exception as e:
        print(f"Error loading settings: {e}")
        flash("Failed to load shelter settings.", "error")
        return render_template('Settings.html', config={
            "maxCapacity": 50, "occupied": 0, "dogs": 0, "cats": 0, "percentage": 0,
            "isAccepting": True, "enforceBoundary": True, "showShelterPin": True,
            "acceptingCashDonations": True, "acceptingInKindDonations": True,
            **DEFAULT_PAYMENT_INFO
        })

@settings_bp.route('/settings/update', methods=['POST'])
@admin_required
def update_settings():
    try:
        db = firestore.client()
        data = request.json or {}
        new_max_capacity = int(data.get('maxCapacity', 50))
        new_is_accepting = bool(data.get('isAccepting', True))
        new_enforce_boundary = bool(data.get('enforceBoundary', True))
        new_show_shelter_pin = bool(data.get('showShelterPin', True))
        new_accepting_cash = bool(data.get('acceptingCashDonations', True))
        new_accepting_inkind = bool(data.get('acceptingInKindDonations', True))

        new_gcash = str(data.get('gcashNumber', DEFAULT_PAYMENT_INFO["gcashNumber"])).strip()
        new_maya = str(data.get('mayaNumber', DEFAULT_PAYMENT_INFO["mayaNumber"])).strip()
        new_bpi = str(data.get('bpiAccount', DEFAULT_PAYMENT_INFO["bpiAccount"])).strip()

        if new_max_capacity < 1:
            return jsonify({"status": "error", "message": "Capacity must be at least 1."}), 400

        if not new_gcash or not new_maya or not new_bpi:
            return jsonify({"status": "error", "message": "GCash number, Maya number, and BPI account are required."}), 400

        settings_ref = db.collection('Settings').document('shelter_config')
        old_settings = settings_ref.get().to_dict() or {}
        old_max_capacity = old_settings.get('maxCapacity', 50)
        old_is_accepting = old_settings.get('isAccepting', True)
        old_enforce_boundary = old_settings.get('enforceBoundary', True)
        old_show_shelter_pin = old_settings.get('showShelterPin', True)

        legacy_old = old_settings.get('acceptingDonations', True)
        old_accepting_cash = old_settings.get('acceptingCashDonations', legacy_old)
        old_accepting_inkind = old_settings.get('acceptingInKindDonations', legacy_old)

        old_gcash = old_settings.get('gcashNumber', DEFAULT_PAYMENT_INFO["gcashNumber"])
        old_maya = old_settings.get('mayaNumber', DEFAULT_PAYMENT_INFO["mayaNumber"])
        old_bpi = old_settings.get('bpiAccount', DEFAULT_PAYMENT_INFO["bpiAccount"])

        settings_ref.set({
            "maxCapacity": new_max_capacity,
            "isAccepting": new_is_accepting,
            "enforceBoundary": new_enforce_boundary,
            "showShelterPin": new_show_shelter_pin,
            "acceptingCashDonations": new_accepting_cash,
            "acceptingInKindDonations": new_accepting_inkind,
            "gcashNumber": new_gcash,
            "mayaNumber": new_maya,
            "bpiAccount": new_bpi,
            "updatedAt": firestore.SERVER_TIMESTAMP
        }, merge=True)

        admin_uid, admin_name = get_admin_info()
        if new_max_capacity != old_max_capacity:
            log_audit_event(
                action="Updated Shelter Capacity",
                details=f"Admin {admin_name} changed shelter max capacity from {old_max_capacity} to {new_max_capacity}",
                user_id=admin_uid,
                user_name=admin_name
            )

        if new_is_accepting != old_is_accepting:
            status_text = "Accepting Intakes" if new_is_accepting else "Paused Intakes"
            log_audit_event(
                action="Updated Intake Status",
                details=f"Admin {admin_name} updated intake acceptance status to: {status_text}",
                user_id=admin_uid,
                user_name=admin_name
            )

        if new_enforce_boundary != old_enforce_boundary:
            boundary_text = "Enforced" if new_enforce_boundary else "Not Enforced"
            log_audit_event(
                action="Updated Boundary Enforcement",
                details=f"Admin {admin_name} updated boundary enforcement setting to: {boundary_text}",
                user_id=admin_uid,
                user_name=admin_name
            )

        if new_show_shelter_pin != old_show_shelter_pin:
            pin_text = "Visible" if new_show_shelter_pin else "Hidden"
            log_audit_event(
                action="Updated Shelter Pin Visibility",
                details=f"Admin {admin_name} updated shelter map pin visibility to: {pin_text}",
                user_id=admin_uid,
                user_name=admin_name
            )

        if new_accepting_cash != old_accepting_cash:
            cash_text = "Accepting Cash Donations" if new_accepting_cash else "Paused Cash Donations"
            log_audit_event(
                action="Updated Cash Donation Status",
                details=f"Admin {admin_name} updated cash donation status to: {cash_text}",
                user_id=admin_uid,
                user_name=admin_name
            )

        if new_accepting_inkind != old_accepting_inkind:
            inkind_text = "Accepting In-Kind Donations" if new_accepting_inkind else "Paused In-Kind Donations"
            log_audit_event(
                action="Updated In-Kind Donation Status",
                details=f"Admin {admin_name} updated in-kind donation status to: {inkind_text}",
                user_id=admin_uid,
                user_name=admin_name
            )

        wallet_changes = []
        if new_gcash != old_gcash:
            wallet_changes.append(f"GCash: '{old_gcash}' -> '{new_gcash}'")
        if new_maya != old_maya:
            wallet_changes.append(f"Maya: '{old_maya}' -> '{new_maya}'")
        if new_bpi != old_bpi:
            wallet_changes.append(f"BPI: '{old_bpi}' -> '{new_bpi}'")

        if wallet_changes:
            log_audit_event(
                action="Updated Donation Payment Accounts",
                details=f"Admin {admin_name} updated payment details ({', '.join(wallet_changes)})",
                user_id=admin_uid,
                user_name=admin_name
            )

        return jsonify({"status": "success", "message": "Shelter settings updated successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500