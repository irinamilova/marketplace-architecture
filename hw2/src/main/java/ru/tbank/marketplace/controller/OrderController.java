package ru.tbank.marketplace.controller;

import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RestController;
import ru.tbank.marketplace.api.OrdersApi;
import ru.tbank.marketplace.api.model.OrderCreate;
import ru.tbank.marketplace.api.model.OrderResponse;
import ru.tbank.marketplace.api.model.OrderUpdate;
import ru.tbank.marketplace.security.SecurityContext;
import ru.tbank.marketplace.service.OrderService;

@RestController
public class OrderController implements OrdersApi {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @Override
    public ResponseEntity<OrderResponse> createOrder(OrderCreate request) {
        return ResponseEntity.status(HttpStatus.CREATED)
            .body(orderService.create(request, SecurityContext.currentUser()));
    }

    @Override
    public ResponseEntity<OrderResponse> getOrder(UUID id) {
        return ResponseEntity.ok(orderService.get(id, SecurityContext.currentUser()));
    }

    @Override
    public ResponseEntity<OrderResponse> updateOrder(UUID id, OrderUpdate request) {
        return ResponseEntity.ok(orderService.update(id, request, SecurityContext.currentUser()));
    }

    @Override
    public ResponseEntity<OrderResponse> cancelOrder(UUID id) {
        return ResponseEntity.ok(orderService.cancel(id, SecurityContext.currentUser()));
    }
}
