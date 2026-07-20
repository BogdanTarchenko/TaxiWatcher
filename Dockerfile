# Цена вытаскивается обычным HTTP GET (см. app/pricing/maps_scraper.py) - браузер
# больше не нужен, обычный слим-образ вместо тяжёлого Playwright+Chromium.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["python", "-m", "app.main"]
