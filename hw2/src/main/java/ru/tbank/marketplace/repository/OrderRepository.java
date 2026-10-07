package ru.tbank.marketplace.repository;

import jakarta.persistence.LockModeType;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import ru.tbank.marketplace.domain.OrderEntity;
import ru.tbank.marketplace.domain.OrderStatus;

public interface OrderRepository extends JpaRepository<OrderEntity, UUID> {
    boolean existsByUserIdAndStatusIn(UUID userId, Set<OrderStatus> statuses);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select distinct o from OrderEntity o left join fetch o.items where o.id = :id")
    Optional<OrderEntity> findLockedById(@Param("id") UUID id);

    @Query("select distinct o from OrderEntity o left join fetch o.items where o.id = :id")
    Optional<OrderEntity> findWithItemsById(@Param("id") UUID id);
}
