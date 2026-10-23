from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "gemma4-voice"
    llm_api_key: str = "ollama"
    llm_num_ctx: int = 6144

    language: str = "en"
    stt_model: str = "large-v3-turbo"
    stt_device: str = "cuda"
    stt_compute_type: str = "int8_float16"
    tts_engine: str = "kokoro"
    tts_voice: str = "af_heart"
    stt_prompt: str = ""

    data_dir: Path = Path("./data")
    models_dir: Path = Path("./models")

    knowledge_dir: Path = Path("./knowledge")
    index_dir: Path = Path("./data/index")
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"

    crm_backend: str = "mock"
    crm_base_url: str = ""
    crm_api_key: str = ""
    # Local WebRTC tests have no caller ID; set this to simulate the caller's number (E.164).
    dev_caller_phone: str = ""
    # Plivo telephony (-t plivo). The serializer hangs up the call with these; also used to look up the caller ID.
    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    duplicate_window_days: int = 30

    fallback_llm_enabled: bool = False
    fallback_llm_base_url: str = ""
    fallback_llm_model: str = ""
    fallback_llm_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
