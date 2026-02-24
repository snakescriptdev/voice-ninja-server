ADMIN_MANAGEMENT.md
Voice Ninja – Admin Management CLI Utility

This document explains how to use the Admin Management Utility script for managing administrator users in the Voice Ninja system.

📌 Overview

The Admin Management Utility is a command-line tool designed to manage administrator users in the system.

It allows you to:

Create a new admin user

Promote an existing user to admin

Update admin status (enable/disable)

List all administrators

The script interacts directly with the database using SQLAlchemy and the UnifiedAuthModel.

Make sure virtual environment is activated

🚀 How to Run

Execute the script using Python:

python admin_utility.py <command> [options]
🛠 Available Commands
1️⃣ Create or Promote Admin

Creates a new admin user or promotes an existing user to admin.

Command
python admin_utility.py create --email <email> --name <name> --phone <phone>
Parameters
Argument	Required	Description
--email	Yes	Email of the admin
--name	No	Name of the admin
--phone	No	Phone number
Example
python admin_utility.py create --email admin@example.com --name "John Doe" --phone 9876543210
Behavior

If the user already exists:

Promotes user to admin

Sets is_admin = True

Sets is_verified = True

Updates name/phone if provided

If the user does not exist:

Creates new user

Sets:

is_admin = True

is_verified = True

has_otp_auth = True

2️⃣ Update Admin Status

Updates the admin status of an existing user.

Command
python admin_utility.py update --email <email> --admin <true/false>
Parameters
Argument	Required	Description
--email	Yes	User email
--admin	Yes	true or false
Example
python admin_utility.py update --email user@example.com --admin false
Behavior

Updates is_admin field

Prints confirmation message

If user not found → prints error message

3️⃣ List All Admins

Lists all users where is_admin = True.

Command
python admin_utility.py list
Example Output
Existing Administrators:
--------------------------------------------------
ID: 1 | Email: admin@example.com | Name: John Doe
--------------------------------------------------