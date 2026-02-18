from pydantic import BaseModel, Field
from typing import Optional, List, Union

class TransferToAgentParams(BaseModel):
    agent_id: str = Field(..., description="The ID of the agent to transfer to")
    condition: str = Field(..., description="The condition that triggers this transfer (e.g., 'User wants to speak to sales')")

class TransferToNumberDestination(BaseModel):
    type: str = Field("phone", description="Type of destination (currently only 'phone')")
    phone_number: str = Field(..., description="The phone number to transfer to (E.164 format e.g. +14155551234)")

class TransferToNumberParams(BaseModel):
    condition: str = Field(..., description="The condition that triggers this transfer (e.g., 'User asks for support')")
    transfer_destination: TransferToNumberDestination = Field(..., description="Destination details for the transfer")

class ToolConfig(BaseModel):
    enabled: bool = False
    name: Optional[str] = None # Name to be passed to LLM e.g "call ending"
    
class TransferToAgentConfig(ToolConfig):
    transfers: List[TransferToAgentParams] = Field(
        default=[],
        description="List of possible transfers. Each item specifies an agent_id to transfer to and the condition for that transfer.",
        examples=[
             [{"agent_id": "agent_xyz123", "condition": "User asks for sales department"}]
        ]
    )

class TransferToNumberConfig(ToolConfig):
    transfers: List[TransferToNumberParams] = Field(
        default=[],
        description="List of possible transfers. Each item specifies a transfer_destination (type, phone_number) and the condition for that transfer.",
        examples=[
            [
                {
                    "condition": "User asks for technical support",
                    "transfer_destination": {
                         "phone_number": "+15551234567"
                    }
                }
            ]
        ]
    )

class BuiltInToolsParams(BaseModel):
    end_call: Optional[Union[bool, ToolConfig]] = Field(default=None, description="Enable end_call tool")
    transfer_to_agent: Optional[TransferToAgentConfig] = Field(default=None, description="Enable and config transfer_to_agent tool")
    transfer_to_number: Optional[TransferToNumberConfig] = Field(default=None, description="Enable and config transfer_to_number tool")
    play_keypad_touch_tone: Optional[Union[bool, ToolConfig]] = Field(default=None, description="Enable play_keypad_touch_tone tool")
