import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
from django.conf import settings


def _initialize():
    if not firebase_admin._apps:
        cred_path = getattr(settings, 'FIREBASE_CREDENTIALS_PATH', '')
        if cred_path:
            cred = credentials.Certificate(cred_path)
        else:
            cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)


def verify_id_token(id_token: str) -> dict:
    """Xác thực Firebase ID token, trả về decoded payload."""
    _initialize()
    return firebase_auth.verify_id_token(id_token)
