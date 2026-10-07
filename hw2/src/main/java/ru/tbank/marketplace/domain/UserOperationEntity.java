package ru.tbank.marketplace.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "user_operations")
public class UserOperationEntity {
    @Id
    private UUID id;
    @Column(name = "user_id", nullable = false)
    private UUID userId;
    @Enumerated(EnumType.STRING)
    @Column(name = "operation_type", nullable = false)
    private OperationType operationType;
    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;

    protected UserOperationEntity() {
    }

    public UserOperationEntity(UUID id, UUID userId, OperationType operationType, OffsetDateTime createdAt) {
        this.id = id;
        this.userId = userId;
        this.operationType = operationType;
        this.createdAt = createdAt;
    }

    public OffsetDateTime getCreatedAt() { return createdAt; }
}
