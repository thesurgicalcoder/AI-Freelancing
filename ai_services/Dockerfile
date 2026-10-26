FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY flask_app/ .
RUN useradd -m app && chown -R app /app
USER app
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:5001"]
