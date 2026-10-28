from datetime import datetime
from firebase_admin import firestore

def log_audit_event(action, details, user_id=None, user_name="User"):
    
    try:
        db = firestore.client()
        db.collection('AuditLogs').add({
            'action': action,
            'details': details,
            'userId': user_id,
            'userName': user_name,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Failed to record audit log: {e}")