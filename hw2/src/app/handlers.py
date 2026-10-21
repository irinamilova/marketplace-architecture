import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from fastapi import Response
from sqlalchemy import func, select

from app.database import Order, OrderItem, Product, PromoCode, User, UserOperation, transaction
from app.errors import ApiException
from app.security import CurrentUser, create_tokens, decode_token, hash_password, require_role, verify_password


ORDER_LIMIT_MINUTES = int(os.getenv("ORDER_LIMIT_MINUTES", "1"))
ACTIVE_ORDER_STATUSES = {"CREATED", "PAYMENT_PENDING"}
ZERO = Decimal("0.00")


def now() -> datetime:
    return datetime.now(timezone.utc)


def validation(field: str, message: str) -> ApiException:
    return ApiException(
        "VALIDATION_ERROR",
        400,
        "Request validation failed",
        {"violations": [{"field": field, "message": message}]},
    )


def product_response(product: Product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "price": product.price,
        "stock": product.stock,
        "category": product.category,
        "status": product.status,
        "seller_id": product.seller_id,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


def order_response(order: Order) -> dict:
    return {
        "id": order.id,
        "user_id": order.user_id,
        "status": order.status,
        "items": [
            {
                "product_id": item.product_id,
                "quantity": item.quantity,
                "price_at_order": item.price_at_order,
            }
            for item in sorted(order.items, key=lambda value: str(value.product_id))
        ],
        "promo_code": order.promo_code.code if order.promo_code else None,
        "total_amount": order.total_amount,
        "discount_amount": order.discount_amount,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def register(request):
    email = str(request.email).lower()
    with transaction() as session:
        if session.scalar(select(User).where(User.email == email)):
            raise validation("email", "already registered")
        user = User(
            id=uuid4(),
            email=email,
            password_hash=hash_password(request.password),
            role=request.role.value,
            created_at=now(),
        )
        session.add(user)
        session.flush()
        return create_tokens(user.id, user.role)


def login(request):
    email = str(request.email).lower()
    with transaction() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None or not verify_password(request.password, user.password_hash):
            raise ApiException("TOKEN_INVALID", 401, "Email or password is invalid")
        return create_tokens(user.id, user.role)


def refresh(request):
    payload = decode_token(request.refresh_token, "refresh")
    try:
        user_id = UUID(payload["sub"])
    except (KeyError, TypeError, ValueError) as exception:
        raise ApiException("REFRESH_TOKEN_INVALID", 401, "Refresh token is invalid") from exception
    with transaction() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ApiException("REFRESH_TOKEN_INVALID", 401, "Refresh token is invalid")
        return create_tokens(user.id, user.role)


def create_product(request, current_user: CurrentUser):
    require_role(current_user, "SELLER", "ADMIN")
    seller_id = request.seller_id or current_user.id
    if current_user.role == "SELLER" and seller_id != current_user.id:
        raise ApiException("ACCESS_DENIED", 403, "Access denied")
    with transaction() as session:
        if session.get(User, seller_id) is None:
            raise ApiException("ACCESS_DENIED", 403, "Seller does not exist")
        timestamp = now()
        product = Product(
            id=uuid4(),
            name=request.name,
            description=request.description,
            price=request.price,
            stock=request.stock,
            category=request.category,
            status=request.status.value,
            seller_id=seller_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(product)
        session.flush()
        return product_response(product)


def find_product(session, product_id: UUID) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise ApiException("PRODUCT_NOT_FOUND", 404, "Product not found")
    return product


def get_product(id: UUID, current_user: CurrentUser):
    with transaction() as session:
        return product_response(find_product(session, id))


def list_products(current_user: CurrentUser, page: int = 0, size: int = 20, status=None, category=None):
    with transaction() as session:
        conditions = []
        if status is not None:
            conditions.append(Product.status == status.value)
        if category is not None:
            conditions.append(Product.category == category)
        total = session.scalar(select(func.count()).select_from(Product).where(*conditions))
        products = session.scalars(
            select(Product).where(*conditions).order_by(Product.created_at).offset(page * size).limit(size)
        ).all()
        return {
            "items": [product_response(product) for product in products],
            "totalElements": total,
            "page": page,
            "size": size,
        }


def check_product_owner(product: Product, user: CurrentUser) -> None:
    if user.role != "ADMIN" and product.seller_id != user.id:
        raise ApiException("ACCESS_DENIED", 403, "Access denied")


def update_product(id: UUID, request, current_user: CurrentUser):
    require_role(current_user, "SELLER", "ADMIN")
    with transaction() as session:
        product = find_product(session, id)
        check_product_owner(product, current_user)
        product.name = request.name
        product.description = request.description
        product.price = request.price
        product.stock = request.stock
        product.category = request.category
        product.status = request.status.value
        product.updated_at = now()
        session.flush()
        return product_response(product)


def delete_product(id: UUID, current_user: CurrentUser):
    require_role(current_user, "SELLER", "ADMIN")
    with transaction() as session:
        product = find_product(session, id)
        check_product_owner(product, current_user)
        product.status = "ARCHIVED"
        product.updated_at = now()
    return Response(status_code=204)


def create_promo_code(request, current_user: CurrentUser):
    require_role(current_user, "SELLER", "ADMIN")
    if request.valid_until <= request.valid_from:
        raise validation("valid_until", "must be after valid_from")
    with transaction() as session:
        if session.scalar(select(PromoCode).where(PromoCode.code == request.code)):
            raise validation("code", "already exists")
        promo = PromoCode(
            id=uuid4(),
            code=request.code,
            discount_type=request.discount_type.value,
            discount_value=request.discount_value,
            min_order_amount=request.min_order_amount,
            max_uses=request.max_uses,
            current_uses=0,
            valid_from=request.valid_from,
            valid_until=request.valid_until,
            active=request.active,
            created_at=now(),
        )
        session.add(promo)
        session.flush()
        return {
            "id": promo.id,
            "code": promo.code,
            "discount_type": promo.discount_type,
            "discount_value": promo.discount_value,
            "min_order_amount": promo.min_order_amount,
            "max_uses": promo.max_uses,
            "current_uses": promo.current_uses,
            "valid_from": promo.valid_from,
            "valid_until": promo.valid_until,
            "active": promo.active,
        }


def check_rate_limit(session, user_id: UUID, operation_type: str) -> None:
    operation = session.scalar(
        select(UserOperation)
        .where(UserOperation.user_id == user_id, UserOperation.operation_type == operation_type)
        .order_by(UserOperation.created_at.desc())
        .limit(1)
    )
    if operation and operation.created_at > now() - timedelta(minutes=ORDER_LIMIT_MINUTES):
        raise ApiException("ORDER_LIMIT_EXCEEDED", 429, "Order operation rate limit exceeded")


def quantities(items) -> dict[UUID, int]:
    result = defaultdict(int)
    for item in items:
        result[item.product_id] += item.quantity
    return dict(result)


def lock_products(session, product_ids) -> dict[UUID, Product]:
    identifiers = sorted(set(product_ids), key=str)
    products = session.scalars(
        select(Product).where(Product.id.in_(identifiers)).order_by(Product.id).with_for_update()
    ).all()
    found = {product.id: product for product in products}
    for product_id in identifiers:
        if product_id not in found:
            raise ApiException(
                "PRODUCT_NOT_FOUND", 404, "Product not found", {"product_id": str(product_id)}
            )
    return found


def check_catalog(products: dict[UUID, Product], product_ids) -> None:
    for product_id in product_ids:
        if products[product_id].status != "ACTIVE":
            raise ApiException(
                "PRODUCT_INACTIVE", 409, "Product is not active", {"product_id": str(product_id)}
            )


def check_stock(products: dict[UUID, Product], requested: dict[UUID, int]) -> None:
    insufficient = [
        {
            "product_id": str(product_id),
            "requested": quantity,
            "available": products[product_id].stock,
        }
        for product_id, quantity in requested.items()
        if products[product_id].stock < quantity
    ]
    if insufficient:
        raise ApiException(
            "INSUFFICIENT_STOCK", 409, "Insufficient stock", {"products": insufficient}
        )


def validate_promo(promo: PromoCode, subtotal: Decimal, check_uses: bool) -> None:
    timestamp = now()
    invalid = (
        not promo.active
        or timestamp < promo.valid_from
        or timestamp > promo.valid_until
        or check_uses and promo.current_uses >= promo.max_uses
    )
    if invalid:
        raise ApiException("PROMO_CODE_INVALID", 422, "Promo code is invalid")
    if subtotal < promo.min_order_amount:
        raise ApiException(
            "PROMO_CODE_MIN_AMOUNT",
            422,
            "Order amount is below promo code minimum",
            {"minimum": str(promo.min_order_amount), "actual": str(subtotal)},
        )


def discount_amount(promo: PromoCode, subtotal: Decimal) -> Decimal:
    if promo.discount_type == "PERCENTAGE":
        discount = subtotal * promo.discount_value / Decimal("100")
        maximum = subtotal * Decimal("0.70")
    else:
        discount = promo.discount_value
        maximum = subtotal
    return min(discount, maximum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def apply_promo(session, code: str | None, subtotal: Decimal):
    if code is None:
        return None, ZERO
    promo = session.scalar(select(PromoCode).where(PromoCode.code == code).with_for_update())
    if promo is None:
        raise ApiException("PROMO_CODE_INVALID", 422, "Promo code is invalid")
    validate_promo(promo, subtotal, True)
    promo.current_uses += 1
    return promo, discount_amount(promo, subtotal)


def check_order_owner(order: Order, user: CurrentUser) -> None:
    if user.role != "ADMIN" and order.user_id != user.id:
        raise ApiException("ORDER_OWNERSHIP_VIOLATION", 403, "Order belongs to another user")


def find_order(session, order_id: UUID, locked: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id)
    if locked:
        query = query.with_for_update()
    order = session.scalar(query)
    if order is None:
        raise ApiException("ORDER_NOT_FOUND", 404, "Order not found")
    return order


def create_order(request, current_user: CurrentUser):
    require_role(current_user, "USER", "ADMIN")
    with transaction() as session:
        check_rate_limit(session, current_user.id, "CREATE_ORDER")
        active = session.scalar(
            select(Order.id).where(
                Order.user_id == current_user.id, Order.status.in_(ACTIVE_ORDER_STATUSES)
            ).limit(1)
        )
        if active:
            raise ApiException("ORDER_HAS_ACTIVE", 409, "User already has an active order")
        requested = quantities(request.items)
        products = lock_products(session, requested)
        check_catalog(products, requested)
        check_stock(products, requested)
        for product_id, quantity in requested.items():
            products[product_id].stock -= quantity
            products[product_id].updated_at = now()
        items = [
            OrderItem(
                id=uuid4(),
                product_id=item.product_id,
                quantity=item.quantity,
                price_at_order=products[item.product_id].price,
            )
            for item in request.items
        ]
        subtotal = sum(
            (item.price_at_order * item.quantity for item in items), ZERO
        ).quantize(Decimal("0.01"))
        promo, discount = apply_promo(session, request.promo_code, subtotal)
        timestamp = now()
        order = Order(
            id=uuid4(),
            user_id=current_user.id,
            status="CREATED",
            promo_code=promo,
            total_amount=subtotal - discount,
            discount_amount=discount,
            created_at=timestamp,
            updated_at=timestamp,
            items=items,
        )
        session.add(order)
        session.add(
            UserOperation(
                id=uuid4(),
                user_id=current_user.id,
                operation_type="CREATE_ORDER",
                created_at=timestamp,
            )
        )
        session.flush()
        return order_response(order)


def get_order(id: UUID, current_user: CurrentUser):
    require_role(current_user, "USER", "ADMIN")
    with transaction() as session:
        order = find_order(session, id)
        check_order_owner(order, current_user)
        return order_response(order)


def update_order(id: UUID, request, current_user: CurrentUser):
    require_role(current_user, "USER", "ADMIN")
    with transaction() as session:
        order = find_order(session, id, True)
        check_order_owner(order, current_user)
        if order.status != "CREATED":
            raise ApiException("INVALID_STATE_TRANSITION", 409, "Order state transition is not allowed")
        check_rate_limit(session, current_user.id, "UPDATE_ORDER")
        old_items = list(order.items)
        product_ids = [item.product_id for item in old_items] + [item.product_id for item in request.items]
        products = lock_products(session, product_ids)
        for item in old_items:
            products[item.product_id].stock += item.quantity
            products[item.product_id].updated_at = now()
        requested = quantities(request.items)
        check_catalog(products, requested)
        check_stock(products, requested)
        for product_id, quantity in requested.items():
            products[product_id].stock -= quantity
            products[product_id].updated_at = now()
        new_items = [
            OrderItem(
                id=uuid4(),
                product_id=item.product_id,
                quantity=item.quantity,
                price_at_order=products[item.product_id].price,
            )
            for item in request.items
        ]
        subtotal = sum(
            (item.price_at_order * item.quantity for item in new_items), ZERO
        ).quantize(Decimal("0.01"))
        promo = order.promo_code
        discount = ZERO
        if promo is not None:
            if subtotal < promo.min_order_amount:
                promo.current_uses -= 1
                promo = None
            else:
                validate_promo(promo, subtotal, False)
                discount = discount_amount(promo, subtotal)
        order.items.clear()
        order.items.extend(new_items)
        order.promo_code = promo
        order.total_amount = subtotal - discount
        order.discount_amount = discount
        order.updated_at = now()
        session.add(
            UserOperation(
                id=uuid4(),
                user_id=current_user.id,
                operation_type="UPDATE_ORDER",
                created_at=order.updated_at,
            )
        )
        session.flush()
        return order_response(order)


def cancel_order(id: UUID, current_user: CurrentUser):
    require_role(current_user, "USER", "ADMIN")
    with transaction() as session:
        order = find_order(session, id, True)
        check_order_owner(order, current_user)
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise ApiException("INVALID_STATE_TRANSITION", 409, "Order state transition is not allowed")
        products = lock_products(session, [item.product_id for item in order.items])
        for item in order.items:
            products[item.product_id].stock += item.quantity
            products[item.product_id].updated_at = now()
        if order.promo_code is not None:
            order.promo_code.current_uses -= 1
        order.status = "CANCELED"
        order.updated_at = now()
        session.flush()
        return order_response(order)


HANDLERS = {
    "register": register,
    "login": login,
    "refresh": refresh,
    "listProducts": list_products,
    "createProduct": create_product,
    "getProduct": get_product,
    "updateProduct": update_product,
    "deleteProduct": delete_product,
    "createOrder": create_order,
    "getOrder": get_order,
    "updateOrder": update_order,
    "cancelOrder": cancel_order,
    "createPromoCode": create_promo_code,
}


def dispatch(operation_id: str, **arguments):
    return HANDLERS[operation_id](**arguments)
