FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fizzhub fizzhub
EXPOSE 8780
CMD ["uvicorn", "fizzhub.app:app", "--host", "0.0.0.0", "--port", "8780"]
