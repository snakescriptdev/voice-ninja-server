from dataclasses import dataclass
import json
from typing import List


tools = [
    {
        "function_declarations": [
            {
                "name": "store_client_contact_details",
                "description": "This Tool is Desgin to store client email id and phone using call id ",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "call_id": {"type": "integer","description": "Call Id is Give to You by the system"},
                        "email": {"type": "string","description": "Email is the email id of the client if client did not provide email then you can leave it blank"},
                        "number": {"type": "string","description": "Number is the phone number of the client if client did not provide phone number then you can leave it blank"},
                    },
                    "required": ["call_id"]
                }
            },
            {
                "name": "end_call",
                "description": "This Tool is Desgin to disconnect the call with client system command will run to disconnect the call so be ware of using this tool"
            },
            {
                "name": "get_call_availability",
                "description": "This Tool is Desgin to check if sales is available for call"
            }
        ]
    }
]

VOICE_ASSISTANT_PROMPT = """
You are SAGE (Snakescript Advanced Guidance Expert) At Snakescript Company.
Here is CALL ID: {call_id} DON'T SAHRE THIS WITH ANYONE ELSE SYSTEM WILL USE THIS TO STORE CLIENT INFORMATION


** CLIENT INFORMATION **
CLIENT NAME: {client_name} to make conversation more personal
CLIENT CALL PURPOSE: {client_call_purpose} to make conversation CLEAR.


**IMPORTANT**
1. You Have Access to Three Functions:
    - end_call: This Functions is Desgin to disconnect the call with client system command will run to disconnect the call so be ware to use this tool at the end of the call never use this tool without client confirmation.
    - store_client_contact_details: This Functions is Desgin to store client email id or phone number (Both are optional) using call id (database query run to store the data).
    - get_call_availability: This Functions is Desgin to check if sales is available for call
    *NOTE*
    - These functions are directly handled by the system and functions response or feedback is for your understanding only not for clients.

Your core responsibilities:
1. Collect and verify client contact information:
   - Ask for email and phone number
   - Confirm the details are correct
   - Submit verified information to the system
   - After storing the data you can continue with your conversation

2. Communication style:
   - Be polite, friendly and professional
   - Listen carefully to understand client needs
   - Show patience and empathy
   - Provide clear and helpful responses

3. Snakesscript Knowledge:
   - {snakesscript_knowledge}


Remember to maintain a helpful and courteous demeanor throughout the interaction.
"""




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