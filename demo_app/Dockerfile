# Demo app container: the FastAPI service with the planted bugs.
#
# Built by CDK (infra/stacks/demo_app_stack.py) with ContainerImage.from_asset,
# using this directory (demo_app/) as the build context. `cdk synth` only stages
# these files into cdk.out; Docker runs at `cdk deploy` time.
#
# Local test (from the repo root):
#   docker build -t mini-debug-assist-demo demo_app/
#   docker run --rm -p 8000:8000 mini-debug-assist-demo
#   curl localhost:8000/health   # 200
#   curl localhost:8000/user/3   # 500 (planted KeyError)

FROM public.ecr.aws/docker/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/demo_app/requirements.txt
RUN pip install -r /app/demo_app/requirements.txt

# The app is imported as the `demo_app` package (demo_app.main:app)
COPY . /app/demo_app

EXPOSE 8000

# --no-access-log: the app already writes one JSON log line per request
CMD ["uvicorn", "demo_app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
