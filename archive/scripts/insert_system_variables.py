import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

DB_URL = os.getenv("DB_URL")
if not DB_URL:
    raise Exception("DB_URL not found")

engine = create_engine(DB_URL, pool_pre_ping=True)

SYSTEM_VARIABLES = [
    {"name": "system__current_agent_id", "description": "Unique identifier of the currently active agent"},
    {"name": "system__time_utc", "description": "Current UTC time (ISO format)"},
    {"name": "system__time", "description": "Current time in user's timezone"},
    {"name": "system__timezone", "description": "User-provided timezone"},
    {"name": "system__call_sid", "description": "Call SID (Twilio only)"}
]


def create_system_variables():
    try:
        for var in SYSTEM_VARIABLES:
            try:
                name = var["name"]
                description = var["description"]

                with engine.begin() as conn:
                    result = conn.execute(
                        text("SELECT id FROM system_variables WHERE name = :name"),
                        {"name": name}
                    ).fetchone()

                    if result:
                        print({"status": "exists", "name": name})
                    else:
                        conn.execute(
                            text("""
                                INSERT INTO system_variables (name, description)
                                VALUES (:name, :description)
                            """),
                            {"name": name, "description": description}
                        )
                        print("✅ added system variable ->", name)

            except SQLAlchemyError as e:
                print(f"❌ DB Error: {str(e)} for variable: {name}")
            except Exception as ex:
                print(f"❌ Exception: {str(ex)} for variable: {name}")

    except Exception as ex:
        print(f"❌ Exception in create_system_variables: {str(ex)}")


if __name__ == "__main__":
    create_system_variables()
