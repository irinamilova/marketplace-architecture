FROM python:3.12-slim
WORKDIR /service
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/service:/service/generated
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p generated && python -m grpc_tools.protoc -Iproto --python_out=generated --grpc_python_out=generated --pyi_out=generated proto/flight.proto
CMD ["python", "-m", "app.flight"]
