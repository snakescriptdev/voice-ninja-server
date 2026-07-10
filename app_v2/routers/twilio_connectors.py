from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from sqlalchemy import func
from typing import List

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from app_v2.databases.models import TwilioUserCreds, UnifiedAuthModel
from app_v2.schemas.twilio_connector_schema import TwilioConnectorCreate, TwilioConnectorUpdate, TwilioConnectorResponse
from app_v2.utils.jwt_utils import HTTPBearer
from app_v2.utils.crypto_utils import encrypt_data, decrypt_data
from app_v2.utils.feature_access import RequireFeatureEnabled
from app_v2.schemas.enum_types import PlanFeatureEnum
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

security = HTTPBearer()
router = APIRouter(
    prefix="/api/v2/twilio-connectors",
    tags=["Twilio Connectors"],
    dependencies=[Depends(security)]
)


def _to_response(creds: TwilioUserCreds) -> TwilioConnectorResponse:
    return TwilioConnectorResponse(
        id=creds.id,
        name=creds.name,
        account_sid=creds.account_sid,
        auth_token=decrypt_data(creds.auth_token),
        created_at=creds.created_at,
    )


@router.get("/", response_model=List[TwilioConnectorResponse], openapi_extra={"security": [{"BearerAuth": []}]})
async def list_twilio_connectors(
    current_user: UnifiedAuthModel = Depends(RequireFeatureEnabled(PlanFeatureEnum.phone_numbers))
):
    """List all Twilio connectors belonging to the current user."""
    try:
        with db():
            connectors = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.user_id == current_user.id
            ).order_by(TwilioUserCreds.created_at.desc()).all()

            return [_to_response(c) for c in connectors]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching Twilio connectors for user_id={current_user.id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Twilio connectors")


@router.get("/{connector_id}", response_model=TwilioConnectorResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def get_twilio_connector(
    connector_id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeatureEnabled(PlanFeatureEnum.phone_numbers))
):
    """Get a single Twilio connector belonging to the current user."""
    try:
        with db():
            connector = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.id == connector_id,
                TwilioUserCreds.user_id == current_user.id
            ).first()

            if not connector:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Twilio connector not found")

            return _to_response(connector)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching Twilio connector_id={connector_id} for user_id={current_user.id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Twilio connector")


@router.post("/", response_model=TwilioConnectorResponse, status_code=status.HTTP_201_CREATED, openapi_extra={"security": [{"BearerAuth": []}]})
async def create_twilio_connector(
    connector_in: TwilioConnectorCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeatureEnabled(PlanFeatureEnum.phone_numbers))
):
    """Add a new Twilio connector after verifying the credentials with Twilio."""
    try:
        with db():
            existing_name = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.user_id == current_user.id,
                func.lower(TwilioUserCreds.name) == connector_in.name.lower()
            ).first()
            if existing_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"A connector with the name '{existing_name.name}' already exists."
                )

            existing_sid = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.user_id == current_user.id,
                func.lower(TwilioUserCreds.account_sid) == connector_in.account_sid.lower()
            ).first()
            if existing_sid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"This Twilio account is already connected as '{existing_sid.name}'."
                )

        try:
            client = TwilioClient(connector_in.account_sid, connector_in.auth_token)
            client.api.accounts(connector_in.account_sid).fetch()
        except TwilioRestException as te:
            logger.warning(f"Twilio credential verification failed for user_id={current_user.id}: {str(te)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Twilio Account SID or Auth Token."
            )

        with db():
            new_connector = TwilioUserCreds(
                user_id=current_user.id,
                name=connector_in.name,
                account_sid=connector_in.account_sid,
                auth_token=encrypt_data(connector_in.auth_token),
            )
            db.session.add(new_connector)
            db.session.commit()
            db.session.refresh(new_connector)

            logger.info(f"Twilio connector created for user_id={current_user.id}, connector_id={new_connector.id}")
            return _to_response(new_connector)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Twilio connector for user_id={current_user.id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create Twilio connector")


@router.put("/{connector_id}", response_model=TwilioConnectorResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def update_twilio_connector(
    connector_id: int,
    connector_in: TwilioConnectorUpdate,
    current_user: UnifiedAuthModel = Depends(RequireFeatureEnabled(PlanFeatureEnum.phone_numbers))
):
    """Update a Twilio connector's name and/or credentials. If credentials change, they are re-verified with Twilio."""
    if (connector_in.account_sid is None) != (connector_in.auth_token is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both Account SID and Auth Token must be provided together to update credentials."
        )

    try:
        with db():
            connector = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.id == connector_id,
                TwilioUserCreds.user_id == current_user.id
            ).first()
            if not connector:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Twilio connector not found")

            if connector_in.name is not None:
                existing_name = db.session.query(TwilioUserCreds).filter(
                    TwilioUserCreds.user_id == current_user.id,
                    TwilioUserCreds.id != connector_id,
                    func.lower(TwilioUserCreds.name) == connector_in.name.lower()
                ).first()
                if existing_name:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"A connector with the name '{existing_name.name}' already exists."
                    )

            if connector_in.account_sid is not None:
                existing_sid = db.session.query(TwilioUserCreds).filter(
                    TwilioUserCreds.user_id == current_user.id,
                    TwilioUserCreds.id != connector_id,
                    func.lower(TwilioUserCreds.account_sid) == connector_in.account_sid.lower()
                ).first()
                if existing_sid:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"This Twilio account is already connected as '{existing_sid.name}'."
                    )

            if connector_in.account_sid is not None and connector_in.auth_token is not None:
                try:
                    client = TwilioClient(connector_in.account_sid, connector_in.auth_token)
                    client.api.accounts(connector_in.account_sid).fetch()
                except TwilioRestException as te:
                    logger.warning(f"Twilio credential verification failed for user_id={current_user.id}: {str(te)}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid Twilio Account SID or Auth Token."
                    )
                connector.account_sid = connector_in.account_sid
                connector.auth_token = encrypt_data(connector_in.auth_token)

            if connector_in.name is not None:
                connector.name = connector_in.name

            db.session.commit()
            db.session.refresh(connector)

            logger.info(f"Twilio connector updated. connector_id={connector_id}, user_id={current_user.id}")
            return _to_response(connector)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating Twilio connector_id={connector_id} for user_id={current_user.id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update Twilio connector")


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT, openapi_extra={"security": [{"BearerAuth": []}]})
async def delete_twilio_connector(
    connector_id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeatureEnabled(PlanFeatureEnum.phone_numbers))
):
    """Delete a Twilio connector."""
    try:
        with db():
            connector = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.id == connector_id,
                TwilioUserCreds.user_id == current_user.id
            ).first()

            if not connector:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Twilio connector not found")

            db.session.delete(connector)
            db.session.commit()

        logger.info(f"Twilio connector deleted. connector_id={connector_id}, user_id={current_user.id}")
        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting Twilio connector_id={connector_id} for user_id={current_user.id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete Twilio connector")
