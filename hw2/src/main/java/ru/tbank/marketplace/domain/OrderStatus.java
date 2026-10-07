package ru.tbank.marketplace.domain;

public enum OrderStatus {
    CREATED,
    PAYMENT_PENDING,
    PAID,
    SHIPPED,
    COMPLETED,
    CANCELED
}
