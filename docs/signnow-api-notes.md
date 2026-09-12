# SignNow API — verified notes

Everything `src/signnow.py` sends is recorded here with the source it was verified
against. Nothing in this file was written from memory or from a blog post.

## The docs site is unreachable from this environment

`https://docs.signnow.com/docs/signnow/reference` — the reference you asked me to
read first — could not be opened. Every `signnow.com` host is refused by this
session's egress proxy:

```
docs.signnow.com:443      gateway answered 403 to CONNECT (policy denial)
www.signnow.com:443       gateway answered 403 to CONNECT (policy denial)
blog.signnow.com:443      gateway answered 403 to CONNECT (policy denial)
api.signnow.com:443       gateway answered 403 to CONNECT (policy denial)
api-eval.signnow.com:443  gateway answered 403 to CONNECT (policy denial)
```

`signnow-netsuite.readme.io` is blocked as well. This is an organization egress
policy, not a transient failure, so it cannot be worked around from here.

## What was used instead

Rather than fall back to blog posts, every shape below was read out of airSlate's
own published source code, which is generated and maintained against the live API:

| Source | What it settles |
|---|---|
| `github.com/signnow/SignNowNodeSDK` | Endpoint paths, HTTP methods, auth type, content type, request and response field names |
| `github.com/signnow/SignNow.NET` | Multipart wire format, complex-tag JSON, validator IDs |
| `SignNow.NET/SignNow.Net.Examples/TestExamples/DocumentWithSignatureFieldTag.pdf` | A real simple text tag, extracted from the fixture PDF |
| `SignNow.NET/SignNow.Net.Test/UnitTests/Models/ComplexTags/ComplexTagsTest.cs` | Exact serialized complex-tag JSON, asserted by their own tests |
| The SignNow MCP server's tool schemas in this session | Independent corroboration of the invite recipient shape |

Both SDK repositories were cloned at their default branch on 2026-09-12.

## Endpoints

### Access token

`POST /oauth2/token` — HTTP Basic (`client_id:client_secret`), body
`application/x-www-form-urlencoded`.

| Key | Notes |
|---|---|
| `username` | Account login email |
| `password` | Account password |
| `grant_type` | `password` |
| `scope` | `*` |

Refresh uses the same URL with `grant_type=refresh_token` and `refresh_token`.

Verified: `SignNowNodeSDK/src/api/auth/request/tokenPost.ts`,
`refreshTokenPost.ts`.

**The password grant needs a SignNow-native password**, which a Google sign-in
account does not have. That is the account in use here, so the client's primary
mode is an API key instead.

### API key (the mode this project uses)

The SignNow developer dashboard issues an API key. It is used directly as the
bearer token on every request; no token exchange happens and no client id or
secret is needed for ordinary calls.

Verified: `SignNowNodeSDK/README.md` ("You can authenticate using either an API
key: `new Sdk({ apiKey })`"), `src/core/sdk.ts` (the key is handed to the
client as its bearer token) and `src/core/apiClient.ts` (`Bearer ${bearerToken}`).
The README also notes the Basic token is only required for *generating* OAuth
tokens and for a few specific endpoints, none of which this project calls.

An API key cannot be refreshed. On a 401 the client raises immediately rather
than retrying, and the message says to reissue the key in the dashboard.

### Verify credentials

`GET /oauth2/token` — Bearer. Returns the token's scope and expiry. Called once
at the start of `prepare` so a bad key fails before anything is rendered or
uploaded.

Verified: `SignNowNodeSDK/src/api/auth/request/tokenGet.ts`; the .NET README
lists it as "Verify Access Token".

### Upload with field extraction

`POST /document/fieldextract` — Bearer, `multipart/form-data`. Returns `{"id": "..."}`.

| Part | Notes |
|---|---|
| `file` | The PDF |
| `Tags` | JSON array of complex tag objects |
| `parse_type` | `default` |
| `client_timestamp` | Unix seconds |

Verified: `SignNowNodeSDK/src/api/document/request/fieldExtractPost.ts` for the
path, method, auth and content type; `SignNow.NET/SignNow.Net/Model/Requests/MultipartHttpContent.cs`
for how the parts are actually assembled on the wire.

**One unresolved discrepancy.** The .NET SDK names the tags part `Tags` with a
capital T; the Node SDK's payload dictionary keys it `tags` lowercase. The .NET
code is the one that shows the literal multipart assembly, so `Tags` is what this
client sends. It is a single constant, `TAGS_PART_NAME` in `src/signnow.py`, so
flipping it is a one-line change if the sandbox rejects it. This is exactly the
kind of detail the reference would have settled.

### Read a document

`GET /document/{document_id}` — Bearer. Returns `roles[]`
(`unique_id`, `name`, `signing_order`), `fields[]` (`id`, `type`, `role`,
`role_id`, `json_attributes`), `field_invites[]`, and `page_count`.

`roles[].unique_id` is the `role_id` the invite needs. Verified:
`SignNowNodeSDK/src/api/document/response/documentGet.ts` and
`.../response/data/role.ts`, `field.ts`, `fieldInvite/fieldInvite.ts`.

### Send a role-based invite

`POST /document/{document_id}/invite` — Bearer, `application/json`.

| Key | Notes |
|---|---|
| `document_id` | |
| `to` | Array of recipients |
| `from` | Sender email |
| `subject`, `message` | Email copy |
| `cc`, `cc_step`, `viewers`, `email_groups` | Optional |

Each `to` entry: `email`, `role_id`, `role`, `order`, `subject`, `message`, plus
optional `expiration_days`, `reminder`, `redirect_uri` and authentication keys.

Verified: `SignNowNodeSDK/src/api/documentInvite/request/sendInvitePost.ts` and
`.../data/to/to.ts`. Corroborated by the SignNow MCP `send_invite` schema in this
session, which exposes the same `order` / `recipients` / `role` / `action` model.

**There is no "prepare but do not send" endpoint.** Creating the invite *is*
sending it. So the approval gate cannot live on SignNow's side — `prepare` builds
the invite payload, writes it to disk and stops; `send` is the only code path that
ever POSTs to this endpoint.

### Invite status

Read `field_invites[]` from `GET /document/{document_id}`. Each carries `status`,
`email`, `role`, `created`, `updated`. No separate status endpoint is needed.

## Text tags

### Simple tags

Placed in the document body; SignNow converts them to fields on upload. Extracted
verbatim from airSlate's own fixture PDF:

```
{{t:t;r:n;o:"Signer 1";}}
```

`t` is type, `r` is required (`y`/`n`), `o` is the role name in double quotes,
semicolon-separated with a trailing semicolon.

Search results claiming a fuller parameter list contradicted each other on what
`r` and `l` mean, so only the four keys proven by the fixture are treated as known.

### Complex tags — what this project uses

Put `{{TagName}}` in the document, then describe each tag in the `Tags` part of the
upload. **Position comes from where the tag sits in the document, not from
coordinates** — no `x`/`y` is sent for any field type this project uses. That is
the reflow immunity you asked for.

Exact JSON, from assertions in airSlate's own unit tests:

```json
{ "type": "signature", "tag_name": "SignatureTagExample", "role": "CLIENT",
  "required": true, "width": 400, "height": 15 }

{ "type": "text", "label": "Label1", "tag_name": "TextTagExample", "role": "CLIENT",
  "required": true, "width": 400, "height": 150 }

{ "type": "text", "label": "Date of Birth", "tag_name": "DateValidatorTagExample",
  "role": "Role1", "required": true, "width": 100, "height": 15,
  "lock_to_sign_date": true, "validator_id": "13435fa6c2a17f83177fcbb5c4a9376ce85befeb" }
```

A date field is a `text` field carrying a validator, not its own type.
`13435fa6c2a17f83177fcbb5c4a9376ce85befeb` is the US date validator, and
`lock_to_sign_date` fills it with the signing date automatically.

Verified: `ComplexTagsTest.cs` for the JSON, `SignNow.Net/Model/ComplexTags/*.cs`
for the property names, `SignNow.Net.Examples/Documents/UploadDocumentWithComplexTags.cs`
for how a real collection is assembled, `SignNow.Net/Model/DataValidator.cs` for
validator IDs.

Only `RadioButtonTag` sets coordinates, because its options need offsets relative
to the anchor. This project uses none.

## Still unverified

These need one sandbox run to confirm, and are called out because they are the
places where being wrong is most likely:

1. `Tags` vs `tags` as the multipart part name.
2. Whether `page_number` is required on complex tags. Their example omits it on
   most tag types but sets it on two, so this client omits it.
3. The exact `status` strings on `field_invites[]`.
