from datetime import datetime
import base64
from .helper import AudioFileMetaData

def encode_filename(SID:str="none", voice:str="none") -> str:
    # Create the base string without extension
    base_str = f'{SID}_{voice}_{datetime.now().strftime("%Y%m%dT%H:%M:%S")}'
    # Encode to base64 and add padding if needed
    encoded = base64.b64encode(base_str.encode()).decode()
    # Add the file extension
    return f"{encoded}.wav"


def decode_filename(filename:str) -> AudioFileMetaData:
    # Remove the file extension before decoding
    base_name = filename.rsplit('.', 1)[0]
    # Add padding if needed
    padding_needed = len(base_name) % 4
    if padding_needed:
        base_name += '=' * (4 - padding_needed)
    
    decode_filename = base64.b64decode(base_name).decode()
    SID, voice, created_at = decode_filename.split("_")
    created_at = datetime.strptime(created_at, "%Y%m%dT%H:%M:%S")
    audio_type = filename.split(".")[-1]
    return AudioFileMetaData(SID=SID, voice=voice, created_at=created_at, audio_type=audio_type)