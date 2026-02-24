from enum import Enum


class RequestMethodEnum(str,Enum):
    get = "GET"
    post = "POST"
    put = "PUT"
    delete = "DELETE"
    patch = "PATCH"



class HeaderValueType(str, Enum):
    STRING = "string"              # Hardcoded value


class JsonSchemaType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class ContentTypeEnum(str, Enum):
    """Common MIME types for webhook request/response content"""
    JSON = "application/json"
    XML = "application/xml"
    FORM_URLENCODED = "application/x-www-form-urlencoded"
    FORM_DATA = "multipart/form-data"
    TEXT_PLAIN = "text/plain"
    TEXT_HTML = "text/html"


class UseCases(str,Enum):
    email_assistant = "email_assistant"
    task_execution = "task_execution"
    system_assistant = "system_assistant"
    knowledge_lookup = "knowledge_lookup"
    customer_support = "customer_support"
    custom = "custom"


class Capebilites(str,Enum):
    email_integration = "email_integration"
    calendar_management = "calendar_management"
    knowledge_base = "knowledge_base"
    api_integration = "api_integration"

class ResponseStyleEnum(str, Enum):
    professional = "professional"
    friendly = "friendly"
    casual = "casual"


class GenderEnum(str,Enum):
    male = "male"
    female = "female"
    null= None

class PhoneNumberAssignStatus(str,Enum):
    assigned = "assigned"
    unassigned = "unassigned"

class CallStatusEnum(str,Enum):
    success = "success"
    failed = "failed"

class ChannelEnum(str,Enum):
    chat = "chat"
    call= "call"
    widget = "widget"

class WidgetPosition(str,Enum):
    top_left = "top-left"
    top_right = "top-right"
    bottom_left = "bottom-left"
    bottom_right = "bottom-right"

class BillingPeriodEnum(str, Enum):
    monthly = "monthly"
    annual = "annual"

class PlanIconEnum(str, Enum):
    zap = "zap"
    sparkles = "sparkles"
    crown = "crown"

class PaymentProviderEnum(str, Enum):
    razorpay = "razorpay"
    stripe = "stripe"

class SubscriptionStatusEnum(str, Enum):
    active = "active"
    cancelled = "cancelled"
    expired = "expired"
    past_due = "past_due"

class PaymentStatusEnum(str, Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    refunded = "refunded"

class PaymentTypeEnum(str, Enum):
    subscription = "subscription"
    coin_purchase = "coin_purchase"
    addon = "addon"

class CoinTransactionTypeEnum(str, Enum):
    credit_subscription = "credit_subscription"
    credit_purchase = "credit_purchase"
    debit_usage = "debit_usage"
    refund = "refund"