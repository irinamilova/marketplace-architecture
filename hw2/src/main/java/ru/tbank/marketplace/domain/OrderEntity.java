package ru.tbank.marketplace.domain;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.OneToMany;
import jakarta.persistence.OrderBy;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Entity
@Table(name = "orders")
public class OrderEntity {
    @Id
    private UUID id;
    @Column(name = "user_id", nullable = false)
    private UUID userId;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private OrderStatus status;
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "promo_code_id")
    private PromoCodeEntity promoCode;
    @Column(name = "total_amount", nullable = false)
    private BigDecimal totalAmount;
    @Column(name = "discount_amount", nullable = false)
    private BigDecimal discountAmount;
    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;
    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;
    @OneToMany(mappedBy = "order", cascade = CascadeType.ALL, orphanRemoval = true)
    @OrderBy("id")
    private List<OrderItemEntity> items = new ArrayList<>();

    protected OrderEntity() {
    }

    public OrderEntity(UUID id, UUID userId, OrderStatus status, PromoCodeEntity promoCode,
                       BigDecimal totalAmount, BigDecimal discountAmount, OffsetDateTime createdAt) {
        this.id = id;
        this.userId = userId;
        this.status = status;
        this.promoCode = promoCode;
        this.totalAmount = totalAmount;
        this.discountAmount = discountAmount;
        this.createdAt = createdAt;
        this.updatedAt = createdAt;
    }

    public UUID getId() { return id; }
    public UUID getUserId() { return userId; }
    public OrderStatus getStatus() { return status; }
    public PromoCodeEntity getPromoCode() { return promoCode; }
    public BigDecimal getTotalAmount() { return totalAmount; }
    public BigDecimal getDiscountAmount() { return discountAmount; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public List<OrderItemEntity> getItems() { return items; }

    public void replaceItems(List<OrderItemEntity> newItems) {
        items.clear();
        newItems.forEach(item -> item.attach(this));
        items.addAll(newItems);
        updatedAt = OffsetDateTime.now();
    }

    public void applyTotals(BigDecimal totalAmount, BigDecimal discountAmount, PromoCodeEntity promoCode) {
        this.totalAmount = totalAmount;
        this.discountAmount = discountAmount;
        this.promoCode = promoCode;
        this.updatedAt = OffsetDateTime.now();
    }

    public void cancel() {
        status = OrderStatus.CANCELED;
        updatedAt = OffsetDateTime.now();
    }
}
