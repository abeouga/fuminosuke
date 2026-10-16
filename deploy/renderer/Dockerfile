FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc python3 python3-pil python3-seccomp poppler-utils fontconfig \
    fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY backend/app/rendering/worker.py /opt/renderer/worker.py
RUN mkdir /input /output /admission && chmod 755 /opt/renderer/worker.py \
    && dpkg-query -W > /opt/renderer/packages.txt
ENV HOME=/tmp/profile PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8
USER 10001:10001
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/opt/renderer/worker.py"]

