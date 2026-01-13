# Voice Ninja V2

This repository contains the refactored version of Voice Ninja (app_v2). The previous codebase has been moved to the `archive/` directory for reference.

## Getting Started

### 1. Prerequisites
- Python 3.12+
- Virtual environment (recommended)

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Configuration
Ensure your `.env` file is configured with the necessary environment variables. See `archive/.env.example` for reference if needed.

Required variables for app_v2:
- `DB_URL`: Database connection string
- `SECRET_KEY`: For session and JWT signing
- `GOOGLE_CLIENT_ID`: For Google Auth
- `GOOGLE_CLIENT_SECRET`: For Google Auth

### 4. Running the Application
```bash
uvicorn main:app --reload
```

The API documentation will be available at `http://localhost:8000/docs`.

## Database Migrations
Manage database schema changes with Alembic:
```bash
# Create a new migration revision
python manage_db.py makemigrations -m "description of changes"

# Apply pending migrations
python manage_db.py migrate

# View migration status
python manage_db.py show
```

## Project Structure

- `app_v2/`: Contains the new refactored API endpoints, schemas, and utilities.
- `main.py`: Entry point for the FastAPI application.
- `archive/`: Contains the legacy codebase and assets.
- `requirements.txt`: Python dependencies for app_v2.
