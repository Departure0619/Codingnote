ARG PYTHON_IMAGE
ARG GCC_IMAGE
ARG JAVA_IMAGE
ARG NODE_IMAGE
FROM ${PYTHON_IMAGE} AS python_runtime
FROM ${JAVA_IMAGE} AS java_runtime
FROM ${NODE_IMAGE} AS node_runtime
FROM ${GCC_IMAGE}
# Both bases must be resolved to immutable repository digests by runtime_setup.
# No package/network downloads: the GCC image already contains GCC/G++ 14.2.0.
COPY --from=python_runtime /usr/local/ /usr/local/
COPY --from=java_runtime /opt/java/openjdk /opt/java/openjdk
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY NODE_LICENSE /usr/share/licenses/node/LICENSE
RUN /sbin/ldconfig \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip* /usr/local/lib/python3.12/ensurepip /usr/local/bin/pip* \
    && mkdir /runner && chmod 755 /runner \
    && python -I -S -c "import sys, fcntl, os; assert sys.version_info[:2] == (3, 12); assert hasattr(os, 'memfd_create')" \
    && test "$(gcc -dumpfullversion)" = "14.2.0" \
    && test "$(g++ -dumpfullversion)" = "14.2.0"
RUN /opt/java/openjdk/bin/java -version 2>&1 | grep '17.0.18' \
    && test "$(node --version)" = "v22.22.0"
COPY supervisor.py managed.py languages.json /runner/
RUN chmod 444 /runner/*
USER 65534:65534
WORKDIR /tmp
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-I", "-S", "-u", "/runner/supervisor.py"]
