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

# Deployment units and the CA probe ride along in the image on purpose: the
# target hosts can reach GHCR but NOT github.com, so `curl`-ing a unit file from
# the repo is not an option there. Shipping them here makes the image the single
# thing a deployer needs to fetch. See "Install" in README.md.
COPY deploy /app/deploy
COPY tools/ca_probe.py /app/tools/ca_probe.py

# tsrs-web.service is generated from the .in template, exactly as the RPM does,
# so there is one source for the unit and no chance of the two drifting. The
# image pins :latest; the RPM pins its own version.
RUN sed -e 's|@IMAGE@|ghcr.io/gemini-rtsw/tsrs_screen:latest|' \
        /app/deploy/tsrs-web.service.in > /app/deploy/tsrs-web.service \
    && rm /app/deploy/tsrs-web.service.in \
    && ! grep -q '@IMAGE@' /app/deploy/tsrs-web.service

ENV TSRS_CHANNELS=/app/channels.json \
    TSRS_CONFIG=/app/tsrs.config.json \
    TSRS_STATIC=/app/static \
    TSRS_BIND=0.0.0.0 \
    TSRS_PORT=8080

# Read-only annunciator: no writes, no privileged ports.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin tsrs
USER 10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/healthz' \
    % os.environ.get('TSRS_PORT','8080'), timeout=4).status==200 else 1)"

# Shell form so TSRS_PORT/TSRS_BIND are expanded at runtime: with --network
# host there is no port isolation, so a shared docker host will often already
# have 8080 taken and the port must be overridable without a command override.
#
# --workers 1 is mandatory: the CA monitor cache is per-process state.
CMD uvicorn tsrs_web.app:app --host "$TSRS_BIND" --port "$TSRS_PORT" \
    --workers 1 --no-access-log
