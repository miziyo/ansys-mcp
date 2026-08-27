# Security policy

## Supported version

Only the latest GitHub release is supported with security fixes.

## Reporting a vulnerability

Do not open a public issue containing credentials, private models, license-server details, or a
working exploit. Use GitHub's private vulnerability reporting page instead:

https://github.com/miziyo/ansys-mcp/security/advisories/new

Include the affected release, MCP tool or worker route, a minimal reproduction that contains no
licensed Ansys files, and the expected security boundary. Reports concerning arbitrary command,
script, executable, path, endpoint, or solver-option injection are treated as high priority.

## Repository data boundary

Generated runtime databases, solver outputs, product logs, local paths, environment snapshots,
licenses, credentials, user models, third-party examples, and product documentation must not be
committed. The public repository keeps only project-owned source, schemas, generated test geometry,
and tests required for the core runner.
