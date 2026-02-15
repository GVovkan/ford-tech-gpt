# Detailed setup: AWS + GitHub auto deploy

This guide configures secure deployment from GitHub Actions to:
- AWS Lambda (`lambda/`)
- S3 static site bucket (`web/`)

It uses GitHub OIDC, so you do not store long-term AWS keys in GitHub.

## 0) Prerequisites

- Existing AWS account access
- Existing Lambda function (Python runtime)
- Existing S3 bucket for website files
- GitHub repository admin access
- `main` branch as deployment branch

## 1) Create GitHub OIDC provider in AWS (one-time per account)

Check if provider already exists:

```bash
aws iam list-open-id-connect-providers
```

If you do not see `token.actions.githubusercontent.com`, create it:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

## 2) Create IAM policy for deployment role

Create file `deploy-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "LambdaUpdate",
      "Effect": "Allow",
      "Action": [
        "lambda:UpdateFunctionCode",
        "lambda:GetFunction"
      ],
      "Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:LAMBDA_FUNCTION_NAME"
    },
    {
      "Sid": "S3SyncBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::WEB_BUCKET_NAME"
    },
    {
      "Sid": "S3SyncObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::WEB_BUCKET_NAME/*"
    }
  ]
}
```

Replace:
- `REGION`
- `ACCOUNT_ID`
- `LAMBDA_FUNCTION_NAME`
- `WEB_BUCKET_NAME`

Create policy:

```bash
aws iam create-policy \
  --policy-name FordTechGitHubDeployPolicy \
  --policy-document file://deploy-policy.json
```

Save the returned policy ARN.

## 3) Create IAM role trusted by GitHub OIDC

Create file `deploy-trust-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:GITHUB_OWNER/REPO_NAME:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

Replace:
- `ACCOUNT_ID`
- `GITHUB_OWNER`
- `REPO_NAME`

Create role:

```bash
aws iam create-role \
  --role-name FordTechGitHubDeployRole \
  --assume-role-policy-document file://deploy-trust-policy.json
```

Attach policy created in step 2:

```bash
aws iam attach-role-policy \
  --role-name FordTechGitHubDeployRole \
  --policy-arn arn:aws:iam::ACCOUNT_ID:policy/FordTechGitHubDeployPolicy
```

Get role ARN (you need this for GitHub secret):

```bash
aws iam get-role --role-name FordTechGitHubDeployRole --query 'Role.Arn' --output text
```

## 4) Confirm S3 bucket is ready for static website

Your workflow uploads files only. Ensure bucket access/serving is already configured for your architecture:
- S3 static website hosting directly, or
- S3 behind CloudFront

Minimum deploy permissions are already covered by the deploy policy above.

## 5) Confirm Lambda function is ready

Your Lambda must already exist and keep runtime config in AWS, including:
- `OPENAI_PARAM_NAME`
- `OPENAI_MODEL` (optional, defaults in code)
- `CORS_ORIGIN`

If needed, set env vars:

```bash
aws lambda update-function-configuration \
  --function-name LAMBDA_FUNCTION_NAME \
  --environment "Variables={OPENAI_PARAM_NAME=/TechStories/OPENAI_API_KEY,OPENAI_MODEL=gpt-4.1,CORS_ORIGIN=*}"
```

## 6) Configure GitHub repository settings

In GitHub repository:

Settings -> Secrets and variables -> Actions

Add secret:
- `AWS_ROLE_TO_ASSUME` = role ARN from step 3

Add variables:
- `AWS_REGION` = AWS region (example `us-east-1`)
- `LAMBDA_FUNCTION_NAME` = target Lambda function name
- `WEB_BUCKET_NAME` = target S3 bucket name

## 7) Verify the workflow file in repository

Workflow path must exist:
- `.github/workflows/deploy.yml`

Current triggers:
- push to `main`
- manual run (`workflow_dispatch`)

## 8) Deploy

Option A: push to `main`

```bash
git checkout main
git pull
git merge YOUR_WORK_BRANCH
git push origin main
```

Option B: manual run
1. Open GitHub -> Actions
2. Select workflow `Deploy AWS Lambda + S3`
3. Click `Run workflow`

## 9) Validate deployment

In GitHub Actions logs confirm:
- Lambda step `Update Lambda function code` succeeded
- S3 step `Sync static site` succeeded

Then validate in AWS:

```bash
aws lambda get-function --function-name LAMBDA_FUNCTION_NAME --query 'Configuration.LastModified' --output text
aws s3 ls s3://WEB_BUCKET_NAME/
```

## 10) Troubleshooting

### Error: `Not authorized to perform sts:AssumeRoleWithWebIdentity`
- Trust policy `sub` does not match repo/branch
- OIDC provider missing or wrong
- GitHub secret `AWS_ROLE_TO_ASSUME` points to wrong role

### Error: Lambda update access denied
- Missing `lambda:UpdateFunctionCode` on exact Lambda ARN

### Error: S3 sync access denied
- Missing `s3:ListBucket` on bucket ARN
- Missing `s3:PutObject`/`s3:DeleteObject` on `bucket/*`

### Website still shows old page
- CDN/browser cache; workflow already sets no-cache for `index.html`
- If using CloudFront, create invalidation after deploy (optional enhancement)

## Optional hardening improvements

- Restrict trust policy to GitHub environment instead of only branch
- Add separate roles for staging and production
- Add CloudFront invalidation step if serving through CloudFront
- Add protected environments and required approvals in GitHub Actions
