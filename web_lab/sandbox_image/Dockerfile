ARG BASE_IMAGE
FROM ${BASE_IMAGE}
# No package downloads during build. The base digest is recorded by setup.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* /usr/local/lib/python3.12/ensurepip /usr/local/bin/pip* \
    && mkdir /runner && chmod 755 /runner
COPY supervisor.py invoke.py /runner/
RUN chmod 444 /runner/*.py
USER 65534:65534
WORKDIR /tmp
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-I", "-S", "-u", "/runner/supervisor.py"]
