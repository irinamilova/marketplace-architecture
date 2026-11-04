from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    detail: str


ERRORS = {
    400: ('Некорректный запрос', {
        'invalid_argument': ('INVALID_ARGUMENT', 'Use distinct three-letter uppercase IATA codes'),
        'price_overflow': ('BAD_REQUEST', 'Total price exceeds supported range'),
    }),
    404: ('Рейс или бронирование не найдены', {
        'flight': ('NOT_FOUND', 'Flight not found'),
        'booking': ('NOT_FOUND', 'Booking not found'),
    }),
    409: ('Конфликт резервирования', {
        'booking_cancelled': ('FAILED_PRECONDITION', 'Booking must be CONFIRMED to cancel'),
        'no_seats': ('RESOURCE_EXHAUSTED', 'Not enough available seats'),
        'flight_status': ('FAILED_PRECONDITION', 'Flight is not scheduled'),
        'released': ('FAILED_PRECONDITION', 'Booking has already been released'),
        'duplicate': ('ALREADY_EXISTS', 'booking_id reused with different parameters'),
    }),
    502: ('Ошибка межсервисного взаимодействия', {
        'credentials': ('UNAUTHENTICATED', 'Valid service API key required'),
        'upstream': ('INTERNAL', 'Flight database operation failed'),
    }),
    503: ('Сервис временно недоступен', {
        'circuit_open': ('UNAVAILABLE', 'Flight service circuit is OPEN; retry later'),
        'recovery_probe': ('UNAVAILABLE', 'Flight service recovery probe is in progress; retry later'),
        'flight_database': ('UNAVAILABLE', 'Flight database unavailable'),
        'booking_database': ('DATABASE_UNAVAILABLE', 'Booking database operation failed; pending operations will be reconciled'),
        'compensated': ('UNAVAILABLE', 'Creation was already compensated; submit a new request'),
    }),
    504: ('Истёк таймаут Flight Service после повторных попыток', {
        'deadline': ('DEADLINE_EXCEEDED', 'Deadline Exceeded'),
    }),
}


def responses(*codes, examples=None):
    result = {}
    for code in codes:
        description, variants = ERRORS[code]
        selected = examples.get(code) if examples else None
        result[code] = {
            'model': ErrorResponse,
            'description': description,
            'content': {'application/json': {'examples': {
                name: {'value': {'error': value[0], 'detail': value[1]}}
                for name, value in variants.items() if selected is None or name in selected
            }}},
        }
    return result
