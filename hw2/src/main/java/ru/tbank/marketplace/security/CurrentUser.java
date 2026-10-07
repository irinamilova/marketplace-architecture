package ru.tbank.marketplace.security;

import java.util.UUID;
import ru.tbank.marketplace.domain.Role;

public record CurrentUser(UUID id, Role role) {
}
