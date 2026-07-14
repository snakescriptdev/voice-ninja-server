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
    knowledge_lookup = "knowledge_lookup"
    customer_support = "customer_support"
    healthcare_assistant = "healthcare_assistant"
    custom = "custom"


class Capebilites(str,Enum):
    knowledge_base = "knowledge_base"
    api_integration = "api_integration"
    agent_transfer = "agent_transfer"
    end_call = "end_call"

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
    in_progress = "in_progress"

class ChannelEnum(str,Enum):
    test_voice = "Test voice"
    call= "call"
    widget = "widget"
    api = "api"
    web_agent = "Web Agent"

class WidgetPosition(str,Enum):
    top_left = "top-left"
    top_right = "top-right"
    bottom_left = "bottom-left"
    bottom_right = "bottom-right"

class WebAgentPosition(str, Enum):
    left = "left"
    center = "center"
    right = "right"

class PaymentProviderEnum(str, Enum):
    razorpay = "razorpay"
    stripe = "stripe"

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
    expired = "expired"
    carry_forward_reset = "carry_forward_reset"
    admin_adjustment = "admin_adjustment"

class PlanFeatureEnum(str,Enum):
    ai_voice_agents = "ai_voice_agents"
    phone_numbers = "phone_numbers"
    monthly_minutes = "monthly_minutes"
    knowledge_base = "knowledge_base"
    widget_agent = "widget_agent"
    api_access = "api_access"
    analytics_dashboard= "analytics_dashboard"
    custom_voice_cloning= "custom_voice_cloning"
    web_agent = "web_agent"


# Features that are pure on/off gates with no numeric usage tracking behind
# them (no entry in FEATURE_USAGE_HANDLERS). A "limit" has no meaning for
# these — enabled always means unlimited, so storing/returning anything but
# None for them is misleading.
BOOLEAN_ONLY_PLAN_FEATURES = {PlanFeatureEnum.analytics_dashboard, PlanFeatureEnum.web_agent}


class SubscriptionBillingEventEnum(str, Enum):
    """
    ActivityLogModel.event_type values for subscription lifecycle changes
    that have no associated PaymentModel row (pause/cancel), but still need
    to show up in the user's billing history.
    """
    paused = "subscription_paused"
    cancellation_scheduled = "subscription_cancellation_scheduled"
    cancelled = "subscription_cancelled"
    cancelled_admin_inactive = "subscription_cancelled_admin_inactive"
    cancelled_admin_deleted = "subscription_cancelled_admin_deleted"


# All event_types billing history should pull in from ActivityLogModel.
SUBSCRIPTION_BILLING_EVENT_TYPES = {e.value for e in SubscriptionBillingEventEnum}

# Display status shown to the user for each event — kept coarse (paused /
# cancellation_scheduled / cancelled); the *reason* for admin-triggered
# cancellations lives in the event's description text instead.
SUBSCRIPTION_BILLING_EVENT_STATUS_LABELS = {
    SubscriptionBillingEventEnum.paused.value: "paused",
    SubscriptionBillingEventEnum.cancellation_scheduled.value: "cancellation_scheduled",
    SubscriptionBillingEventEnum.cancelled.value: "cancelled",
    SubscriptionBillingEventEnum.cancelled_admin_inactive.value: "cancelled",
    SubscriptionBillingEventEnum.cancelled_admin_deleted.value: "cancelled",
}