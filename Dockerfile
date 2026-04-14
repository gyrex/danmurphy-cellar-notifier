FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium --with-deps

# Correct line - use the new filename with underscore
COPY danmurphy_cellar_notifier.py .

RUN mkdir -p /data

CMD ["python", "danmurphy_cellar_notifier.py"]