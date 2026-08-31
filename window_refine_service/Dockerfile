# Build from the repository root. Mount verified model assets at /models.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    WINDOW_REFINE_PORT=8078 \
    WINDOW_REFINE_MODEL_ROOT=/models/pyannote \
    PYANNOTE_SEGMENTATION_MODEL_DIR=/models/pyannote/segmentation-3.0 \
    PYANNOTE_OSD_MODEL_DIR=/models/pyannote/overlapped-speech-detection \
    VOICEANALYSIS_AUDIO_ROOT=/runtime/audio

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY environments/window-refine/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.lock.txt

COPY window_refine_service /app/window_refine_service

EXPOSE 8078

CMD ["python", "-m", "window_refine_service.app"]
