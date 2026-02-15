# ford-tech-gpt

This repository contains:
- `lambda/` - AWS Lambda backend
- `web/` - static frontend for S3 hosting

## CI/CD auto-deploy overview

Auto deploy is implemented with GitHub Actions in `.github/workflows/deploy.yml`.

On every push to `main` (or manual run), the workflow:
1. Packages `lambda/` into `lambda-package.zip`
2. Deploys that zip to an existing AWS Lambda function
3. Syncs `web/` to an existing S3 bucket
4. Re-uploads `index.html` with no-cache headers

For full, step-by-step setup instructions (AWS + GitHub), see:
- `infra/deploy-setup.md`

## Quick required GitHub configuration

Add one repository secret:
- `AWS_ROLE_TO_ASSUME` - IAM Role ARN to assume via GitHub OIDC

Add three repository variables:
- `AWS_REGION` - example `us-east-1`
- `LAMBDA_FUNCTION_NAME` - existing Lambda function name
- `WEB_BUCKET_NAME` - existing S3 bucket name for `web/`

## Notes

- The workflow deploys code/content only and does not create infrastructure.
- Lambda runtime env vars still must be configured in AWS (`OPENAI_PARAM_NAME`, `OPENAI_MODEL`, `CORS_ORIGIN`).
- Runtime access to AWS SSM Parameter Store must remain granted to the Lambda execution role.
