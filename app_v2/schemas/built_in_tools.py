from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List, Union

class TransferToAgentParams(BaseModel):
    agent_id: int = Field(..., description="The internal numeric `id` of the agent to transfer to (the `id` field from a GET /api/v2/public/agents item) — not its display name.")
    condition: str = Field(..., description="The condition that triggers this transfer (e.g., 'User wants to speak to sales')")

    @field_validator("condition")
    @classmethod
    def condition_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("condition is required for transfer to agent tool")
        return value

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
        description=(
            "List of possible transfers — accepts multiple entries, one per "
            "condition, so a single agent can route to different target "
            "agents depending on what the caller says."
        ),
        examples=[
            [
                {"agent_id": 42, "condition": "User asks for the sales department"},
                {"agent_id": 57, "condition": "User asks for billing or a refund"},
            ]
        ]
    )
    # Duplicate-transfer detection (same agent_id + condition) happens in the
    # router (transform_built_in_tools in app_v2/routers/agents.py) instead of
    # here, so the error message can show the target agent's name rather than
    # its raw id — this schema has no DB access to resolve that name.

    @model_validator(mode="after")
    def enabled_requires_at_least_one_transfer(self):
        if self.enabled and not self.transfers:
            raise ValueError("transfer_to_agent is enabled but has no transfers configured — add at least one {agent_id, condition} entry")
        return self

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


class PublicBuiltInToolsParams(BaseModel):
    """
    built_in_tools payload for the public API (POST/PUT /api/v2/public/agents).

    Deliberately simpler than the internal BuiltInToolsParams:
    - No transfer_to_number or play_keypad_touch_tone — both depend on a
      Twilio phone connection, which the public API has never supported
      configuring.
    - end_call is a plain bool, not the internal {enabled, name} object —
      `name` only overrides the label ElevenLabs shows the LLM for the tool
      (e.g. "call ending" instead of "end_call") and isn't meaningful to set
      through this API.
    - transfer_to_agent is a flat list of {agent_id, condition} — there's no
      separate `enabled` flag; an empty/omitted list means disabled, any
      entries mean enabled. Converted to a full BuiltInToolsParams (with
      transfer_to_number/play_keypad_touch_tone forced to None, and
      transfer_to_agent wrapped back into {enabled, transfers}) before being
      passed to transform_built_in_tools in app_v2/routers/agents.py, which
      is shared with the internal router.
    """
    model_config = ConfigDict(extra="forbid")

    end_call: Optional[bool] = Field(default=None, description="(boolean) Whether the agent can end the call on its own once the conversation is complete.")
    transfer_to_agent: Optional[List[TransferToAgentParams]] = Field(
        default=None,
        description=(
            "(list of objects, NOT a single object) Agents this agent can transfer to, one entry per "
            "condition — accepts multiple entries so a single agent can route to different targets "
            "depending on what the caller says. Omit or send an empty list to disable transfer_to_agent."
        ),
        examples=[
            [
                {"agent_id": 42, "condition": "User asks for the sales department"},
                {"agent_id": 57, "condition": "User asks for billing or a refund"},
            ]
        ],
    )
