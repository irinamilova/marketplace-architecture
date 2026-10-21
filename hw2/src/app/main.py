from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import ApiException
from app.generated.router import router
from app.logging_middleware import log_requests


app = FastAPI(title="Marketplace API", version="1.0.0")
app.middleware("http")(log_requests)
app.include_router(router)


@app.exception_handler(ApiException)
async def api_exception_handler(request: Request, exception: ApiException):
    body = {"error_code": exception.error_code, "message": exception.message}
    if exception.details is not None:
        body["details"] = exception.details
    return JSONResponse(status_code=exception.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exception: RequestValidationError):
    violations = []
    for error in exception.errors():
        location = [str(part) for part in error["loc"] if part not in {"body", "query", "path"}]
        violations.append({"field": ".".join(location), "message": error["msg"]})
    return JSONResponse(
        status_code=400,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": {"violations": violations},
        },
    )
