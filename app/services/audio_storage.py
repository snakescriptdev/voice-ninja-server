import shutil
from pathlib import Path
from typing import Optional
from fastapi import UploadFile
from app.core import VoiceSettings
from app.utils import encode_filename,decode_filename,AudioFile,AudioFileMetaData
from fastapi import Request


class AudioStorage:
    @staticmethod
    async def save_audio(file: UploadFile, session_id: str) -> Optional[Path]:
        """Save audio file with session ID"""
        try:

            filename = encode_filename(session_id,"none")
            file_path = VoiceSettings.AUDIO_STORAGE_DIR / filename
            
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            return file_path
        except Exception as e:
            print(f"Error saving audio: {e}")
            return None

    @staticmethod
    def get_audio_path(session_id: str) -> Optional[Path]:
        """Get audio file for a session"""
        file_path = VoiceSettings.AUDIO_STORAGE_DIR / session_id
        return file_path if file_path.exists() else None

    @staticmethod
    def delete_audio(filename: str) -> bool:
        """Delete audio file"""
        try:
            file_path = VoiceSettings.AUDIO_STORAGE_DIR / filename
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception as e:
            print(f"Error deleting audio: {e}")
            return False