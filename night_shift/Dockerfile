FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY night_shift ./night_shift
RUN pip install --no-cache-dir .
ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "night_shift.server:api", "--host", "0.0.0.0", "--port", "8080"]
