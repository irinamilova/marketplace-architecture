package ru.tbank.marketplace.service;

import java.time.OffsetDateTime;
import java.util.UUID;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import ru.tbank.marketplace.api.model.ApiError.ErrorCodeEnum;
import ru.tbank.marketplace.api.model.ProductCreate;
import ru.tbank.marketplace.api.model.ProductPage;
import ru.tbank.marketplace.api.model.ProductResponse;
import ru.tbank.marketplace.api.model.ProductUpdate;
import ru.tbank.marketplace.domain.ProductEntity;
import ru.tbank.marketplace.domain.Role;
import ru.tbank.marketplace.error.ApiException;
import ru.tbank.marketplace.repository.ProductRepository;
import ru.tbank.marketplace.repository.UserRepository;
import ru.tbank.marketplace.security.CurrentUser;

@Service
public class ProductService {
    private final ProductRepository products;
    private final UserRepository users;

    public ProductService(ProductRepository products, UserRepository users) {
        this.products = products;
        this.users = users;
    }

    @Transactional
    public ProductResponse create(ProductCreate request, CurrentUser user) {
        UUID sellerId = request.getSellerId() == null ? user.id() : request.getSellerId();
        if (user.role() == Role.SELLER && !sellerId.equals(user.id())) {
            throw accessDenied();
        }
        if (!users.existsById(sellerId)) {
            throw new ApiException(ErrorCodeEnum.ACCESS_DENIED, HttpStatus.FORBIDDEN, "Seller does not exist");
        }
        OffsetDateTime now = OffsetDateTime.now();
        ProductEntity product = new ProductEntity(UUID.randomUUID(), request.getName(), request.getDescription(),
            request.getPrice(), request.getStock(), request.getCategory(),
            ru.tbank.marketplace.domain.ProductStatus.valueOf(request.getStatus().getValue()), sellerId, now, now);
        return toResponse(products.save(product));
    }

    @Transactional(readOnly = true)
    public ProductResponse get(UUID id) {
        return toResponse(find(id));
    }

    @Transactional(readOnly = true)
    public ProductPage list(int page, int size, ru.tbank.marketplace.api.model.ProductStatus status, String category) {
        Specification<ProductEntity> specification = (root, query, builder) -> builder.conjunction();
        if (status != null) {
            specification = specification.and((root, query, builder) ->
                builder.equal(root.get("status"), ru.tbank.marketplace.domain.ProductStatus.valueOf(status.getValue())));
        }
        if (category != null) {
            specification = specification.and((root, query, builder) -> builder.equal(root.get("category"), category));
        }
        var result = products.findAll(specification, PageRequest.of(page, size));
        return new ProductPage(result.getContent().stream().map(this::toResponse).toList(),
            result.getTotalElements(), page, size);
    }

    @Transactional
    public ProductResponse update(UUID id, ProductUpdate request, CurrentUser user) {
        ProductEntity product = find(id);
        checkOwner(product, user);
        product.update(request.getName(), request.getDescription(), request.getPrice(), request.getStock(),
            request.getCategory(), ru.tbank.marketplace.domain.ProductStatus.valueOf(request.getStatus().getValue()));
        return toResponse(product);
    }

    @Transactional
    public void archive(UUID id, CurrentUser user) {
        ProductEntity product = find(id);
        checkOwner(product, user);
        product.archive();
    }

    private ProductEntity find(UUID id) {
        return products.findById(id).orElseThrow(() ->
            new ApiException(ErrorCodeEnum.PRODUCT_NOT_FOUND, HttpStatus.NOT_FOUND, "Product not found"));
    }

    private void checkOwner(ProductEntity product, CurrentUser user) {
        if (user.role() != Role.ADMIN && !product.getSellerId().equals(user.id())) {
            throw accessDenied();
        }
    }

    private ApiException accessDenied() {
        return new ApiException(ErrorCodeEnum.ACCESS_DENIED, HttpStatus.FORBIDDEN, "Access denied");
    }

    private ProductResponse toResponse(ProductEntity product) {
        return new ProductResponse(product.getId(), product.getName(), product.getPrice(), product.getStock(),
            product.getCategory(), ru.tbank.marketplace.api.model.ProductStatus.fromValue(product.getStatus().name()),
            product.getSellerId(), product.getCreatedAt(), product.getUpdatedAt())
            .description(product.getDescription());
    }
}
