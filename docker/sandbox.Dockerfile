# Lightweight Python sandbox for executing LLM-generated pandas code.
# No network access, read-only data mount, memory-limited at runtime.
FROM python:3.10-slim

# Install only the data-science packages the agent is allowed to use
RUN pip install --no-cache-dir \
    pandas==2.2.3 \
    numpy==2.2.0 \
    scipy==1.14.1 \
    scikit-learn==1.6.0 \
    pyarrow==18.1.0 \
    && rm -rf /root/.cache

# Non-root user for minimal privilege
RUN useradd -m sandbox
USER sandbox
WORKDIR /home/sandbox

# Entry point: execute a Python script passed as an argument
ENTRYPOINT ["python"]
