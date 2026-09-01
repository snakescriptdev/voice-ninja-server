from typing import Optional

from fastapi import Depends, HTTPException, APIRouter, status
from fastapi_sqlalchemy import db
from pydantic import BaseModel
from app_v2.databases.models import AIModels, AgentModel
from app_v2.core.logger import setup_logger
from app_v2.schemas.ai_model import AIModelIn, AIModelRead, AIModelUpdate
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.llm_pricing import LlmPricingResponse, LlmPriceItem
from app_v2.schemas.voice_schema import VoiceRead
from app_v2.utils.jwt_utils import HTTPBearer
from app_v2.utils.jwt_utils import is_admin
from app_v2.utils.elevenlabs.agent_utils import ElevenLabsAgent
from app_v2.utils.currency_utils import get_usd_to_inr_rate
from app_v2.utils.coin_utils import get_free_tier_defaults
from app_v2.routers.voice import voice_to_read

logger = setup_logger(__name__)

router = APIRouter(prefix="/api/v2", tags=["agent"])

security = HTTPBearer()


@router.post(
    "/ai-model",
    response_model=AIModelRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(is_admin)],
)
async def create_ai_model(model_in: AIModelIn):
    """
    create_ai_model is a path operation function to create a ai model and save it in db.
    """
    try:
        provider = model_in.provider.lower()
        model_name = model_in.model_name.lower()

        ai_model = (
            db.session.query(AIModels)
            .filter(AIModels.model_name == model_name)
            .first()
        )

        if ai_model:
            logger.info("model already exists", model_name)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model already exists",
            )

        ai_model = AIModels(
            provider=provider,
            model_name=model_name,
        )

        db.session.add(ai_model)
        db.session.commit()
        db.session.refresh(ai_model)

        logger.info("ai model created successfully")
        return ai_model

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while creating ai model {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not create the ai model at the moment:{str(e)}",
        )


@router.get(
    "/ai-model",
    response_model=PaginatedResponse[AIModelRead],
    status_code=status.HTTP_200_OK,
)
async def get_ai_models(
    skip: int = 0,
    limit: int = 10,
):
    try:
        query = db.session.query(AIModels)
        total = query.count()
        
        ai_models = query.offset(skip).limit(limit).all()

        import math
        pages = math.ceil(total / limit) if limit > 0 else 1
        current_page = (skip // limit) + 1 if limit > 0 else 1

        logger.info("ai models fetched successfully")
        
        return PaginatedResponse(
            total=total,
            page=current_page,
            size=limit,
            pages=pages,
            items=ai_models
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while loading ai models {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not fetch ai_models at the moment:{str(e)}",
        )


class FreeTierDefaultsResponse(BaseModel):
    ai_model: Optional[AIModelRead] = None
    voice: Optional[VoiceRead] = None


@router.get(
    "/free-tier-defaults",
    response_model=FreeTierDefaultsResponse,
    status_code=status.HTTP_200_OK,
    summary="The admin-designated free-tier default AI model and voice",
)
async def get_free_tier_defaults_endpoint():
    """
    Used by the agent create/edit form (both the "start from scratch" flow
    and the guided-setup wizard) to reliably resolve and pin the free-tier
    default model/voice for an unpaid user — independent of GET /ai-model
    and GET /voice's own pagination (both default to a 50-item page), which
    the default item is not guaranteed to appear on. `ai_model`/`voice` are
    null if the admin hasn't configured either yet (feature is a no-op).
    """
    free_model, free_voice = get_free_tier_defaults()
    return FreeTierDefaultsResponse(
        ai_model=free_model,
        voice=voice_to_read(free_voice) if free_voice else None,
    )


@router.get(
    "/ai-model-pricing",
    response_model=LlmPricingResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(is_admin)],
    summary="Per-minute LLM pricing (USD + INR), for the admin panel's model catalog",
)
async def get_ai_model_pricing_for_admin():
    """
    Shows the same per-minute USD/INR price for every LLM that
    /agents/{agent_id}/llm-pricing shows a user on the create/edit agent
    page — same ElevenLabs calculate_llm_usage() call, same USD->INR
    conversion. The admin model-catalog picker has no single agent of its
    own to derive this from, so it's computed against the most recently
    created agent anywhere in the system that has been synced to
    ElevenLabs, as a representative reference. Returns an empty price list
    (not an error) if no synced agent exists yet anywhere.
    """
    usd_to_inr_rate = get_usd_to_inr_rate()

    reference_agent = (
        db.session.query(AgentModel)
        .filter(AgentModel.elevenlabs_agent_id.isnot(None))
        .order_by(AgentModel.id.desc())
        .first()
    )
    if not reference_agent:
        return LlmPricingResponse(llm_prices=[], usd_to_inr_rate=usd_to_inr_rate)

    response = ElevenLabsAgent().calculate_llm_usage(reference_agent.elevenlabs_agent_id)
    if not response.status:
        return LlmPricingResponse(llm_prices=[], usd_to_inr_rate=usd_to_inr_rate)

    llm_prices = [
        LlmPriceItem(
            llm=item["llm"],
            price_per_minute_usd=item["price_per_minute"],
            price_per_minute_inr=round(item["price_per_minute"] * usd_to_inr_rate, 4),
        )
        for item in (response.data or {}).get("llm_prices", [])
    ]
    return LlmPricingResponse(llm_prices=llm_prices, usd_to_inr_rate=usd_to_inr_rate)


@router.get(
    "/ai-model/{id}",
    response_model=AIModelRead,
    status_code=status.HTTP_200_OK,
)
async def get_ai_model_by_id(id: int):
    try:
        ai_model = (
            db.session.query(AIModels)
            .filter(AIModels.id == id)
            .first()
        )

        if ai_model is None:
            logger.info(f"ai_model with id {id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ai model with id: {id} not found",
            )

        logger.info("ai model fetched successfully")
        return ai_model

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while fetching the ai model {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not fetch ai Model at the moment:{str(e)}",
        )


@router.put(
    "/ai-model/{id}",
    response_model=AIModelRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(is_admin)],
)
async def update_ai_model(id: int, model_updt: AIModelUpdate):
    try:
        ai_model = (
            db.session.query(AIModels)
            .filter(AIModels.id == id)
            .first()
        )

        if not ai_model:
            logger.info(f"could not find ai model with id {id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ai model with the id {id} not found",
            )

        if model_updt.provider is not None:
            ai_model.provider = model_updt.provider

        if model_updt.model_name is not None:
            ai_model.model_name = model_updt.model_name

        if model_updt.is_free_tier_default is not None:
            if model_updt.is_free_tier_default:
                # Clear the flag on every other row first, in the same
                # transaction, so uniqueness (enforced at the app level, not
                # via a DB constraint) can never be violated.
                db.session.query(AIModels).filter(AIModels.id != id).update(
                    {AIModels.is_free_tier_default: False}, synchronize_session=False
                )
            ai_model.is_free_tier_default = model_updt.is_free_tier_default

        db.session.commit()
        db.session.refresh(ai_model)

        logger.info("ai model updated successfully")
        return ai_model

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while updating aimodel {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not update the ai model at the moment:{str(e)}",
        )


@router.delete(
    "/ai-modle/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(is_admin)],
)
async def delete_ai_model(id: int):
    try:
        ai_model = (
            db.session.query(AIModels)
            .filter(AIModels.id == id)
            .first()
        )

        if ai_model is None:
            logger.info(f"could not find ai model with id: {id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ai model with id: {id} not found",
            )

        db.session.delete(ai_model)
        db.session.commit()

        logger.info("ai model deleted successfuly")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while deleting the ai model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not delete ai model at the moment:{str(e)}",
        )
