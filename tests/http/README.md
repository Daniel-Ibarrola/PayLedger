# HTTP client requests

`items_service.http` runs against the deployed API from PyCharm's HTTP Client
(gutter ▶ per request, or "Run all requests in file").

Pick the `dev` environment from the dropdown before running. Its `baseUrl` is
empty in the committed `http-client.env.json` and is filled in by
`http-client.private.env.json`, which is **not** in git:

```
make http-env
```

reads the API URL from Terraform outputs and writes that file.

## Why the URL isn't committed

The API is unauthenticated until Cognito lands. The URL isn't a credential, but
it is currently the only thing between a public repo and someone writing to the
table, so it stays out of git. Once there's an authorizer in front, the public
env file can hold it directly.
