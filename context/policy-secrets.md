---
id: policy-secrets
title: Credentials in generated code
subject: policy
subcategory: security
tags: [secrets, credentials, keys, aws, s3, bucket, boto3, api, token, password, auth, login, oauth, config, environment]
priority: 9
always: true
summary: Never write a credential literal. Read secrets from os.environ or a named AWS profile, fail loudly when one is missing, and never print or log their values.
updated: 2026-09-13
---

Credentials are never in the code you write, and never in a file you create.
They are supplied by the environment the code runs in.

- Read them with `os.environ["NAME"]`, or `os.getenv("NAME")` when there is a
  genuine default that is not itself a secret.
- For AWS, do not read keys at all. Construct
  `boto3.Session(profile_name=os.environ.get("AWS_PROFILE"))`, or just
  `boto3.client("s3")` and let the default credential chain find them. Never
  pass `aws_access_key_id` or `aws_secret_access_key` as literals.
- A missing variable is a fatal error, not something to work around. Raise
  with the variable's name in the message. Do not substitute a placeholder,
  an empty string, or a plausible-looking fake key — code that runs with a
  wrong credential fails somewhere far away from the cause.

Do not invent a config file, `.env`, `settings.py`, or constants module that
holds key material, even as an example or a placeholder. If a file needs to
document that a variable is required, name the variable and leave the value
out entirely.

Never write a secret to stdout, a log line, an error message, or a comment.
When an error needs to refer to one, refer to the variable's name and never
its value. The same applies to anything derived from one: signed URLs,
session tokens, and connection strings with a password in them are all
secrets.

Strings that look like credentials are credentials. Treat anything matching
`AKIA...`, a long base64 or hex blob assigned to a name containing `key`,
`token`, `secret`, or `password`, or a URL with `user:pass@` in it, as
something that must come from the environment.

If a request seems to require a specific key value in order to be written at
all, that request is under-specified. Write the code to read the value by
name and say which variable you expect, rather than guessing at a value.
