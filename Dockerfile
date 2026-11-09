# Cloud Run Job 이미지 — friday_orchestrator (Weekly Bundle → 격주 Standard)
# 런타임 코드(scripts/, config/)와 의존성만 담는다. secret·.env·sent history 데이터는 넣지 않는다
# (secret 은 Secret Manager, sent history 는 GCS).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-gcp.txt ./
RUN pip install -r requirements-gcp.txt

COPY scripts/ scripts/
COPY config/ config/

# benchmark.py / send_report.py 가 /app 아래 benchmark/, local_output/ 을 만들기 때문에 /app 은 쓰기 가능해야 한다
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# 인자 없음 = --mode auto. Cloud Run 실행별 --args 로 --mode=weekly-bundle / --mode=friday 지정 가능.
ENTRYPOINT ["python", "scripts/friday_orchestrator.py"]
