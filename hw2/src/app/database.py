import os
from contextlib import contextmanager

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, create_engine
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship, sessionmaker


DATABASE_URL = os.getenv(
    "DB_URL", "postgresql+psycopg://marketplace:marketplace@localhost:5432/marketplace"
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    email = mapped_column(String(320), nullable=False, unique=True)
    password_hash = mapped_column(String(100), nullable=False)
    role = mapped_column(String(16), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)


class Product(Base):
    __tablename__ = "products"

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    name = mapped_column(String(255), nullable=False)
    description = mapped_column(String(4000))
    price = mapped_column(Numeric(12, 2), nullable=False)
    stock = mapped_column(Integer, nullable=False)
    category = mapped_column(String(100), nullable=False)
    status = mapped_column(String(16), nullable=False)
    seller_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    code = mapped_column(String(20), nullable=False, unique=True)
    discount_type = mapped_column(String(20), nullable=False)
    discount_value = mapped_column(Numeric(12, 2), nullable=False)
    min_order_amount = mapped_column(Numeric(12, 2), nullable=False)
    max_uses = mapped_column(Integer, nullable=False)
    current_uses = mapped_column(Integer, nullable=False)
    valid_from = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until = mapped_column(DateTime(timezone=True), nullable=False)
    active = mapped_column(Boolean, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status = mapped_column(String(24), nullable=False)
    promo_code_id = mapped_column(UUID(as_uuid=True), ForeignKey("promo_codes.id"))
    total_amount = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount = mapped_column(Numeric(12, 2), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False)
    promo_code = relationship(PromoCode)
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan", lazy="selectin")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    order_id = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    quantity = mapped_column(Integer, nullable=False)
    price_at_order = mapped_column(Numeric(12, 2), nullable=False)
    order = relationship(Order, back_populates="items")


class UserOperation(Base):
    __tablename__ = "user_operations"

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    operation_type = mapped_column(String(20), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)


@contextmanager
def transaction():
    with SessionLocal.begin() as session:
        yield session
