package ru.tbank.marketplace.controller;

import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RestController;
import ru.tbank.marketplace.api.ProductsApi;
import ru.tbank.marketplace.api.model.ProductCreate;
import ru.tbank.marketplace.api.model.ProductPage;
import ru.tbank.marketplace.api.model.ProductResponse;
import ru.tbank.marketplace.api.model.ProductStatus;
import ru.tbank.marketplace.api.model.ProductUpdate;
import ru.tbank.marketplace.security.SecurityContext;
import ru.tbank.marketplace.service.ProductService;

@RestController
public class ProductController implements ProductsApi {
    private final ProductService productService;

    public ProductController(ProductService productService) {
        this.productService = productService;
    }

    @Override
    public ResponseEntity<ProductResponse> createProduct(ProductCreate request) {
        return ResponseEntity.status(HttpStatus.CREATED)
            .body(productService.create(request, SecurityContext.currentUser()));
    }

    @Override
    public ResponseEntity<ProductResponse> getProduct(UUID id) {
        return ResponseEntity.ok(productService.get(id));
    }

    @Override
    public ResponseEntity<ProductPage> listProducts(Integer page, Integer size, ProductStatus status, String category) {
        return ResponseEntity.ok(productService.list(page, size, status, category));
    }

    @Override
    public ResponseEntity<ProductResponse> updateProduct(UUID id, ProductUpdate request) {
        return ResponseEntity.ok(productService.update(id, request, SecurityContext.currentUser()));
    }

    @Override
    public ResponseEntity<Void> deleteProduct(UUID id) {
        productService.archive(id, SecurityContext.currentUser());
        return ResponseEntity.noContent().build();
    }
}
