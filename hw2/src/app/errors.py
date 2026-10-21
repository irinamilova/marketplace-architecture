class ApiException(Exception):
    def __init__(self, error_code: str, status_code: int, message: str, details=None):
        self.error_code = error_code
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(message)
