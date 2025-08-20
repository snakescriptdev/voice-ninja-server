from app.core.config import DEFAULT_VARS
from pydantic import BaseModel, Field, model_validator
from typing import Dict, Any ,List, Optional

class SaveNoiseVariablesRequest(BaseModel):
    agent_id: str
    variables: Dict[str, Any]

    @model_validator(mode="before")
    def validate_variables(cls, values):
        variables = values.get("variables", {})
        errors = {}
        AUDIO_VARS = DEFAULT_VARS

        for key, val in variables.items():
            if key not in AUDIO_VARS:
                errors[key] = "Invalid variable"
                continue

            default_val = AUDIO_VARS[key]

            # BOOLEAN
            if isinstance(default_val, bool):
                if isinstance(val, bool):
                    continue
                elif isinstance(val, str) and val.lower() in ["true", "1", "false", "0"]:
                    continue
                else:
                    errors[key] = "Must be a boolean (True/False/0/1)"

            # INTEGER
            elif isinstance(default_val, int):
                try:
                    int(val)
                except:
                    errors[key] = "Must be an integer"

            # FLOAT
            elif isinstance(default_val, float):
                try:
                    float(val)
                except:
                    errors[key] = "Must be a float"

            # RANGE EXAMPLE
            if key == "AUDIO_NOISE_REDUCTION_STRENGTH":
                try:
                    v = float(val)
                    if not (0.0 <= v <= 1.0):
                        errors[key] = "Must be between 0.0 and 1.0"
                except:
                    errors[key] = "Must be a float"

        if errors:
            # In Pydantic v2, just raise ValueError with the dict; FastAPI can catch and return JSON
            raise ValueError(errors)

        return values


class ResetNoiseVariablesRequest(BaseModel):
    agent_id: str
    variables: Optional[List[str]] = None
