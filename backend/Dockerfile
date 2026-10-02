FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/src ./backend/src
COPY config ./config
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir ./backend
ENV PYTHONPATH=/app/backend/src
EXPOSE 8001
CMD ["uvicorn", "lead_agent.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
