FROM python:3.11-slim

WORKDIR /app

# Instalar chromium y chromedriver desde apt (misma versión garantizada)
RUN apt-get update && \
    apt-get install -y \
        chromium \
        chromium-driver \
        --no-install-recommends && \
    rm -rf /var/lib/apt/lists/* && \
    chromium --version && \
    chromedriver --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["python", "main.py"]
