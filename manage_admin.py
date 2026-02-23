import argparse
import sys
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app_v2.databases.models import UnifiedAuthModel, Base
from app_v2.core.config import VoiceSettings

# Setup Database Session
engine = create_engine(VoiceSettings.DB_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        return db
    finally:
        pass # Session will be closed manually in functions

def create_admin_user(email, name, phone):
    """Creates a new admin user or promotes an existing one."""
    session = get_db()
    try:
        user = session.query(UnifiedAuthModel).filter(UnifiedAuthModel.email == email).first()
        
        if user:
            print(f"User with email {email} already exists. Promoting to Admin...")
            user.is_admin = True
            user.is_verified = True
            user.name = name or user.name
            user.phone = phone or user.phone
        else:
            print(f"Creating new Admin user: {email}")
            user = UnifiedAuthModel(
                email=email,
                name=name,
                phone=phone,
                is_admin=True,
                is_verified=True,
                has_otp_auth=True
            )
            session.add(user)
        
        session.commit()
        print(f"Successfully created/updated Admin: {user.email}")
    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
    finally:
        session.close()

def update_admin_status(email, is_admin):
    """Updates the admin status of a user."""
    session = get_db()
    try:
        user = session.query(UnifiedAuthModel).filter(UnifiedAuthModel.email == email).first()
        if not user:
            print(f"User with email {email} not found.")
            return

        user.is_admin = is_admin
        session.commit()
        print(f"User {email} admin status set to: {is_admin}")
    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
    finally:
        session.close()

def list_admins():
    """Lists all admin users."""
    session = get_db()
    try:
        admins = session.query(UnifiedAuthModel).filter(UnifiedAuthModel.is_admin == True).all()
        print("\nExisting Administrators:")
        print("-" * 50)
        for admin in admins:
            print(f"ID: {admin.id} | Email: {admin.email} | Name: {admin.name}")
        print("-" * 50)
    finally:
        session.close()

def main():
    parser = argparse.ArgumentParser(description="Voice Ninja Admin Management Utility")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new admin or promote existing user")
    create_parser.add_argument("--email", required=True, help="Email of the admin")
    create_parser.add_argument("--name", help="Name of the admin")
    create_parser.add_argument("--phone", help="Phone number of the admin")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update admin status of a user")
    update_parser.add_argument("--email", required=True, help="Email of the user")
    update_parser.add_argument("--admin", type=lambda x: (str(x).lower() == 'true'), required=True, help="Set admin status (true/false)")

    # List command
    subparsers.add_parser("list", help="List all administrators")

    args = parser.parse_args()

    if args.command == "create":
        create_admin_user(args.email, args.name, args.phone)
    elif args.command == "update":
        update_admin_status(args.email, args.admin)
    elif args.command == "list":
        list_admins()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
