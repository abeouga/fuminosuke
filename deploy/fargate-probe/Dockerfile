# Phase 7-B only; the parent image is checked against the frozen Phase 7 ID before build.
FROM fuminosuke-backend:phase7 AS trust
FROM fuminosuke-renderer:phase7
USER root
COPY --from=trust /etc/ssl/certs/ca-certificates.crt /opt/probe/ca-certificates.crt
COPY deploy/fargate-probe/native.py deploy/fargate-probe/transport.py /opt/probe/
RUN mkdir -p /scratch /input /output && chown 10001:10001 /scratch /input /output /tmp
VOLUME ["/scratch", "/input", "/output", "/tmp"]
USER 10001:10001
ENTRYPOINT ["/usr/bin/python3", "-I", "-B"]
