package ru.tbank.marketplace.logging;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.NullNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import ru.tbank.marketplace.security.CurrentUser;

@Component
public class RequestLoggingFilter extends OncePerRequestFilter {
    private static final Logger LOGGER = LoggerFactory.getLogger(RequestLoggingFilter.class);
    private static final Set<String> MUTATING_METHODS = Set.of("POST", "PUT", "DELETE");
    private final ObjectMapper objectMapper;

    public RequestLoggingFilter(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
        throws ServletException, IOException {
        String requestId = UUID.randomUUID().toString();
        Instant timestamp = Instant.now();
        long started = System.nanoTime();
        byte[] body = request.getInputStream().readAllBytes();
        CachedBodyRequest wrapped = new CachedBodyRequest(request, body);
        response.setHeader("X-Request-Id", requestId);
        try {
            chain.doFilter(wrapped, response);
        } finally {
            Map<String, Object> log = new LinkedHashMap<>();
            log.put("request_id", requestId);
            log.put("method", request.getMethod());
            log.put("endpoint", request.getRequestURI());
            log.put("status_code", response.getStatus());
            log.put("duration_ms", (System.nanoTime() - started) / 1_000_000);
            String userId = currentUserId();
            log.put("user_id", userId == null ? NullNode.getInstance() : userId);
            log.put("timestamp", timestamp.toString());
            if (MUTATING_METHODS.contains(request.getMethod())) {
                log.put("request_body", body.length == 0 ? NullNode.getInstance() : maskedBody(body));
            }
            LOGGER.info(objectMapper.writeValueAsString(log));
        }
    }

    private String currentUserId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        return authentication != null && authentication.getPrincipal() instanceof CurrentUser user
            ? user.id().toString() : null;
    }

    private Object maskedBody(byte[] body) {
        try {
            JsonNode node = objectMapper.readTree(new String(body, StandardCharsets.UTF_8));
            mask(node);
            return node;
        } catch (Exception exception) {
            return "unavailable";
        }
    }

    private void mask(JsonNode node) {
        if (node instanceof ObjectNode object) {
            object.fieldNames().forEachRemaining(name -> {
                if (name.equals("password") || name.equals("refresh_token")) {
                    object.put(name, "***");
                } else {
                    mask(object.get(name));
                }
            });
        } else if (node.isArray()) {
            node.forEach(this::mask);
        }
    }

    private static class CachedBodyRequest extends HttpServletRequestWrapper {
        private final byte[] body;

        CachedBodyRequest(HttpServletRequest request, byte[] body) {
            super(request);
            this.body = body;
        }

        @Override
        public ServletInputStream getInputStream() {
            ByteArrayInputStream input = new ByteArrayInputStream(body);
            return new ServletInputStream() {
                @Override
                public int read() {
                    return input.read();
                }

                @Override
                public boolean isFinished() {
                    return input.available() == 0;
                }

                @Override
                public boolean isReady() {
                    return true;
                }

                @Override
                public void setReadListener(ReadListener listener) {
                    try {
                        if (isFinished()) {
                            listener.onAllDataRead();
                        } else {
                            listener.onDataAvailable();
                        }
                    } catch (IOException exception) {
                        listener.onError(exception);
                    }
                }
            };
        }

        @Override
        public BufferedReader getReader() {
            return new BufferedReader(new InputStreamReader(getInputStream(), StandardCharsets.UTF_8));
        }
    }
}
