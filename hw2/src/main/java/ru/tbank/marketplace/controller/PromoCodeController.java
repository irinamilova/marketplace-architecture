package ru.tbank.marketplace.controller;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RestController;
import ru.tbank.marketplace.api.PromoCodesApi;
import ru.tbank.marketplace.api.model.PromoCodeCreate;
import ru.tbank.marketplace.api.model.PromoCodeResponse;
import ru.tbank.marketplace.service.PromoCodeService;

@RestController
public class PromoCodeController implements PromoCodesApi {
    private final PromoCodeService promoCodeService;

    public PromoCodeController(PromoCodeService promoCodeService) {
        this.promoCodeService = promoCodeService;
    }

    @Override
    public ResponseEntity<PromoCodeResponse> createPromoCode(PromoCodeCreate request) {
        return ResponseEntity.status(HttpStatus.CREATED).body(promoCodeService.create(request));
    }
}
