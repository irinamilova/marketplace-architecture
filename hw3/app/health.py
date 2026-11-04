import os
import sys
import urllib.request
import grpc
import psycopg
import flight_pb2 as pb
import flight_pb2_grpc as rpc

if sys.argv[1] == 'flight':
    with grpc.insecure_channel('localhost:50051') as channel:
        rpc.FlightServiceStub(channel).GetFlight(
            pb.GetFlightRequest(flight_id='11111111-1111-4111-8111-111111111111'),
            metadata=(('x-api-key', os.environ['GRPC_API_KEY']),), timeout=2)
else:
    urllib.request.urlopen('http://localhost:8000/openapi.json', timeout=2).read()
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=2) as conn:
        conn.execute('SELECT 1')
