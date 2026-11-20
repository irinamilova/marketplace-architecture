FROM python:3.13-slim
WORKDIR /opt/cinema
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY schemas schemas
COPY sql sql
COPY tests tests
USER 65534:65534
