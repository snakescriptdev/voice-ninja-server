from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi_sqlalchemy import db
from sqlalchemy.orm import joinedload
from typing import List

from app_v2.schemas.variables import (
    VariableCreateSchema,
    VariableReadSchema,
    VariableUpdateSchema
)
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import (
    VariablesModel,
    AgentModel,
    UnifiedAuthModel
)
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(
    prefix="/api/v2/agent-variables",
    tags=["agent-variables"],
    dependencies=[Depends(security)]
)


def variable_to_read(var: VariablesModel) -> VariableReadSchema:
    return VariableReadSchema(
        variable_name=var.variable_name,
        variable_value=var.variable_value,
        id=var.id,
        agent_id=var.agent_id,
        created_at=var.created_at,
        modified_at=var.modified_at
    )


# -------------------- CREATE --------------------

@router.post(
    "/",
    response_model=VariableReadSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create agent variable",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def create_variable(
    var_in: VariableCreateSchema,
    agent_id: int = Query(..., description="ID of the agent to attach variable to"),
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        # Check ownership of agent
        agent = (
            db.session.query(AgentModel)
            .filter(
                AgentModel.id == agent_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not agent:
            logger.warning(f"Agent not found or unauthorized | agent_id={agent_id} user_id={current_user.id}")
            raise HTTPException(status_code=404, detail="Agent not found")

        # Create variable
        new_var = VariablesModel(
            variable_name=var_in.variable_name,
            variable_value=var_in.variable_value,
            agent_id=agent.id,
        )

        db.session.add(new_var)
        db.session.commit()
        db.session.refresh(new_var)

        logger.info(f"Variable created successfully | id={new_var.id}")
        return variable_to_read(new_var)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error while creating variable: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create variable"
        )


# -------------------- GET BY AGENT --------------------

@router.get(
    "/agent/{agent_id}",
    response_model=List[VariableReadSchema],
    summary="Get all variables for an agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_variables_by_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        # Check ownership
        agent = (
            db.session.query(AgentModel)
            .filter(
                AgentModel.id == agent_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        variables = (
            db.session.query(VariablesModel)
            .filter(VariablesModel.agent_id == agent_id)
            .all()
        )

        return [variable_to_read(v) for v in variables]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching variables for agent {agent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch variables"
        )


# -------------------- GET BY ID --------------------

@router.get(
    "/{variable_id}",
    response_model=VariableReadSchema,
    summary="Get variable by ID",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_variable_by_id(
    variable_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        # Join AgentModel to verify user ownership
        var = (
            db.session.query(VariablesModel)
            .join(AgentModel)
            .filter(
                VariablesModel.id == variable_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not var:
            logger.info(f"Variable not found | id={variable_id}")
            raise HTTPException(status_code=404, detail="Variable not found")

        return variable_to_read(var)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching variable {variable_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch variable"
        )


# -------------------- UPDATE --------------------

@router.put(
    "/{variable_id}",
    response_model=VariableReadSchema,
    summary="Update variable",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_variable(
    variable_id: int,
    var_in: VariableUpdateSchema,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        var = (
            db.session.query(VariablesModel)
            .join(AgentModel)
            .filter(
                VariablesModel.id == variable_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not var:
            raise HTTPException(status_code=404, detail="Variable not found")

        if var_in.variable_name is not None:
            var.variable_name = var_in.variable_name
        if var_in.variable_value is not None:
            var.variable_value = var_in.variable_value

        db.session.commit()
        db.session.refresh(var)

        logger.info(f"Variable updated successfully | id={var.id}")
        return variable_to_read(var)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating variable {variable_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update variable"
        )


# -------------------- DELETE --------------------

@router.delete(
    "/{variable_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete variable",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def delete_variable(
    variable_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        var = (
            db.session.query(VariablesModel)
            .join(AgentModel)
            .filter(
                VariablesModel.id == variable_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not var:
            raise HTTPException(status_code=404, detail="Variable not found")

        db.session.delete(var)
        db.session.commit()

        logger.info(f"Variable deleted successfully | id={variable_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting variable {variable_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete variable"
        )
