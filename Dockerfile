# Версия тега образа должна совпадать с версией пакета playwright в requirements.txt -
# в образе уже стоит Chromium под конкретную версию Playwright, рассинхрон роняет запуск браузера.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["python", "-m", "app.main"]
