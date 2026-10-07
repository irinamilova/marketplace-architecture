package ru.tbank.marketplace.repository;

import jakarta.persistence.LockModeType;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import ru.tbank.marketplace.domain.PromoCodeEntity;

public interface PromoCodeRepository extends JpaRepository<PromoCodeEntity, UUID> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select p from PromoCodeEntity p where p.code = :code")
    Optional<PromoCodeEntity> findLockedByCode(@Param("code") String code);

    boolean existsByCode(String code);
}
