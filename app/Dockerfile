FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Optional: install the browser Craigslist scraping needs. Adds ~450 MB to the
# image; skip it if you only run the Kijiji source (which needs no browser) or
# mount a Chrome/Chromium binary into the container instead.
# RUN playwright install --with-deps chromium
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]