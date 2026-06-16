FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Instalar chromedriver compatible con el chromium de playwright
RUN apt-get update && apt-get install -y chromium-driver --no-install-recommends && \
    chromedriver --version && \
    chromium --version || true

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["python", "main.py"]
