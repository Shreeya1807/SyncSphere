FROM python:3.12-slim
WORKDIR /app
COPY requirements-modern.txt .
RUN pip install --no-cache-dir -r requirements-modern.txt && useradd --create-home app
COPY syncsphere ./syncsphere
COPY run.py .
RUN mkdir data && chown -R app:app /app
USER app
EXPOSE 5000
HEALTHCHECK --interval=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health/ready')"
CMD ["gunicorn", "--workers", "1", "--threads", "50", "--bind", "0.0.0.0:5000", "run:app"]
