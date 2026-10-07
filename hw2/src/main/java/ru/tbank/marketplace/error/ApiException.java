package ru.tbank.marketplace.error;

import java.util.Map;
import org.springframework.http.HttpStatus;
import ru.tbank.marketplace.api.model.ApiError.ErrorCodeEnum;

public class ApiException extends RuntimeException {
    private final ErrorCodeEnum code;
    private final HttpStatus status;
    private final Map<String, Object> details;

    public ApiException(ErrorCodeEnum code, HttpStatus status, String message) {
        this(code, status, message, null);
    }

    public ApiException(ErrorCodeEnum code, HttpStatus status, String message, Map<String, Object> details) {
        super(message);
        this.code = code;
        this.status = status;
        this.details = details;
    }

    public ErrorCodeEnum getCode() { return code; }
    public HttpStatus getStatus() { return status; }
    public Map<String, Object> getDetails() { return details; }
}
