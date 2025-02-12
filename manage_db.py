import sys
import subprocess

def run_alembic_command(*args):
    """Run Alembic command and print output."""
    command = ["alembic"] + list(args)
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

commands = {
    "makemigrations": ["revision", "--autogenerate", "-m", "Auto migration"],
    "migrate": ["upgrade", "head"],
    "rollback": ["downgrade", "-1"],
    "showmigrations": ["current"],
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python manage.py <command>")
        print("Available commands: makemigrations, migrate, rollback, showmigrations")
        sys.exit(1)

    command = sys.argv[1]
    if command in commands:
        run_alembic_command(*commands[command])
    else:
        print(f"Unknown command: {command}")
        print("Available commands: makemigrations, migrate, rollback, showmigrations")
