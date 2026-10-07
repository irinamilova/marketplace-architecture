package ru.tbank.marketplace.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.UUID;
import javax.crypto.SecretKey;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import ru.tbank.marketplace.domain.Role;

@Service
public class JwtService {
    private final SecretKey key;
    private final Duration accessLifetime;
    private final Duration refreshLifetime;

    public JwtService(@Value("${app.jwt.secret}") String secret,
                      @Value("${app.jwt.access-minutes}") long accessMinutes,
                      @Value("${app.jwt.refresh-days}") long refreshDays) {
        key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        accessLifetime = Duration.ofMinutes(accessMinutes);
        refreshLifetime = Duration.ofDays(refreshDays);
    }

    public String createAccessToken(UUID userId, Role role) {
        return createToken(userId, role, "access", accessLifetime);
    }

    public String createRefreshToken(UUID userId, Role role) {
        return createToken(userId, role, "refresh", refreshLifetime);
    }

    public Claims parse(String token) {
        return Jwts.parser().verifyWith(key).build().parseSignedClaims(token).getPayload();
    }

    private String createToken(UUID userId, Role role, String type, Duration lifetime) {
        Instant now = Instant.now();
        return Jwts.builder()
            .subject(userId.toString())
            .claim("role", role.name())
            .claim("type", type)
            .issuedAt(Date.from(now))
            .expiration(Date.from(now.plus(lifetime)))
            .signWith(key)
            .compact();
    }
}
