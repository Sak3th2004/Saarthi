# /infra — AWS deploy (IaC)

Owner: the `infra` subagent. Built in **Week 6** (see `SPRINT_PLAN.md`).

IaC (CDK or Terraform): agents + MCP server on Bedrock AgentCore Runtime, AgentCore Memory,
Cognito user pool, SNS (SMS) + SES (email), frontend hosting (Amplify/Vercel). One region where
Bedrock (Claude) AND AgentCore both exist. Every AWS service used is documented here for the AWS
Builder mini submission. Secrets in a manager, never git.
