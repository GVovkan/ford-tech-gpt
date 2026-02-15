# ford-tech-gpt

## Auto deploy to AWS Lambda and S3

This repo now includes a GitHub Actions workflow at `.github/workflows/deploy.yml` that deploys:
- `lambda/` to AWS Lambda
- `web/` to an S3 bucket

The workflow runs on push to `main` and can also be started manually from the Actions tab.

### 1) Configure GitHub OIDC role in AWS

Create an IAM role trusted by GitHub OIDC and allow it to:
- `lambda:UpdateFunctionCode` on your Lambda function
- `s3:ListBucket`, `s3:PutObject`, `s3:DeleteObject` on your web bucket

Then store the role ARN as a GitHub repository secret:
- `AWS_ROLE_TO_ASSUME`

### 2) Add repository variables

Set these in GitHub repo **Settings -> Secrets and variables -> Actions -> Variables**:
- `AWS_REGION` (example: `us-east-1`)
- `LAMBDA_FUNCTION_NAME` (your existing Lambda name)
- `WEB_BUCKET_NAME` (target S3 bucket name)

### 3) Deploy flow

1. Push to `main`.
2. Workflow packages Lambda code into `lambda-package.zip`.
3. Workflow updates Lambda function code.
4. Workflow syncs `web/` to S3 with `--delete`.
5. Workflow re-uploads `index.html` with no-cache headers.

### Notes

- This workflow deploys code/content only. It does not create AWS resources.
- Keep Lambda environment variables (`OPENAI_PARAM_NAME`, `OPENAI_MODEL`, `CORS_ORIGIN`) configured in Lambda.
- Keep SSM parameter and IAM permissions in place for runtime access.
