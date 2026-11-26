# Rewind on Hugging Face Spaces (Docker SDK). Built by Hugging Face from this folder.
FROM python:3.12-slim

# ffmpeg: video previews and look-alike matching. rclone (official current build): cloud account sign-in.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
 && curl -fsSL -o /tmp/rclone.zip https://downloads.rclone.org/rclone-current-linux-amd64.zip \
 && unzip -q /tmp/rclone.zip -d /tmp \
 && install -m 755 /tmp/rclone-*-linux-amd64/rclone /usr/local/bin/rclone \
 && rm -rf /tmp/rclone* /var/lib/apt/lists/*

# Spaces run the app as user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 REWIND_CLOUD=1 PORT=7860
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user rewind ./rewind
EXPOSE 7860
CMD ["python", "-m", "rewind", "space"]
