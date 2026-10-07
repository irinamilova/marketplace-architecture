package ru.tbank.marketplace.controller;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RestController;
import ru.tbank.marketplace.api.AuthApi;
import ru.tbank.marketplace.api.model.AuthResponse;
import ru.tbank.marketplace.api.model.LoginRequest;
import ru.tbank.marketplace.api.model.RefreshRequest;
import ru.tbank.marketplace.api.model.RegisterRequest;
import ru.tbank.marketplace.service.AuthService;

@RestController
public class AuthController implements AuthApi {
    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @Override
    public ResponseEntity<AuthResponse> register(RegisterRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED).body(authService.register(request));
    }

    @Override
    public ResponseEntity<AuthResponse> login(LoginRequest request) {
        return ResponseEntity.ok(authService.login(request));
    }

    @Override
    public ResponseEntity<AuthResponse> refresh(RefreshRequest request) {
        return ResponseEntity.ok(authService.refresh(request));
    }
}
