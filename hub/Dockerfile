# Built from the repo root (see compose.yaml) so the agent source can be served to new rigs.
FROM python:3.12-slim
WORKDIR /app
COPY hub/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hub/fizzhub fizzhub
COPY agent /app/agent
ENV FIZZHUB_AGENT_DIR=/app/agent
EXPOSE 8780
CMD ["uvicorn", "fizzhub.app:app", "--host", "0.0.0.0", "--port", "8780"]
