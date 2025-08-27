import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

DB_URL = os.getenv("DB_URL", None)
if not DB_URL:
    raise Exception ("DB_URL not found")
engine = create_engine(DB_URL, pool_pre_ping=True)

VALID_LLMS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4",
    "gpt-4-turbo",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-3.5-turbo",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "claude-sonnet-4",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
    "claude-3-5-sonnet-v1",
    "claude-3-haiku",
    "grok-beta",
    "custom-llm",
    "qwen3-4b",
    "qwen3-30b-a3b",
    "gpt-oss-20b",
    "gpt-oss-120b",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.5-flash-lite-preview-06-17",
    "gemini-2.0-flash-lite-001",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash-002",
    "gemini-1.5-flash-001",
    "gemini-1.5-pro-002",
    "gemini-1.5-pro-001",
    "claude-sonnet-4@20250514",
    "claude-3-7-sonnet@20250219",
    "claude-3-5-sonnet@20240620",
    "claude-3-5-sonnet-v2@20241022",
    "claude-3-haiku@20240307",
    "gpt-5-2025-08-07",
    "gpt-5-mini-2025-08-07",
    "gpt-5-nano-2025-08-07",
    "gpt-4.1-2025-04-14",
    "gpt-4.1-mini-2025-04-14",
    "gpt-4.1-nano-2025-04-14",
    "gpt-4o-mini-2024-07-18",
    "gpt-4o-2024-11-20",
    "gpt-4o-2024-08-06",
    "gpt-4o-2024-05-13",
    "gpt-4-0613",
    "gpt-4-0314",
    "gpt-4-turbo-2024-04-09",
    "gpt-3.5-turbo-0125",
    "gpt-3.5-turbo-1106"
]

def create_llm_models():
    try:
        for llm_name in VALID_LLMS:
            try:
                if not llm_name:
                    print(f"Skipping invalid llm_name: {llm_name}")
                    continue

                with engine.begin() as conn:
                    # Check if model already exists
                    result = conn.execute(
                        text("SELECT id, name FROM llm_models WHERE name = :name"),
                        {"name": llm_name}
                    ).fetchone()

                    if result:
                        print({"status": "exists", "llm_model": dict(result._mapping)})
                    else:
                        # Insert new record
                        conn.execute(
                            text("""
                                INSERT INTO llm_models (name, created_at, modified_at)
                                VALUES (:name, NOW(), NOW())
                            """),
                            {"name": llm_name}
                        )
                        print("added llm ->", llm_name)

            except SQLAlchemyError as e:
                print(f"Database error: {str(e)} for llm_name: {llm_name}")
            except Exception as ex:
                print(f"Exception: {str(ex)} for llm_name: {llm_name}")
    except Exception as ex:
        print(f"Exception in create_llm_models: {str(ex)} for list: {len(VALID_LLMS)}")


create_llm_models()