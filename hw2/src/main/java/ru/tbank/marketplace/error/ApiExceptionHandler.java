package ru.tbank.marketplace.error;

import jakarta.validation.ConstraintViolationException;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import ru.tbank.marketplace.api.model.ApiError;
import ru.tbank.marketplace.api.model.ApiError.ErrorCodeEnum;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ApiError> handleApi(ApiException exception) {
        return ResponseEntity.status(exception.getStatus())
            .body(new ApiError(exception.getCode(), exception.getMessage()).details(exception.getDetails()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleBodyValidation(MethodArgumentNotValidException exception) {
        List<Map<String, String>> violations = exception.getBindingResult().getAllErrors().stream()
            .map(error -> Map.of(
                "field", error instanceof FieldError fieldError ? fieldError.getField() : error.getObjectName(),
                "message", error.getDefaultMessage() == null ? "invalid value" : error.getDefaultMessage()))
            .toList();
        return validation(violations);
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ApiError> handleConstraintValidation(ConstraintViolationException exception) {
        List<Map<String, String>> violations = exception.getConstraintViolations().stream()
            .map(error -> Map.of("field", error.getPropertyPath().toString(), "message", error.getMessage()))
            .toList();
        return validation(violations);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadable() {
        return validation(List.of(Map.of("field", "body", "message", "invalid JSON or field value")));
    }

    private ResponseEntity<ApiError> validation(List<Map<String, String>> violations) {
        ApiError error = new ApiError(ErrorCodeEnum.VALIDATION_ERROR, "Request validation failed")
            .details(Map.of("violations", violations));
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
    }
}
