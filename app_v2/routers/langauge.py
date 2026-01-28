from fastapi import APIRouter, Depends, HTTPException, status
from app_v2.schemas.language_schema import LanguageIn, LanguageRead, LanguageUpdate
from app_v2.databases.models.language import LanguageModel
from sqlalchemy.orm import Session
from app_v2.dependecies import get_db
from sqlalchemy import or_
from app_v2.dependecies import is_admin
from app_v2.core.logger import setup_logger
from app_v2.utils.jwt_utils import get_current_user,HTTPBearer

logger = setup_logger(__name__)
router = APIRouter(prefix="/api/v2/agent",tags=["agent"])

security = HTTPBearer()

@router.post("/language", response_model=LanguageRead, status_code=status.HTTP_201_CREATED,openapi_extra={"security": [{"BearerAuth": []}]},dependencies=[Depends(security),Depends(is_admin)])
async def create_language(lang_in: LanguageIn, db: Session = Depends(get_db)):
    try:
        lang_code = lang_in.lang_code.lower()
        language = lang_in.language.lower()

        lang_instance = (
            db.query(LanguageModel)
            .filter(
                or_(
                    LanguageModel.lang_code == lang_code,
                    LanguageModel.language == language
                )
            )
            .first()
        )

        if lang_instance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Language already exists"
            )

        lang_instance = LanguageModel(
            lang_code=lang_code,
            language=language
        )

        db.add(lang_instance)
        db.commit()
        db.refresh(lang_instance)

        return lang_instance  # 👈 matches LanguageRead

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while creating language: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to create language at the moment."
        )


@router.get("/language",response_model=list[LanguageRead],status_code=status.HTTP_200_OK)
async def get_languages(db:Session= Depends(get_db)):
       try:
             languages = db.query(LanguageModel).all()
             if not languages:
                   logger.info(f"no languages to show from database {languages}")
                   raise HTTPException(status.HTTP_404_NOT_FOUND,detail="no languages found")
            
             logger.info("languages fetched succesfully from database")
             return languages
       except HTTPException:
            raise
       except Exception as e:
             logger.error(f"error while fetching the languages at the moment: {e}")
             raise HTTPException(
                  status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                  detail="could not fetch the languages at the moment."
             )
       

@router.get("/language/{id}",response_model=LanguageRead,status_code = status.HTTP_200_OK)
async def get_langauge_by_id(id:int,db:Session = Depends(get_db)):
     try:
        language = db.query(LanguageModel).filter(LanguageModel.id==id).first()
        if language is None:
            logger.info(f"langauge not found with id:{id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"language with id: {id} not found"
            )
        logger.info("language returned successfully")
        return language
     except HTTPException:
        raise
     except Exception as e:
          logger.info(f"error while fetching the language: {e}")
          raise HTTPException(
               status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
               detail= "could not fetch the language at the moment"
          )



@router.put("/language/{id}",response_model=LanguageRead,status_code=status.HTTP_200_OK,openapi_extra={"security": [{"BearerAuth": []}]},dependencies=[Depends(security),Depends(is_admin)])
async def update_langauge(id:int,lang_updt:LanguageUpdate, db:Session = Depends(get_db)):
            try:
                language = db.query(LanguageModel).filter(LanguageModel.id==id).first()
                if not language:
                     logger.info(f"language with id: {id} not found")
                     raise HTTPException(
                          status_code= status.HTTP_404_NOT_FOUND,
                          detail= f"language with id: {id} not found"
                     )
                if lang_updt.lang_code is not None:
                     language.lang_code = lang_updt.lang_code.lower()
                
                if lang_updt.language is not None:
                     language.language = lang_updt.language.lower()
                db.commit()
                db.refresh(language)
                
                logger.info("language updated successfully successfully")
                
                return language
            except HTTPException:
                 raise
            except Exception as e:
                 logger.error(f"error while updating the langauge: {e}")
                 raise HTTPException(
                      status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
                      detail="could not update the language at the moment"
                 )   
            


@router.delete("/language/{id}",status_code =status.HTTP_204_NO_CONTENT,openapi_extra={"security": [{"BearerAuth": []}]},dependencies=[Depends(security),Depends(is_admin)])
async def delete_language(id:int, db: Session = Depends(get_db)):
            try:
                  lang_instance = db.query(LanguageModel).filter(LanguageModel.id ==id).first()
                  if lang_instance is None:
                        logger.info(f"langauge with id: {id} not found")
                        raise HTTPException(
                              status_code= status.HTTP_404_NOT_FOUND,
                              detail= f"langauge with id:{id} not found"
                        )
                  db.delete(lang_instance)
                  db.commit()
                  logger.info("language deleted successfully")
                  return 
            except HTTPException:
                 raise
            except Exception as e:
                  logger.error(f"error while deleting language {e}")
                  raise HTTPException(
                    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="could not delete the langauge at the moment"
                  )
                  
                        
                        
     