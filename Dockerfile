FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "uvicorn[standard]"
COPY . .
ENV RUN_LOCAL_SCHEDULER=1
EXPOSE 8000
CMD ["uvicorn", "index:app", "--host", "0.0.0.0", "--port", "8000"]
