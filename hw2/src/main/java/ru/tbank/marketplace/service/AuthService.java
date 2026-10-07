package ru.tbank.marketplace.service;

import io.jsonwebtoken.JwtException;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import ru.tbank.marketplace.api.model.ApiError.ErrorCodeEnum;
import ru.tbank.marketplace.api.model.AuthResponse;
import ru.tbank.marketplace.api.model.LoginRequest;
import ru.tbank.marketplace.api.model.RefreshRequest;
import ru.tbank.marketplace.api.model.RegisterRequest;
import ru.tbank.marketplace.domain.Role;
import ru.tbank.marketplace.domain.UserEntity;
import ru.tbank.marketplace.error.ApiException;
import ru.tbank.marketplace.repository.UserRepository;
import ru.tbank.marketplace.security.JwtService;

@Service
public class AuthService {
    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;

    public AuthService(UserRepository users, PasswordEncoder passwordEncoder, JwtService jwtService) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
    }

    public AuthResponse register(RegisterRequest request) {
        String email = request.getEmail().toLowerCase();
        if (users.findByEmail(email).isPresent()) {
            throw new ApiException(ErrorCodeEnum.VALIDATION_ERROR, HttpStatus.BAD_REQUEST,
                "Request validation failed", Map.of("violations",
                    java.util.List.of(Map.of("field", "email", "message", "already registered"))));
        }
        UserEntity user = new UserEntity(UUID.randomUUID(), email, passwordEncoder.encode(request.getPassword()),
            Role.valueOf(request.getRole().getValue()), OffsetDateTime.now());
        users.save(user);
        return tokens(user);
    }

    public AuthResponse login(LoginRequest request) {
        UserEntity user = users.findByEmail(request.getEmail().toLowerCase())
            .filter(found -> passwordEncoder.matches(request.getPassword(), found.getPasswordHash()))
            .orElseThrow(() -> new ApiException(ErrorCodeEnum.TOKEN_INVALID, HttpStatus.UNAUTHORIZED,
                "Email or password is invalid"));
        return tokens(user);
    }

    public AuthResponse refresh(RefreshRequest request) {
        try {
            var claims = jwtService.parse(request.getRefreshToken());
            if (!"refresh".equals(claims.get("type", String.class))) {
                throw new JwtException("Invalid token type");
            }
            UUID userId = UUID.fromString(claims.getSubject());
            UserEntity user = users.findById(userId).orElseThrow(() -> new JwtException("Unknown user"));
            return tokens(user);
        } catch (JwtException | IllegalArgumentException exception) {
            throw new ApiException(ErrorCodeEnum.REFRESH_TOKEN_INVALID, HttpStatus.UNAUTHORIZED,
                "Refresh token is invalid");
        }
    }

    private AuthResponse tokens(UserEntity user) {
        return new AuthResponse(
            jwtService.createAccessToken(user.getId(), user.getRole()),
            jwtService.createRefreshToken(user.getId(), user.getRole()));
    }
}
