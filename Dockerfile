FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Instalar chromium Y chromedriver del mismo paquete apt (versiones compatibles)
RUN apt-get update && \
    apt-get install -y chromium chromium-driver --no-install-recommends && \
    echo "chromium: $(chromium --version)" && \
    echo "chromedriver: $(chromedriver --version)"

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["python", "main.py"]
