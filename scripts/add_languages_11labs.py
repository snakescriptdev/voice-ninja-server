import json
from sqlalchemy import create_engine, text, bindparam
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
import os

DB_URL = os.getenv("DB_URL", None)
if not DB_URL:
    raise Exception("DB_URL not found")

engine = create_engine(DB_URL, pool_pre_ping=True)

ELEVENLABS_MODELS = [
    {
        "name": "eleven_multilingual_v2",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
            {"code": "de", "name": "German"},
            {"code": "hi", "name": "Hindi"},
            {"code": "fr", "name": "French"},
            {"code": "ko", "name": "Korean"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "it", "name": "Italian"},
            {"code": "es", "name": "Spanish"},
            {"code": "id", "name": "Indonesian"},
            {"code": "nl", "name": "Dutch"},
            {"code": "tr", "name": "Turkish"},
            {"code": "fil", "name": "Filipino"},
            {"code": "pl", "name": "Polish"},
            {"code": "sv", "name": "Swedish"},
            {"code": "bg", "name": "Bulgarian"},
            {"code": "ro", "name": "Romanian"},
            {"code": "ar", "name": "Arabic"},
            {"code": "cs", "name": "Czech"},
            {"code": "el", "name": "Greek"},
            {"code": "fi", "name": "Finnish"},
            {"code": "hr", "name": "Croatian"},
            {"code": "ms", "name": "Malay"},
            {"code": "sk", "name": "Slovak"},
            {"code": "da", "name": "Danish"},
            {"code": "ta", "name": "Tamil"},
            {"code": "uk", "name": "Ukrainian"},
            {"code": "ru", "name": "Russian"},
        ],
    },
    {
        "name": "eleven_turbo_v2",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "es", "name": "Spanish"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "it", "name": "Italian"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "hi", "name": "Hindi"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
        ],
    },
    {
        "name": "eleven_monolingual_v1",
        "languages": [
            {"code": "en", "name": "English"},
        ],
    },
    {
        "name": "eleven_multilingual_stable_v1",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "es", "name": "Spanish"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "it", "name": "Italian"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "hi", "name": "Hindi"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
        ],
    },
]

def create_elevenlab_models():
    try:
        for model in ELEVENLABS_MODELS:
            try:
                model_name = model.get("name")
                if not model_name:
                    print(f"Skipping invalid model: {model}")
                    continue

                languages = model.get("languages", [])
                if not isinstance(languages, list):
                    print(f"⚠️ Invalid languages for {model_name}, skipping")
                    continue

                with engine.begin() as conn:
                    result = conn.execute(
                        text("SELECT id, name FROM elevenlab_models WHERE name = :name"),
                        {"name": model_name}
                    ).fetchone()

                    if result:
                        print({"status": "exists", "model": dict(result._mapping)})
                    else:
                        stmt = text("""
                            INSERT INTO elevenlab_models (name, languages, created_at, modified_at)
                            VALUES (:name, :languages, NOW(), NOW())
                        """).bindparams(
                            bindparam("languages", type_=JSONB)
                        )

                        conn.execute(
                            stmt,
                            {"name": model_name, "languages": languages}
                        )
                        print("✅ added elevenlab model ->", model_name)

            except SQLAlchemyError as e:
                print(f"❌ Database error: {str(e)} for model: {model.get('name')}")
            except Exception as ex:
                print(f"❌ Exception: {str(ex)} for model: {model.get('name')}")
    except Exception as ex:
        print(f"❌ Exception in create_elevenlab_models: {str(ex)}")


if __name__ == "__main__":
    create_elevenlab_models()
