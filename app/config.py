from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    # Optional comma-separated list of extra keys (separate projects = separate quotas).
    gemini_api_keys: str = ""

    llm_model: str = "gemini-flash-lite-latest"
    llm_fallback_models: str = "gemini-3.5-flash,gemini-3-flash-preview,gemini-flash-latest"
    # -1 = do not send a thinking config (some models reject thinking_budget=0 with a 400).
    llm_thinking_budget: int = -1
    llm_timeout_s: float = 9.0
    llm_fallback_timeout_s: float = 8.0
    llm_max_repairs: int = 1

    request_deadline_s: float = 25.0
    interpretation_cache_size: int = 2048
    enable_hedging: bool = True
    enable_secondary_objective: bool = True
    enable_rule_fallback: bool = True
    infeasible_policy: str = "best_effort"  # "best_effort" | "error"
    prompt_version: str = "v1"

    log_level: str = "INFO"
    port: int = 8000
    web_concurrency: int = 2

    @property
    def api_key_list(self) -> list[str]:
        keys = [self.gemini_api_key, *self.gemini_api_keys.split(",")]
        out: list[str] = []
        for k in keys:
            k = k.strip()
            if k and k not in out:
                out.append(k)
        return out

    @property
    def thinking_budget(self) -> int | None:
        return None if self.llm_thinking_budget < 0 else self.llm_thinking_budget

    @property
    def fallback_model_list(self) -> list[str]:
        return [m.strip() for m in self.llm_fallback_models.split(",") if m.strip()]

    @property
    def model_chain(self) -> list[str]:
        return [self.llm_model, *self.fallback_model_list]


settings = Settings()
