# Shared Lambda runtime container
# Function code is mounted to /var/task via docker-compose volume.
ARG PYTHON_VERSION=3.12
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

CMD ["app.lambda_handler"]
