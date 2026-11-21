# Python 3.14 test image for unit and simulator suites.
FROM python:3.14-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY custom_components/bosch/manifest.json /tmp/manifest.json
COPY unittests/requirements.txt /tmp/unittests-requirements.txt
COPY tests/requirements.txt /tmp/tests-requirements.txt

RUN python -m pip install --upgrade pip \
    && pip install pytest pytest-asyncio \
    && pip install -r /tmp/unittests-requirements.txt \
    && pip install -r /tmp/tests-requirements.txt \
    && pip install "$(python -c "import json; print(json.load(open('/tmp/manifest.json'))['requirements'][0])")"

COPY . /app

CMD ["python", "-m", "pytest", "--tb=short", "-q", "unittests"]
