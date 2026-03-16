# MetaForge OCCT Converter
# Converts STEP/IGES CAD files to GLB (glTF binary) for 3D web viewing.
#
# Runs as an HTTP microservice on port 8100 inside docker-compose.
# The gateway POSTs STEP files to http://occt-converter:8100/convert
#
# Can also be used standalone as a CLI:
#   docker run --rm -v /host/input:/data --entrypoint python metaforge-occt \
#       convert.py /data/model.step --quality standard --output-dir /data/out

FROM mambaorg/micromamba:1.5-jammy

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1-mesa-glx libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*
USER $MAMBA_USER

# Install Python + OCCT bindings + mesh export deps
RUN micromamba install -y -n base -c conda-forge \
        python=3.11 \
        pythonocc-core=7.7.2 \
        trimesh \
        numpy \
        scipy \
        networkx \
        pygltflib && \
    micromamba clean -afy

COPY convert.py /app/convert.py
COPY server.py /app/server.py

WORKDIR /app
EXPOSE 8100

# Default: run as HTTP service; override entrypoint for CLI usage
CMD ["python", "server.py"]
