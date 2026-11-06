# Security policy

## Report a vulnerability

Do not open a public issue for a security problem.
Use the private reporting form of GitHub: open the **Security** tab of this repository and choose **Report a vulnerability**.
Describe the problem, the steps to repeat it, and the effect that it has. Include the version or the commit.

We try to answer within 5 working days. We tell you when we fix the problem. We credit you if you want us to.

## Supported versions

| Version | Supported |
|---|---|
| The latest commit on `main` | Yes |
| Older commits | No |

## What is in scope

- The API server, its keys, roles and workspaces.
- The handling of production traces: redaction, encryption and retention.
- The `python:` and `http:` app targets. They run your code or call your URL, so only the command line and pytest can start them.
- The GitHub Action and the CI workflow.
- The pull request comment, which shows text from a model.
- The dashboard.

## What we already do

- API keys have roles and can expire. Workspaces keep their data apart.
- TrustBench redacts personal data and secrets in production traces. It can encrypt the stored text.
- The server never runs app targets. A test checks this.
- The Action gives the token only to the step that posts the comment. It pins every third-party action to a commit SHA.
- The pull request comment escapes everything that comes from a model or from test data.
- A check of the dependencies runs before each release (`pip-audit` and `pnpm audit`).

## Known limits

`docs/ISSUES.md` lists the limits that we know, with their severity. The most important ones:

- The server has no TLS. Put it behind a proxy.
- Evaluation runs store tool-call data and outputs as they are. Do not put real customer data or secrets in test data.
- The code does not check the content of a Docker image.

The security scan of this project is a first pass. It is not a professional audit.
