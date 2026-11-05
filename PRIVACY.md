# Privacy policy

multi-account-gmail-mcp is open-source software that each user runs themselves, with their own Google OAuth client.

- **No data is collected by the authors.** The software has no servers, analytics or telemetry.
- **Where your data goes:** the software talks only to Google's APIs (`oauth2.googleapis.com`, `gmail.googleapis.com`), directly from the machine where you run it. Email content is passed only to the MCP client you connect it to (for example Claude Code).
- **Credentials:** OAuth tokens are stored only where you put them (environment variables or a local file on your machine).
- **Revoking access:** at any time at https://myaccount.google.com/permissions.

The use of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.
