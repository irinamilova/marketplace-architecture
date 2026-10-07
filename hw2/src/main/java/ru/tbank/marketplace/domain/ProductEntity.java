package ru.tbank.marketplace.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "products")
public class ProductEntity {
    @Id
    private UUID id;
    @Column(nullable = false)
    private String name;
    private String description;
    @Column(nullable = false)
    private BigDecimal price;
    @Column(nullable = false)
    private int stock;
    @Column(nullable = false)
    private String category;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private ProductStatus status;
    @Column(name = "seller_id", nullable = false)
    private UUID sellerId;
    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;
    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;

    protected ProductEntity() {
    }

    public ProductEntity(UUID id, String name, String description, BigDecimal price, int stock, String category,
                         ProductStatus status, UUID sellerId, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
        this.id = id;
        this.name = name;
        this.description = description;
        this.price = price;
        this.stock = stock;
        this.category = category;
        this.status = status;
        this.sellerId = sellerId;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public UUID getId() { return id; }
    public String getName() { return name; }
    public String getDescription() { return description; }
    public BigDecimal getPrice() { return price; }
    public int getStock() { return stock; }
    public String getCategory() { return category; }
    public ProductStatus getStatus() { return status; }
    public UUID getSellerId() { return sellerId; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }

    public void update(String name, String description, BigDecimal price, int stock, String category, ProductStatus status) {
        this.name = name;
        this.description = description;
        this.price = price;
        this.stock = stock;
        this.category = category;
        this.status = status;
        this.updatedAt = OffsetDateTime.now();
    }

    public void setStock(int stock) {
        this.stock = stock;
        this.updatedAt = OffsetDateTime.now();
    }

    public void archive() {
        this.status = ProductStatus.ARCHIVED;
        this.updatedAt = OffsetDateTime.now();
    }
}
