from dataclasses import dataclass
import json
from typing import List


tools = [
    {
        "function_declarations": [
            {
                "name": "end_call",
                "description": "Ends the current conversation or call session",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Optional reason for ending the call",
                            "enum": ["completed", "user_request", "timeout", "error"],
                        }
                    },
                    "required": ['reason']
                }
            },
            {
                "name": "submit_email_number",
                "description": "Submit the email number",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "call_id": {
                            "type": "string",
                            "description": "The call id to submit",
                        },
                        "email": {
                            "type": "string",
                            "description": "The email to submit",
                        },
                        "number": {
                            "type": "string",
                            "description": "The number to submit",
                        }
                    },
                    "required": ['call_id', 'email', 'number']
                }
            }
        ]
    }
]

@dataclass
class Conversation:
    role: str
    message: str


@dataclass
class ConversationList:
    conversation: List[Conversation]

    @classmethod
    def load_from_json(cls, json_str: str | dict) -> 'ConversationList':
        """
        Load conversation from JSON string or dict
        
        Args:
            json_str: JSON string or dict containing conversation data
            
        Returns:
            ConversationList: Populated conversation list object
        """
        # Convert string to dict if needed
        if isinstance(json_str, str):
            data = json.loads(json_str)
        else:
            data = json_str
            
        # Convert conversation items to Conversation objects
        conversations = [
            Conversation(
                role=item['role'],
                message=item['message']
            ) for item in data['conversation']
        ]
        
        return cls(conversation=conversations)