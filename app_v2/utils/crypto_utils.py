from cryptography.fernet import Fernet
import base64
import os
from app_v2.core.config import VoiceSettings

# We use the SECRET_KEY for encryption. 
# Fernet keys must be 32 url-safe base64-encoded bytes.
def get_encryption_key():
    secret = VoiceSettings.SECRET_KEY
    # Ensure it's 32 bytes by padding or hashing if needed, 
    # but for simplicity we'll assume it's set correctly or derive it.
    # A more robust way would be using PBKDF2 to derive a key from the secret.
    key = base64.urlsafe_b64encode(secret.ljust(32)[:32].encode())
    return key

def encrypt_data(data: str) -> str:
    """Encrypt a string using Fernet symmetric encryption."""
    if not data:
        raise ValueError("Data cannot be empty")
    f = Fernet(get_encryption_key())
    return f.encrypt(data.encode()).decode()

def decrypt_data(token: str) -> str:
    """Decrypt a Fernet-encrypted token."""
    if not token:
        raise ValueError("Token cannot be empty")
    try:
        f = Fernet(get_encryption_key())
        return f.decrypt(token.encode()).decode()
    except Exception as e:
        # If decryption fails (e.g. key changed), return empty or handle error
        raise str(e)
