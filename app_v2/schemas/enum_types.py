from enum import Enum


class RequestMethodEnum(str,Enum):
    get = "GET"
    post = "POST"
    put = "PUT"
    delete = "DELETE"
    patch = "PATCH"

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

