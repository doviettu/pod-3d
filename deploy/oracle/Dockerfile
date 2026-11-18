# POD Studio cho máy Linux ARM (Oracle Ampere A1) hoặc x86. Image chỉ chứa thư viện;
# code là bản git clone trên máy chủ, gắn vào /app, nên cập nhật chỉ cần `git pull` + restart.
FROM python:3.12-slim-bookworm

ARG UID=1000
ARG GID=1000
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates tini \
        nodejs npm \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie \
        libgl1 libglib2.0-0 \
        fonts-dejavu fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Python: engine + Studio + SAM 3 (transformers) + SAM 2/VTracer phụ thuộc.
COPY requirements.txt studio/review-requirements.txt /tmp/req/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r /tmp/req/requirements.txt \
    && /opt/venv/bin/pip install "transformers>=5" torchvision huggingface_hub accelerate \
    && /opt/venv/bin/pip install --target /opt/vendor -r /tmp/req/review-requirements.txt

# SAM 2 chính thức (commit giống trên Mac), chỉ dùng mã nguồn; model tải vào runtime/models.
RUN git clone https://github.com/facebookresearch/sam2 /opt/repos/sam2 \
    && git -C /opt/repos/sam2 checkout 2b90b9f5ceec907a1c18123530e92e794ad901a4

# Xuất PSD.
COPY studio/package.json studio/package-lock.json /opt/node/
RUN cd /opt/node && npm ci --omit=dev

RUN groupadd -g ${GID} pod && useradd -m -u ${UID} -g ${GID} -s /bin/bash pod
USER pod
# Claude Code (Hỏi Claude, Phân tích Redesign). Đăng nhập bằng CLAUDE_CODE_OAUTH_TOKEN trong secrets/claude.env.
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH=/home/pod/.local/bin:/opt/venv/bin:$PATH \
    POD_HOST=0.0.0.0 \
    POD_ONLINE_ONLY=1

COPY --chown=pod:pod deploy/oracle/entrypoint.sh /usr/local/bin/pod-entrypoint
WORKDIR /app
EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/pod-entrypoint"]
CMD ["python", "studio/studio.py", "--port", "8765"]
