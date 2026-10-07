package ru.tbank.marketplace.service;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import ru.tbank.marketplace.api.model.ApiError.ErrorCodeEnum;
import ru.tbank.marketplace.api.model.PromoCodeCreate;
import ru.tbank.marketplace.api.model.PromoCodeResponse;
import ru.tbank.marketplace.domain.DiscountType;
import ru.tbank.marketplace.domain.PromoCodeEntity;
import ru.tbank.marketplace.error.ApiException;
import ru.tbank.marketplace.repository.PromoCodeRepository;

@Service
public class PromoCodeService {
    private final PromoCodeRepository promoCodes;

    public PromoCodeService(PromoCodeRepository promoCodes) {
        this.promoCodes = promoCodes;
    }

    @Transactional
    public PromoCodeResponse create(PromoCodeCreate request) {
        if (!request.getValidUntil().isAfter(request.getValidFrom())) {
            throw validation("valid_until", "must be after valid_from");
        }
        if (promoCodes.existsByCode(request.getCode())) {
            throw validation("code", "already exists");
        }
        PromoCodeEntity promo = new PromoCodeEntity(UUID.randomUUID(), request.getCode(),
            DiscountType.valueOf(request.getDiscountType().getValue()), request.getDiscountValue(),
            request.getMinOrderAmount(), request.getMaxUses(), request.getValidFrom(), request.getValidUntil(),
            Boolean.TRUE.equals(request.getActive()), OffsetDateTime.now());
        return toResponse(promoCodes.save(promo));
    }

    private ApiException validation(String field, String message) {
        return new ApiException(ErrorCodeEnum.VALIDATION_ERROR, HttpStatus.BAD_REQUEST,
            "Request validation failed", Map.of("violations", List.of(Map.of("field", field, "message", message))));
    }

    private PromoCodeResponse toResponse(PromoCodeEntity promo) {
        return new PromoCodeResponse(promo.getId(), promo.getCode(),
            ru.tbank.marketplace.api.model.DiscountType.fromValue(promo.getDiscountType().name()),
            promo.getDiscountValue(), promo.getMinOrderAmount(), promo.getMaxUses(), promo.getCurrentUses(),
            promo.getValidFrom(), promo.getValidUntil(), promo.isActive());
    }
}
