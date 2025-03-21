# AI Voice Assistant Setup Guide

## System Requirements

### Python Version
- Python 3.12.7 or higher is required
- Check your Python version:
```bash
python --version
```

### Operating System
- Linux, macOS, or Windows
- Recommended: Ubuntu 22.04 or higher

## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/snakescriptdev/voice_ninja.git
cd voice_ninja
```

### 2. Create and Activate Virtual Environment
```bash
python -m venv venv
source venv/bin/activate # Linux/Mac
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
```bash
cp .env.example .env
```


```.env
# Google API Configuration
GOOGLE_API_KEY=your_google_api_key_here

# Cal API Configuration
CAL_API_KEY=your_cal_api_key_here

# Database Configuration
DB_URL=your_database_url_here

# Mail Configuration
MAIL_USERNAME=your_mail_username_here
MAIL_PASSWORD=your_mail_password_here
MAIL_PORT=your_mail_port_here
MAIL_SERVER=your_mail_server_here
MAIL_TLS=your_mail_tls_here
MAIL_SSL=your_mail_ssl_here
MAIL_FROM=your_mail_from_here

# Twilio Configuration
TWILIO_ACCOUNT_SID=your_twilio_account_sid_here
TWILIO_AUTH_TOKEN=your_twilio_auth_token_here
TWILIO_PHONE_NUMBER=your_twilio_phone_number_here

# Razorpay Configuration
RAZOR_KEY_ID=your_razor_key_id_here
RAZOR_KEY_SECRET=your_razor_key_secret_here

# Domain Name
DOMAIN_NAME=your_domain_name_here

# Host
HOST=your_host_here

```

### Required Environment Variables
| Variable | Description | Required |
|----------|-------------|----------|
| GOOGLE_API_KEY | API key for Google services integration | Yes |
| CAL_API_KEY | API key for Cal services integration | Yes |
| DB_URL | Database URL | Yes |
| MAIL_USERNAME | Mail username | Yes |
| MAIL_PASSWORD | Mail password | Yes |
| MAIL_PORT | Mail port | Yes |
| MAIL_SERVER | Mail server | Yes |
| MAIL_TLS | Mail tls | Yes |
| MAIL_SSL | Mail ssl | Yes |
| MAIL_FROM | Mail from | Yes |
| TWILIO_ACCOUNT_SID | Twilio account sid | Yes |
| TWILIO_AUTH_TOKEN | Twilio auth token | Yes |
| TWILIO_PHONE_NUMBER | Twilio phone number | Yes |
| RAZOR_KEY_ID | Razorpay key id | Yes |
| RAZOR_KEY_SECRET | Razorpay key secret | Yes |
| DOMAIN_NAME | Domain name | Yes |
| HOST | Host | Yes |

**Note:** Never commit your actual API keys to version control. The values shown above are just examples.

### 5. Create the folder versions in the alembic folder
```bash
mkdir alembic/versions
```

### 6. After the database is created, run the following command to make migrations
```bash
python manage_db.py makemigrations
```

### 7. After the migrations are created, run the following command to apply the migrations
```bash
python manage_db.py migrate
```

### 8. Run the Application
```bash
uvicorn app.main:app --reload
or
fastapi dev
```

### 9. Access the Application
```bash
http://localhost:8000
```



