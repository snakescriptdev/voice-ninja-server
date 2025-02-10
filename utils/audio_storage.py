import shutil
from pathlib import Path
from typing import Optional
from fastapi import UploadFile
from .config import AUDIO_STORAGE_DIR

class AudioStorage:
    @staticmethod
    async def save_audio(file: UploadFile, session_id: str) -> Optional[Path]:
        """Save audio file with session ID"""
        try:
            filename = f"{session_id}.wav"
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
        file_path = AUDIO_STORAGE_DIR / f"{session_id}.wav"
        return file_path if file_path.exists() else None

    @staticmethod
    def delete_audio(session_id: str) -> bool:
        """Delete audio file"""
        try:
            file_path = AUDIO_STORAGE_DIR / f"{session_id}.wav"
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception as e:
            print(f"Error deleting audio: {e}")
            return False
        
    @staticmethod
    def get_audio_files() -> list[Path]:
        """Get all audio files"""
        return list(AUDIO_STORAGE_DIR.glob("*.wav"))