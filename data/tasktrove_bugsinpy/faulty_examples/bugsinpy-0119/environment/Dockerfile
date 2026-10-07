FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python3 -m pip install --no-cache-dir pytest==8.3.5 numpy==2.1.3 pandas==2.2.3 pillow==11.1.0 six==1.17.0 click==8.1.8 jinja2==3.1.6 mock==5.2.0 s3fs==2025.3.0 lxml==5.3.1 parsel==1.10.0 twisted==24.11.0
WORKDIR /app
RUN ln -s /setup_files/solution.py /app/solution.py
