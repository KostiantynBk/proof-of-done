FROM python:3.12-slim
ARG RUNNER_UID=1000
ARG RUNNER_GID=1000
RUN test "$RUNNER_UID" -gt 0 && test "$RUNNER_GID" -gt 0 \
    && pip install --no-cache-dir pytest pytest-timeout pypdf \
    && groupadd -g "$RUNNER_GID" runner && useradd -m -u "$RUNNER_UID" -g "$RUNNER_GID" runner \
    && mkdir -p /home/runner/.aws /home/runner/.ssh \
    && printf '# CANARY — FAKE\n[default]\naws_access_key_id = AKIAPODCANARY0000000\naws_secret_access_key = POD-CANARY-FAKE-SECRET\n' > /home/runner/.aws/credentials \
    && printf '# CANARY — FAKE\nPOD-CANARY-FAKE-PRIVATE-KEY\n' > /home/runner/.ssh/id_rsa \
    && printf '# CANARY — FAKE\nmachine example.invalid login canary password POD-CANARY-FAKE\n' > /home/runner/.netrc \
    && chown -R runner:runner /home/runner
COPY sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
COPY run_pytest.sh extract_pdf.py /opt/pod/
RUN chmod 755 /opt/pod/run_pytest.sh
USER ${RUNNER_UID}:${RUNNER_GID}
ENV HOME=/home/runner
WORKDIR /work
