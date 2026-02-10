import sys
import subprocess
import argparse
from sqlalchemy import create_engine, text
from app_v2.core.config import VoiceSettings

def run_command(command):
    """Run a shell command and print its output."""
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {' '.join(command)}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        return False

def makemigrations(message):
    """Create a new migration revision."""
    print(f"Creating migration: {message}")
    return run_command(["alembic", "revision", "--autogenerate", "-m", message])

def migrate():
    """Apply all pending migrations."""
    print("Applying migrations to head...")
    return run_command(["alembic", "upgrade", "head"])

def rollback(revision="-1"):
    """Rollback migrations."""
    print(f"Rolling back to {revision}...")
    return run_command(["alembic", "downgrade", revision])

def show():
    """Show current migration status."""
    return run_command(["alembic", "current"])

def history():
    """Show migration history."""
    return run_command(["alembic", "history"])

def reset_alembic_version():
    """Clear alembic_version table so you can fix 'Can't locate revision' errors."""
    try:
        engine = create_engine(VoiceSettings.DB_URL)
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM alembic_version"))
            conn.commit()
        print("Cleared alembic_version. Run makemigrations then migrate (or stamp head if DB is already in sync).")
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Database management script for Voice Ninja V2")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Makemigrations command
    make_parser = subparsers.add_parser("makemigrations", help="Create a new migration revision")
    make_parser.add_argument("-m", "--message", default="Auto migration", help="Migration message")

    # Migrate command
    subparsers.add_parser("migrate", help="Apply migrations to head")

    # Rollback command
    roll_parser = subparsers.add_parser("rollback", help="Rollback migrations")
    roll_parser.add_argument("revision", nargs="?", default="-1", help="Revision to rollback to")

    # Show status command
    subparsers.add_parser("show", help="Show current migration status")

    # History command
    subparsers.add_parser("history", help="Show migration history")

    # Reset alembic version (fix missing revision)
    subparsers.add_parser("reset_alembic_version", help="Clear alembic_version table (fix 'Can't locate revision')")

    args = parser.parse_args()

    if args.command == "makemigrations":
        makemigrations(args.message)
    elif args.command == "migrate":
        migrate()
    elif args.command == "rollback":
        rollback(args.revision)
    elif args.command == "show":
        show()
    elif args.command == "history":
        history()
    elif args.command == "reset_alembic_version":
        reset_alembic_version()
    else:
        parser.print_help()
