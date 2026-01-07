"""Constants for API responses and status codes.

This module defines all constants used for consistent API responses
across the application.
"""

# Response field names
STATUS = 'status'
STATUS_CODE = 'status_code'
MESSAGE = 'message'
DATA = 'data'

# Status values
STATUS_SUCCESS = 'success'
STATUS_FAILED = 'failed'

# HTTP Status Codes
HTTP_200_OK = 200
HTTP_400_BAD_REQUEST = 400
HTTP_401_UNAUTHORIZED = 401
HTTP_404_NOT_FOUND = 404
HTTP_500_INTERNAL_SERVER_ERROR = 500

# Response Messages
MSG_USERNAME_REQUIRED = 'Username required'
MSG_INVALID_EMAIL_OR_PHONE = 'Invalid email or phone format'
MSG_OTP_SENT_EMAIL = 'OTP sent to your email'
MSG_OTP_SENT_SMS = 'OTP sent to your SMS'
MSG_FAILED_TO_SEND_OTP = 'Failed to send OTP'
MSG_USERNAME_AND_OTP_REQUIRED = 'Username and OTP required'
MSG_USER_NOT_FOUND = 'User not found'
MSG_INVALID_OTP = 'Invalid OTP'
MSG_OTP_EXPIRED = 'OTP expired'
MSG_LOGIN_SUCCESSFUL = 'Login successful'
MSG_FAILED_TO_SEND_OTP_VIA_METHOD = 'Failed to send OTP via {method}'

# OTP Configuration
OTP_EXPIRY_MINUTES = 10
OTP_LENGTH = 6

# Delivery Methods
METHOD_EMAIL = 'email'
METHOD_SMS = 'SMS'

