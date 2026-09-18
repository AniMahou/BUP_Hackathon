"""Error types and their JSON body shape (§6.3): {"error","message","details"}."""


class BadRequestError(Exception):
    def __init__(self, message: str, details: list[dict] | None = None):
        self.message = message
        self.details = details or []
        super().__init__(message)


class UnprocessableError(Exception):
    def __init__(self, message: str, details: list[dict] | None = None):
        self.message = message
        self.details = details or []
        super().__init__(message)


def error_body(code: str, message: str, details: list[dict] | None = None) -> dict:
    return {"error": code, "message": message, "details": details or []}
