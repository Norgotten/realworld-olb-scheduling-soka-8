FROM python:3.12-slim
WORKDIR /app
COPY worker.py .
EXPOSE 5000
CMD ["python", "-u", "worker.py"]
