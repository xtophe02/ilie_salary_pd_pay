FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure data directories exist
RUN mkdir -p data/uploads data/generated

EXPOSE 5001

ENV FLASK_ENV=production

CMD ["python", "app.py"]
