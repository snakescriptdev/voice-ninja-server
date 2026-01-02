import os
from elevenlabs import ElevenLabs
from sqlalchemy import create_engine, text  
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DB_URL", None)
if not DB_URL:
    raise Exception ("DB_URL not found")
engine = create_engine(DB_URL, pool_pre_ping=True)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") 
client = ElevenLabs(api_key=ELEVEN_API_KEY)


def create_new_and_check_existing(voice_data):
    try:
        for item in voice_data:
            try:
                name = item.get("name")
                elevenlabs_voice_id = item.get("elevenlabs_voice_id")

                if not name or not elevenlabs_voice_id:
                    print(f'skipping record due to missing fields, name: {name}, elevenlabs_voice_id: {elevenlabs_voice_id}')
                    continue

                with engine.begin() as conn:  
                    result = conn.execute(
                        text("SELECT id, voice_name FROM custom_voices WHERE elevenlabs_voice_id = :vid"),
                        {"vid": elevenlabs_voice_id}
                    ).fetchone()

                    if result:
                        print({"status": "exists", "voice": dict(result._mapping)})
                    else:
                        conn.execute(
                            text("""
                                INSERT INTO custom_voices (voice_name, elevenlabs_voice_id, is_custom_voice, created_at, modified_at)
                                VALUES (:vname, :vid, FALSE, NOW(), NOW())
                            """),
                            {"vname": name, "vid": elevenlabs_voice_id}
                        )
                        print("added voice ->", name)

            except SQLAlchemyError as e:
                print(f"Database error: {str(e)} for item: {item}")
            except Exception as ex:
                print(f"Exception: {str(ex)} for item: {item}")
    except Exception as ex:
            print(f"Exception in create_new_and_check_existing: {str(ex)} for voice_data: {len(voice_data)}")


def fetch_11labs_voices():
    try:
        has_more = True
        next_page_token = None
        while has_more:
            if not next_page_token:
                response_data = client.voices.search(include_total_count=True, page_size=50)
            else:
                response_data = client.voices.search(include_total_count=True, page_size=100,next_page_token=next_page_token)
            has_more = response_data.has_more
            next_page_token = response_data.next_page_token
    
            print('response_data',response_data.has_more)
            voice_data = [{"name": x.name, "elevenlabs_voice_id": x.voice_id} for x in response_data.voices]
            print(f'voice_data is : {voice_data}')

            create_new_and_check_existing(voice_data)

    except Exception as ex:
        print(f"Exception in fetch_11labs_voices: {str(ex)} ")


fetch_11labs_voices()