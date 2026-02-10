from fastapi import Depends, HTTPException, APIRouter, status
from fastapi_sqlalchemy import db
from app_v2.databases.models import AIModels
from app_v2.core.logger import setup_logger
from app_v2.schemas.ai_model import AIModelIn, AIModelRead, AIModelUpdate
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.utils.jwt_utils import HTTPBearer
from app_v2.utils.jwt_utils import is_admin

logger = setup_logger(__name__)

router = APIRouter(prefix="/api/v2", tags=["agent"])

security = HTTPBearer()


@router.post(
    "/ai-model",
    response_model=AIModelRead,
    status_code=status.HTTP_201_CREATED
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
    status_code=status.HTTP_200_OK
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
    status_code=status.HTTP_204_NO_CONTENT
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
