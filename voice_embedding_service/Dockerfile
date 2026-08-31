# Build from the repository root. Mount verified model assets at /models.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ECAPA_MODEL_DIR=/models/ecapa/spkrec-ecapa-voxceleb \
    VOICE_EMBEDDING_PORT=8077 \
    VOICEPRINT_TORCH_THREADS=4 \
    VOICEPRINT_TORCH_INTEROP_THREADS=1 \
    OMP_NUM_THREADS=4 \
    MKL_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4 \
    MALLOC_ARENA_MAX=2

WORKDIR /app

COPY environments/voice-embedding/requirements.lock.txt /app/requirements.lock.txt
RUN pip install -r /app/requirements.lock.txt

COPY voice_embedding_service /app/voice_embedding_service

EXPOSE 8077

CMD ["python", "-m", "voice_embedding_service.app"]
