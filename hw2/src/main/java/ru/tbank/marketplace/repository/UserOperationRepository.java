package ru.tbank.marketplace.repository;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import ru.tbank.marketplace.domain.OperationType;
import ru.tbank.marketplace.domain.UserOperationEntity;

public interface UserOperationRepository extends JpaRepository<UserOperationEntity, UUID> {
    Optional<UserOperationEntity> findTopByUserIdAndOperationTypeOrderByCreatedAtDesc(UUID userId, OperationType type);
}
