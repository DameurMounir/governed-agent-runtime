# Security Policy

## Supported version

The current `0.1.x` line receives security fixes while it remains the latest release line.

## Reporting

Report suspected vulnerabilities through GitHub's private vulnerability reporting feature when available. Do not open a public issue containing exploit details, credentials, personal data, or unpublished evidence.

## Security model

The runtime is fail-closed for missing authority, capability, effect permission, evidence, and registry entries. In-memory authority replay protection and evidence storage are reference implementations, not durable distributed controls. Production integrations must replace them with appropriately protected persistent services.
