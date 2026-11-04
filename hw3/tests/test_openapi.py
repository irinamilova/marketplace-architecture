"""Guard against Swagger showing only success + automatic validation errors."""
from uuid import uuid4
import grpc
import psycopg
import pytest
from fastapi.testclient import TestClient
from app.booking import app
from app.api_errors import ErrorResponse

EXPECTED = [
    ('/flights', 'get', {200, 400, 422, 502, 503, 504}),
    ('/flights/{flight_id}', 'get', {200, 404, 422, 502, 503, 504}),
    ('/bookings', 'post', {201, 400, 404, 409, 422, 502, 503, 504}),
    ('/bookings', 'get', {200, 422, 503}),
    ('/bookings/{booking_id}', 'get', {200, 404, 422, 503}),
    ('/bookings/{booking_id}/cancel', 'post', {200, 404, 409, 422, 502, 503, 504}),
]


@pytest.mark.parametrize('path,method,codes', EXPECTED)
def test_documented_responses(path, method, codes):
    schema = app.openapi()
    documented = schema['paths'][path][method]['responses']
    assert set(map(int, documented)) == codes
    for code in codes - {200, 201, 422}:
        content = documented[str(code)]['content']['application/json']
        assert content['schema'] == {'$ref': '#/components/schemas/ErrorResponse'}
        assert content['examples']
        for example in content['examples'].values():
            ErrorResponse.model_validate(example['value'])
    if 422 in codes:
        assert documented['422']['content']['application/json']['schema']['$ref'].endswith('/HTTPValidationError')


@pytest.mark.parametrize('code,status', [
    (grpc.StatusCode.INVALID_ARGUMENT,400), (grpc.StatusCode.NOT_FOUND,404),
    (grpc.StatusCode.RESOURCE_EXHAUSTED,409), (grpc.StatusCode.ALREADY_EXISTS,409),
    (grpc.StatusCode.FAILED_PRECONDITION,409), (grpc.StatusCode.UNAUTHENTICATED,502),
    (grpc.StatusCode.INTERNAL,502), (grpc.StatusCode.UNAVAILABLE,503),
    (grpc.StatusCode.DEADLINE_EXCEEDED,504),
])
def test_runtime_grpc_errors_match_documented_contract(monkeypatch, code, status):
    class Failure(grpc.RpcError):
        def code(self):
            return code
        def details(self):
            return 'Controlled transport failure'
    class Flights:
        def GetFlight(self, request):
            raise Failure()
    monkeypatch.setattr(app.state,'flights',Flights(),raising=False)
    # No lifespan: failure injection does not start external dependencies.
    response = TestClient(app).post('/bookings',json={
        'user_id':'test','flight_id':str(uuid4()),'passenger_name':'Test',
        'passenger_email':'test@example.com','seat_count':1,
    })
    assert response.status_code == status
    assert response.json() == {'error':code.name,'detail':'Controlled transport failure'}
    assert str(status) in app.openapi()['paths']['/bookings']['post']['responses']


def test_runtime_database_error_matches_contract(monkeypatch):
    class Database:
        def connection(self):
            raise psycopg.OperationalError('Controlled database outage')
    monkeypatch.setattr(app.state,'db',Database(),raising=False)
    response = TestClient(app).get('/bookings',params={'user_id':'test'})
    assert response.status_code == 503
    assert ErrorResponse.model_validate(response.json()).error == 'DATABASE_UNAVAILABLE'


def test_real_swagger_schema_and_local_errors():
    import httpx
    with httpx.Client(base_url='http://localhost:8000') as client:
        assert client.get('/docs').status_code == 200
        schema = client.get('/openapi.json').json()
        for path, method, codes in EXPECTED:
            assert set(map(int,schema['paths'][path][method]['responses'])) == codes
        response = client.get('/bookings/' + str(uuid4()))
        assert response.status_code == 404
        assert response.json() == {'error':'NOT_FOUND','detail':'Booking not found'}
        response = client.get('/flights',params={'origin':'SVO','destination':'SVO'})
        assert response.status_code == 400
        assert response.json()['error'] == 'INVALID_ARGUMENT'
        response = client.post('/bookings',json={})
        assert response.status_code == 422
        assert len(response.json()['detail']) == 5


def test_only_required_rest_operations_and_status_enums():
    schema = app.openapi()
    assert {(p,m) for p,methods in schema['paths'].items() for m in methods} == {(p,m) for p,m,_ in EXPECTED}
    definitions = schema['components']['schemas']
    assert definitions['BookingResponse']['properties']['status']['enum'] == ['CONFIRMED','CANCELLED']
    assert definitions['FlightResponse']['properties']['status']['enum'] == ['SCHEDULED','DEPARTED','CANCELLED','COMPLETED']
