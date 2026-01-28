from fastapi import Depends, HTTPException, APIRouter,status
from app_v2.databases.models.ai_model import AIModels
from app_v2.core.logger import setup_logger
from app_v2.dependecies import get_db, is_admin
from sqlalchemy.orm import Session
from app_v2.databases.models.ai_model import AIModels
logger = setup_logger(__name__)

from app_v2.schemas.ai_model import AIModelIn, AIModelRead, AIModelUpdate
from app_v2.utils.jwt_utils import HTTPBearer


router = APIRouter(prefix="/api/v2/agent",tags=["agent"])

security = HTTPBearer()

@router.post("/ai-model",response_model=AIModelRead,status_code=status.HTTP_201_CREATED,openapi_extra={"security": [{"BearerAuth": []}]},dependencies=[Depends(security),Depends(is_admin)])
async def create_ai_model(model_in:AIModelIn,db:Session = Depends(get_db)):
            """
                create_ai_model is a path operation function to create a ai model and save it in db.
                -Args:
                        model_in: description - json parameter required to create model
                        db: dependency object for database instance
                -Returns:
                            the json format response object of the ai model created        
            """
            try:
                    provider = model_in.provider.lower()
                    model_name = model_in.model_name.lower()

                    # check for existence before creating
                    ai_model = db.query(AIModels).filter(AIModels.model_name ==model_name).first()

                    if ai_model:
                            logger.info("model already exists",model_name)
                            raise HTTPException(
                                    status_code = status.HTTP_400_BAD_REQUEST,
                                    detail= "model already exists"
                            )
                    ai_model = AIModels(provider=provider,model_name=model_name)
                    db.add(ai_model)
                    db.commit()
                    db.refresh(ai_model)
                    logger.info("ai model created successfully")
                    return ai_model
            except HTTPException:
                raise
            except Exception as e:
                    logger.error(f"error while creating ai model {e}")
                    raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="could not create the ai model at the moment"
                    )


@router.get("/ai-model",response_model=list[AIModelRead],status_code=status.HTTP_200_OK)
async def get_ai_models(db: Session = Depends(get_db)):
        try:
                ai_models = db.query(AIModels).all()
                if not ai_models:
                        logger.info("no ai models preset indb yet")
                        raise HTTPException(
                            status_code = status.HTTP_404_NOT_FOUND,
                            detail="no ai models to show"
                        )
                logger.info("ai models fetched successfully")
                return ai_models
        except HTTPException:
                raise
        except Exception as e:
                logger.error(f"error while loading ai models {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="could not fetch ai_models at the moment"
                )
        

@router.get("/ai-model/{id}",response_model=AIModelRead,status_code = status.HTTP_200_OK)
async def get_ai_model_by_id(id:int,db: Session = Depends(get_db)):
                try:
                        ai_model = db.query(AIModels).filter(AIModels.id==id).first()
                        if ai_model is None:
                                logger.info(f"ai_model with id {id} not found")
                                raise HTTPException(
                                        status_code =status.HTTP_404_NOT_FOUND,
                                        detail = f"ai model with id: {id} not found"
                                )
                        logger.info("ai model fetched successfully")
                        return ai_model
                except HTTPException:
                        raise
                except Exception as e:
                        logger.error(f"error while fetching the ai model {e}")
                        raise HTTPException(
                                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail= "could not fetch ai Model at the moment"
                        )


@router.put("/ai-model/{id}",response_model=AIModelRead,status_code= status.HTTP_200_OK,openapi_extra={"security": [{"BearerAuth": []}]},dependencies=[Depends(security),Depends(is_admin)])
async def update_ai_model(id:int, model_updt:AIModelUpdate, db: Session = Depends(get_db)):
                          try:
                                  ai_model = db.query(AIModels).filter(AIModels.id==id).first()
                                  if not ai_model:
                                          logger.info(f"could not find ai model with id {id}")
                                          raise HTTPException(
                                                  status_code = status.HTTP_404_NOT_FOUND,
                                                  detail= f"ai model with the id {id} not found"
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
                                          status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
                                          detail="could not update the ai model at the moment"
                                  )

@router.delete("/ai-modle/{id}",status_code=status.HTTP_204_NO_CONTENT,openapi_extra={"security": [{"BearerAuth": []}]},dependencies=[Depends(security),Depends(is_admin)])
async def delete_ai_model(id:int, db: Session = Depends(get_db)):
        try:
                ai_model = db.query(AIModels).filter(AIModels.id==id).first()
                if ai_model is None:
                        logger.info(f"could not find ai model with id: {id}")
                        raise HTTPException(
                                status_code= status.HTTP_404_NOT_FOUND,
                                detail=f"ai model with id: {id} not found"
                        )
                db.delete(ai_model)
                db.commit()
                logger.info("ai model deleted successfuly")
        except HTTPException:
                raise
        except Exception as e:
                logger.error(f"error while deleting the ai model: {e}")
                raise HTTPException(
                        status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail= "could not delete ai model at the moment"
                )