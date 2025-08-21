import requests
import logging
from time import sleep
import os
from typing import Optional,Any
from elevenlabs import ElevenLabs

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") 
BASE_URL = "https://api.elevenlabs.io/v1"

class ElevenLabsResponse:
    def __init__(self, status: bool, data: Optional[Any] = None, error_message: str = ""):
        self.status = status
        self.data = data
        self.error_message = error_message

    def to_dict(self):
        return {
            "status": self.status,
            "data": self.data,
            "error_message": self.error_message
        }


class ElevenLabsUtils:
    def __init__(self):
        self.client = ElevenLabs(api_key=ELEVEN_API_KEY)
        self.api_key = ELEVEN_API_KEY
        self.base_url = BASE_URL
        self.headers = {"xi-api-key": self.api_key}

    def create_cloned_voice(self, file_path: str, name: str = "MyCustomVoice", retries: int = 3) -> ElevenLabsResponse:
        """
        Add a custom voice by uploading an audio file via ElevenLabs API.
        """
        for attempt in range(1, retries + 1):
            try:
                with open(file_path, "rb") as f:
                    files = {"files": f}
                    data = {"name": name}
                    response = requests.post(f"{self.base_url}/voices/add", headers=self.headers, files=files, data=data)

                if response.status_code == 200:
                    result = response.json()
                    voice_id = result.get("voice_id")
                    logger.info(f"✅ Voice cloned successfully! voice_id = {voice_id}")
                    return ElevenLabsResponse(status=True, data={"voice_id": voice_id})
                else:
                    error_message = f"Attempt {attempt}: Failed to clone voice. Status {response.status_code}, Response: {response.text}"
                    logger.warning(error_message)
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Exception while creating cloned voice: {error_message}")

            sleep(2)

        return ElevenLabsResponse(status=False, error_message=error_message)

    def get_voice(self, voice_id: str, retries: int = 3) -> ElevenLabsResponse:
        for attempt in range(1, retries + 1):
            try:
                voice = self.client.voices.get(voice_id=voice_id)
                logger.info(f"✅ Retrieved voice {voice_id}")
                return ElevenLabsResponse(status=True, data={"voice": voice})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsResponse(status=False, error_message=error_message)

    def edit_voice_name(self, voice_id: str, new_name: str, retries: int = 3) -> ElevenLabsResponse:
        check = self.get_voice(voice_id)
        if not check.status:
            return ElevenLabsResponse(status=False, error_message=f"Voice not found: {voice_id}")

        for attempt in range(1, retries + 1):
            try:
                updated_voice = self.client.voices.update(voice_id=voice_id, name=new_name)
                logger.info(f"✅ Voice {voice_id} renamed successfully to {new_name}")
                return ElevenLabsResponse(status=True, data={"voice_id": voice_id, "name":new_name})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsResponse(status=False, error_message=f"Failed to edit voice name after {retries} attempts")

    def delete_voice(self, voice_id: str, retries: int = 3) -> ElevenLabsResponse:
        check = self.get_voice(voice_id)
        if not check.status:
            return ElevenLabsResponse(status=False, error_message=f"Voice not found: {voice_id}")

        for attempt in range(1, retries + 1):
            try:
                self.client.voices.delete(voice_id=voice_id)
                logger.info(f"✅ Voice {voice_id} deleted successfully")
                return ElevenLabsResponse(status=True, data={"voice_id": voice_id})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsResponse(status=False, error_message=f"Failed to delete voice after {retries} attempts")

    def get_all_voices(self, retries: int = 3) -> ElevenLabsResponse:
        for attempt in range(1, retries + 1):
            try:
                voices = self.client.voices.search(include_total_count=True)
                logger.info(f"✅ Retrieved {len(voices.voices)} voices")
                return ElevenLabsResponse(status=True, data={"voices": voices.voices})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsResponse(status=False, error_message=error_message)
