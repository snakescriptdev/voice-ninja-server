import shutil
from pathlib import Path
from typing import Optional
from fastapi import UploadFile
from .config import AUDIO_STORAGE_DIR
from .extra_utils import decode_filename,encode_filename,AudioFile
from fastapi import Request
import soundfile as sf


class AudioStorage:
    @staticmethod
    async def save_audio(file: UploadFile, session_id: str) -> Optional[Path]:
        """Save audio file with session ID"""
        try:

            filename = encode_filename(session_id,"none")
            file_path = AUDIO_STORAGE_DIR / filename
            
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            return file_path
        except Exception as e:
            print(f"Error saving audio: {e}")
            return None

    @staticmethod
    def get_audio_path(session_id: str) -> Optional[Path]:
        """Get audio file for a session"""
        file_path = AUDIO_STORAGE_DIR / session_id
        return file_path if file_path.exists() else None

    @staticmethod
    def delete_audio(filename: str) -> bool:
        """Delete audio file"""
        try:
            file_path = AUDIO_STORAGE_DIR / filename
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception as e:
            print(f"Error deleting audio: {e}")
            return False
        
    @staticmethod
    def get_audio_files(request: Optional[Request]=None) -> list[AudioFile]:
        """Get all audio files"""
        audio_files = list(AUDIO_STORAGE_DIR.glob("*.wav"))
        result = []
        
        for file in audio_files:
            decode_file_name = decode_filename(file.name)
            url = (
                f"{request.base_url._url}audio/{file.name}/"
                if request
                else f"/audio/{file.name}"
            )
            duration = sf.info(file).duration
            
            audio_file = AudioFile(
                name=file.name,
                url=url,
                session_id=decode_file_name.SID,
                voice=decode_file_name.voice,
                created_at=decode_file_name.created_at,
                duration=duration
            )
            result.append(audio_file)
        
        # Sort result by created_at in descending order (newest first)
        result.sort(key=lambda x: x.created_at, reverse=True)
            
        return result
