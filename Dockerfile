# SilverFile: everything in one image. Runs offline (no API key) by default.
#
#   docker compose up                    # staff dashboard on :8000, taxpayer page and grader's dashboard on :8001
#   docker compose run --rm app python run.py --case demo/eval_case_1/case.json
#
# See docs/notes/mlops_devops.md for the design.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SILVERFILE_OFFLINE=1 \
    DASHBOARD_HOST=0.0.0.0 \
    CHECKPOINT_DB=/app/state/silverfile_checkpoints.sqlite \
    SILVERFILE_TRACE_DIR=/app/traces

# tesseract-ocr reads the W-2 image offline; libgl1/libglib2.0-0 are what rapidocr's OpenCV needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code changes don't reinstall them. Prefer the exact lock file.
COPY requirements.txt requirements.lock* ./
RUN if [ -f requirements.lock ]; then pip install -r requirements.lock; else pip install -r requirements.txt; fi

COPY . .
RUN mkdir -p /app/outputs /app/state /app/traces

EXPOSE 8000 8001
CMD ["python", "run.py", "--dashboard"]
