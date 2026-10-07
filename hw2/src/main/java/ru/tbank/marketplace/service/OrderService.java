package ru.tbank.marketplace.service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import ru.tbank.marketplace.api.model.ApiError.ErrorCodeEnum;
import ru.tbank.marketplace.api.model.OrderCreate;
import ru.tbank.marketplace.api.model.OrderItemRequest;
import ru.tbank.marketplace.api.model.OrderItemResponse;
import ru.tbank.marketplace.api.model.OrderResponse;
import ru.tbank.marketplace.api.model.OrderUpdate;
import ru.tbank.marketplace.domain.DiscountType;
import ru.tbank.marketplace.domain.OperationType;
import ru.tbank.marketplace.domain.OrderEntity;
import ru.tbank.marketplace.domain.OrderItemEntity;
import ru.tbank.marketplace.domain.OrderStatus;
import ru.tbank.marketplace.domain.ProductEntity;
import ru.tbank.marketplace.domain.ProductStatus;
import ru.tbank.marketplace.domain.PromoCodeEntity;
import ru.tbank.marketplace.domain.Role;
import ru.tbank.marketplace.domain.UserOperationEntity;
import ru.tbank.marketplace.error.ApiException;
import ru.tbank.marketplace.repository.OrderRepository;
import ru.tbank.marketplace.repository.ProductRepository;
import ru.tbank.marketplace.repository.PromoCodeRepository;
import ru.tbank.marketplace.repository.UserOperationRepository;
import ru.tbank.marketplace.security.CurrentUser;

@Service
public class OrderService {
    private static final Set<OrderStatus> ACTIVE_STATUSES = Set.of(OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING);
    private final OrderRepository orders;
    private final ProductRepository products;
    private final PromoCodeRepository promoCodes;
    private final UserOperationRepository operations;
    private final long limitMinutes;

    public OrderService(OrderRepository orders, ProductRepository products, PromoCodeRepository promoCodes,
                        UserOperationRepository operations, @Value("${app.order-limit-minutes}") long limitMinutes) {
        this.orders = orders;
        this.products = products;
        this.promoCodes = promoCodes;
        this.operations = operations;
        this.limitMinutes = limitMinutes;
    }

    @Transactional
    public OrderResponse create(OrderCreate request, CurrentUser user) {
        checkRateLimit(user.id(), OperationType.CREATE_ORDER);
        if (orders.existsByUserIdAndStatusIn(user.id(), ACTIVE_STATUSES)) {
            throw new ApiException(ErrorCodeEnum.ORDER_HAS_ACTIVE, HttpStatus.CONFLICT,
                "User already has an active order");
        }
        List<ProductEntity> lockedProducts = lockProducts(request.getItems());
        checkCatalog(lockedProducts);
        Map<UUID, Integer> quantities = quantities(request.getItems());
        checkStock(lockedProducts, quantities);
        lockedProducts.forEach(product -> product.setStock(product.getStock() - quantities.get(product.getId())));
        List<OrderItemEntity> items = createItems(request.getItems(), lockedProducts);
        BigDecimal subtotal = subtotal(items);
        Discount discount = applyNewPromo(request.getPromoCode(), subtotal);
        OffsetDateTime now = OffsetDateTime.now();
        OrderEntity order = new OrderEntity(UUID.randomUUID(), user.id(), OrderStatus.CREATED, discount.promo(),
            subtotal.subtract(discount.amount()), discount.amount(), now);
        order.replaceItems(items);
        orders.save(order);
        operations.save(new UserOperationEntity(UUID.randomUUID(), user.id(), OperationType.CREATE_ORDER, now));
        return toResponse(order);
    }

    @Transactional(readOnly = true)
    public OrderResponse get(UUID id, CurrentUser user) {
        OrderEntity order = orders.findWithItemsById(id).orElseThrow(this::orderNotFound);
        checkOwner(order, user);
        return toResponse(order);
    }

    @Transactional
    public OrderResponse update(UUID id, OrderUpdate request, CurrentUser user) {
        OrderEntity order = orders.findLockedById(id).orElseThrow(this::orderNotFound);
        checkOwner(order, user);
        if (order.getStatus() != OrderStatus.CREATED) {
            throw invalidTransition();
        }
        checkRateLimit(user.id(), OperationType.UPDATE_ORDER);
        Map<UUID, ProductEntity> lockedProducts = lockProductsForUpdate(order, request.getItems());
        order.getItems().forEach(item -> {
            ProductEntity product = lockedProducts.get(item.getProductId());
            product.setStock(product.getStock() + item.getQuantity());
        });
        List<ProductEntity> requestedProducts = request.getItems().stream()
            .map(item -> lockedProducts.get(item.getProductId())).distinct().toList();
        checkCatalog(requestedProducts);
        Map<UUID, Integer> quantities = quantities(request.getItems());
        checkStock(requestedProducts, quantities);
        requestedProducts.forEach(product ->
            product.setStock(product.getStock() - quantities.get(product.getId())));
        List<OrderItemEntity> items = createItems(request.getItems(), requestedProducts);
        BigDecimal subtotal = subtotal(items);
        Discount discount = recalculatePromo(order.getPromoCode(), subtotal);
        order.replaceItems(items);
        order.applyTotals(subtotal.subtract(discount.amount()), discount.amount(), discount.promo());
        operations.save(new UserOperationEntity(UUID.randomUUID(), user.id(), OperationType.UPDATE_ORDER,
            OffsetDateTime.now()));
        return toResponse(order);
    }

    @Transactional
    public OrderResponse cancel(UUID id, CurrentUser user) {
        OrderEntity order = orders.findLockedById(id).orElseThrow(this::orderNotFound);
        checkOwner(order, user);
        if (!ACTIVE_STATUSES.contains(order.getStatus())) {
            throw invalidTransition();
        }
        List<UUID> productIds = order.getItems().stream().map(OrderItemEntity::getProductId).distinct().sorted().toList();
        Map<UUID, ProductEntity> lockedProducts = new LinkedHashMap<>();
        productIds.forEach(productId -> lockedProducts.put(productId,
            products.findLockedById(productId).orElseThrow(() -> productNotFound(productId))));
        order.getItems().forEach(item -> {
            ProductEntity product = lockedProducts.get(item.getProductId());
            product.setStock(product.getStock() + item.getQuantity());
        });
        if (order.getPromoCode() != null) {
            order.getPromoCode().decrementUses();
        }
        order.cancel();
        return toResponse(order);
    }

    private List<ProductEntity> lockProducts(List<OrderItemRequest> items) {
        return items.stream().map(OrderItemRequest::getProductId).distinct().sorted()
            .map(id -> products.findLockedById(id).orElseThrow(() -> productNotFound(id))).toList();
    }

    private Map<UUID, ProductEntity> lockProductsForUpdate(OrderEntity order, List<OrderItemRequest> items) {
        return java.util.stream.Stream.concat(
                order.getItems().stream().map(OrderItemEntity::getProductId),
                items.stream().map(OrderItemRequest::getProductId))
            .distinct().sorted().collect(java.util.stream.Collectors.toMap(id -> id,
                id -> products.findLockedById(id).orElseThrow(() -> productNotFound(id)),
                (first, second) -> first, LinkedHashMap::new));
    }

    private void checkCatalog(List<ProductEntity> lockedProducts) {
        lockedProducts.stream().filter(product -> product.getStatus() != ProductStatus.ACTIVE).findFirst()
            .ifPresent(product -> {
                throw new ApiException(ErrorCodeEnum.PRODUCT_INACTIVE, HttpStatus.CONFLICT,
                    "Product is not active", Map.of("product_id", product.getId()));
            });
    }

    private void checkStock(List<ProductEntity> lockedProducts, Map<UUID, Integer> quantities) {
        List<Map<String, Object>> insufficient = lockedProducts.stream()
            .filter(product -> product.getStock() < quantities.get(product.getId()))
            .map(product -> Map.<String, Object>of(
                "product_id", product.getId(),
                "requested", quantities.get(product.getId()),
                "available", product.getStock()))
            .toList();
        if (!insufficient.isEmpty()) {
            throw new ApiException(ErrorCodeEnum.INSUFFICIENT_STOCK, HttpStatus.CONFLICT,
                "Insufficient stock", Map.of("products", insufficient));
        }
    }

    private Map<UUID, Integer> quantities(List<OrderItemRequest> items) {
        Map<UUID, Integer> result = new LinkedHashMap<>();
        items.forEach(item -> result.merge(item.getProductId(), item.getQuantity(), Integer::sum));
        return result;
    }

    private List<OrderItemEntity> createItems(List<OrderItemRequest> requested, List<ProductEntity> lockedProducts) {
        Map<UUID, ProductEntity> byId = lockedProducts.stream()
            .collect(java.util.stream.Collectors.toMap(ProductEntity::getId, product -> product));
        return requested.stream().map(item -> new OrderItemEntity(UUID.randomUUID(), item.getProductId(),
            item.getQuantity(), byId.get(item.getProductId()).getPrice())).toList();
    }

    private BigDecimal subtotal(List<OrderItemEntity> items) {
        return items.stream()
            .map(item -> item.getPriceAtOrder().multiply(BigDecimal.valueOf(item.getQuantity())))
            .reduce(BigDecimal.ZERO, BigDecimal::add).setScale(2, RoundingMode.HALF_UP);
    }

    private Discount applyNewPromo(String code, BigDecimal subtotal) {
        if (code == null) {
            return new Discount(null, BigDecimal.ZERO.setScale(2));
        }
        PromoCodeEntity promo = promoCodes.findLockedByCode(code).orElseThrow(this::invalidPromo);
        validatePromo(promo, subtotal, true);
        promo.incrementUses();
        return new Discount(promo, calculateDiscount(promo, subtotal));
    }

    private Discount recalculatePromo(PromoCodeEntity promo, BigDecimal subtotal) {
        if (promo == null) {
            return new Discount(null, BigDecimal.ZERO.setScale(2));
        }
        if (subtotal.compareTo(promo.getMinOrderAmount()) < 0) {
            promo.decrementUses();
            return new Discount(null, BigDecimal.ZERO.setScale(2));
        }
        validatePromo(promo, subtotal, false);
        return new Discount(promo, calculateDiscount(promo, subtotal));
    }

    private void validatePromo(PromoCodeEntity promo, BigDecimal subtotal, boolean checkUses) {
        OffsetDateTime now = OffsetDateTime.now();
        if (!promo.isActive() || now.isBefore(promo.getValidFrom()) || now.isAfter(promo.getValidUntil())
            || checkUses && promo.getCurrentUses() >= promo.getMaxUses()) {
            throw invalidPromo();
        }
        if (subtotal.compareTo(promo.getMinOrderAmount()) < 0) {
            throw new ApiException(ErrorCodeEnum.PROMO_CODE_MIN_AMOUNT, HttpStatus.UNPROCESSABLE_ENTITY,
                "Order amount is below promo code minimum",
                Map.of("minimum", promo.getMinOrderAmount(), "actual", subtotal));
        }
    }

    private BigDecimal calculateDiscount(PromoCodeEntity promo, BigDecimal subtotal) {
        BigDecimal amount = promo.getDiscountType() == DiscountType.PERCENTAGE
            ? subtotal.multiply(promo.getDiscountValue()).divide(BigDecimal.valueOf(100), 2, RoundingMode.HALF_UP)
            : promo.getDiscountValue();
        BigDecimal maximum = promo.getDiscountType() == DiscountType.PERCENTAGE
            ? subtotal.multiply(new BigDecimal("0.70")) : subtotal;
        return amount.min(maximum).setScale(2, RoundingMode.HALF_UP);
    }

    private void checkRateLimit(UUID userId, OperationType type) {
        OffsetDateTime threshold = OffsetDateTime.now().minusMinutes(limitMinutes);
        operations.findTopByUserIdAndOperationTypeOrderByCreatedAtDesc(userId, type)
            .filter(operation -> operation.getCreatedAt().isAfter(threshold))
            .ifPresent(operation -> {
                throw new ApiException(ErrorCodeEnum.ORDER_LIMIT_EXCEEDED, HttpStatus.TOO_MANY_REQUESTS,
                    "Order operation rate limit exceeded");
            });
    }

    private void checkOwner(OrderEntity order, CurrentUser user) {
        if (user.role() != Role.ADMIN && !order.getUserId().equals(user.id())) {
            throw new ApiException(ErrorCodeEnum.ORDER_OWNERSHIP_VIOLATION, HttpStatus.FORBIDDEN,
                "Order belongs to another user");
        }
    }

    private ApiException orderNotFound() {
        return new ApiException(ErrorCodeEnum.ORDER_NOT_FOUND, HttpStatus.NOT_FOUND, "Order not found");
    }

    private ApiException productNotFound(UUID id) {
        return new ApiException(ErrorCodeEnum.PRODUCT_NOT_FOUND, HttpStatus.NOT_FOUND, "Product not found",
            Map.of("product_id", id));
    }

    private ApiException invalidTransition() {
        return new ApiException(ErrorCodeEnum.INVALID_STATE_TRANSITION, HttpStatus.CONFLICT,
            "Order state transition is not allowed");
    }

    private ApiException invalidPromo() {
        return new ApiException(ErrorCodeEnum.PROMO_CODE_INVALID, HttpStatus.UNPROCESSABLE_ENTITY,
            "Promo code is invalid");
    }

    private OrderResponse toResponse(OrderEntity order) {
        List<OrderItemResponse> items = order.getItems().stream()
            .sorted(Comparator.comparing(item -> item.getProductId().toString()))
            .map(item -> new OrderItemResponse(item.getProductId(), item.getQuantity(), item.getPriceAtOrder()))
            .toList();
        return new OrderResponse(order.getId(), order.getUserId(),
            ru.tbank.marketplace.api.model.OrderStatus.fromValue(order.getStatus().name()), items,
            order.getTotalAmount(), order.getDiscountAmount(), order.getCreatedAt(), order.getUpdatedAt())
            .promoCode(order.getPromoCode() == null ? null : order.getPromoCode().getCode());
    }

    private record Discount(PromoCodeEntity promo, BigDecimal amount) {
    }
}
