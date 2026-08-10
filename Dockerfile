# TSRS panel gateway.
#
# pyepics wheels bundle their own libca, so no epics-base is needed in the image.
# If site policy requires the Gemini epics-base build instead, install it and set
# PYEPICS_LIBCA=/path/to/libca.so -- pyepics will not find it on its own.
#
# PLATFORM IS PINNED ON PURPOSE.
# pyepics ships only an x86-64 libca (epics/clibs/linux64/libca.so). On an arm64
# builder -- e.g. an Apple Silicon laptop -- an unpinned build produces an arm64
# image whose libca cannot be dlopen'd, and the gateway dies at startup with
# "loading Epics CA DLL failed". The deployment target (hbfbfotsrs-ld1) is
# x86_64, so amd64 is the correct architecture; it runs emulated on arm Macs,
# which is ample for 76 channels. An arm64 image would need epics-base built
# for aarch64 and PYEPICS_LIBCA pointed at it.
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY gateway/tsrs_web /app/tsrs_web
COPY gateway/channels.json /app/channels.json
COPY tsrs.config.json /app/tsrs.config.json
COPY static /app/static

ENV TSRS_CHANNELS=/app/channels.json \
    TSRS_CONFIG=/app/tsrs.config.json \
    TSRS_STATIC=/app/static

# Read-only annunciator: no writes, no privileged ports.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin tsrs
USER 10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/healthz',timeout=4).status==200 else 1)"

# --workers 1 is mandatory: the CA monitor cache is per-process state.
CMD ["uvicorn", "tsrs_web.app:app", \
     "--host", "0.0.0.0", "--port", "8080", "--workers", "1", \
     "--no-access-log"]
