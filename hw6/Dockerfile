FROM python:3.11-slim
WORKDIR /opt/warehouse
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY schemas schemas
COPY migrations migrations
COPY tests tests
USER 65534:65534
