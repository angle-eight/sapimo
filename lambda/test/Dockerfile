# Lambda Function Container
FROM public.ecr.aws/lambda/python:3.12

# Lambda function code
COPY app.py ${LAMBDA_TASK_ROOT}

# Default handler
CMD ["app.lambda_handler"]