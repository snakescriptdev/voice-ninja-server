import secrets
import string
import uuid
import bcrypt

def generate_client_id() -> str:
    """Generate a unique client ID prefixed with vn_."""
    return f"vn_{uuid.uuid4().hex}"

def generate_client_secret() -> str:
    """Generate a secure client secret."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(48))

def hash_secret(secret: str) -> str:
    """Hash the client secret using bcrypt."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(secret.encode("utf-8"), salt)
    return hashed.decode("utf-8")

def verify_secret(secret: str, hashed_secret: str) -> bool:
    """Verify a client secret against its hash."""
    return bcrypt.checkpw(secret.encode("utf-8"), hashed_secret.encode("utf-8"))
