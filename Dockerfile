FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Instalar selenium y dependencias
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copiar el chromedriver de playwright a una ubicación accesible
RUN CHROME_DIR=$(find /ms-playwright -name "chrome" -type f 2>/dev/null | head -1 | xargs dirname) && \
    if [ -d "$CHROME_DIR" ]; then \
        ls -la "$CHROME_DIR" && \
        echo "Chrome dir: $CHROME_DIR"; \
    fi

COPY . .

EXPOSE 8080
CMD ["python", "main.py"]
