# Sử dụng image Python slim để tối ưu kích thước
FROM python:3.11-slim

# Thiết lập biến môi trường
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Tạo thư mục làm việc
WORKDIR /app

# Cài đặt các system dependencies cần thiết (nếu có)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy và cài đặt thư viện Python
COPY requirements.txt /app/
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy source code backend
COPY . /app/

# Expose port
EXPOSE 8000

# Chạy lệnh khởi động server (development by default)
# Nếu muốn chạy production, có thể dùng: CMD ["gunicorn", "shift_manager.wsgi:application", "--bind", "0.0.0.0:8000"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
