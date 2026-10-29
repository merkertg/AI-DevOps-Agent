# ADR 0002: Docker access for the Stage 04 lab collector

- Status: accepted
- Date: 2026-10-29

## Context

Stage 04 reads container state and logs from a disposable local Docker lab.
The Docker Engine API socket grants control equivalent to root on the Docker
host: whoever can connect can create privileged containers, mount host paths,
exec into containers, restart them or read every container's environment.
Mounting the socket read-only does not change this; it only prevents replacing
the socket file. A Python wrapper that lacks a `restart()` method does not
limit what the process holding the socket is able to request.

Options considered:

1. **Docker SDK for Python.** Convenient, but adds a dependency whose client
   exposes the full mutation API to any code holding it.
2. **Docker CLI subprocess.** Adds argument construction for an external
   binary and returns formatted text that still needs parsing.
3. **Minimal standard-library Engine API client.** Sends only fixed `GET`
   requests to two endpoint templates for allowlisted container names.
4. **Generic socket-filtering proxy container.** Moves enforcement outside
   the collector, but the proxy itself mounts the full socket. Common proxies
   allow whole endpoint groups: permitting container reads also permits
   inspecting every container's environment and exporting or archiving
   container filesystems.
5. **Rootless Docker, an authorization plugin or a dedicated collector
   identity.** These change the daemon or host configuration, which a learning
   lab must not require.

## Decision

Use option 3 for the local lab only, and make the trust boundary explicit:

- The collector is a host CLI that the developer runs on demand. It is not an
  HTTP route, a background service, an agent tool or a model-visible function.
  No lab container mounts the Docker socket.
- The allowlist is code: lab `stage04`, service `training-api`, container
  `aidevops-lab04-api`. Changing it requires a reviewed code change. Requested
  names outside it are rejected before any socket connection.
- Every request passes one guard that permits only `GET` on
  `/v1.44/containers/{name}/json` and `/v1.44/containers/{name}/logs` for
  allowlisted names with fixed query parameters. The client has no other
  request path and exposes only `inspect()` and `logs()`.
- The collector reads a container only if Docker returns the exact requested
  name and the label `ai-devops-agent.lab=stage04`. It copies selected state
  fields and the lab label; environment, mounts, command, network settings and
  other labels are discarded in memory and never reach the report.
- Responses have connection and total-time limits and byte limits. Log lines
  are bounded, redacted with the Stage 03 filter and kept as inert data.

## Consequences

The collector's code cannot send a mutating request, but the operating-system
process still holds whatever rights the socket grants. That privilege equals
the developer's own Docker access, so it adds no new principal on a single
developer machine. The guard protects against coding mistakes and against
untrusted log text influencing requests; it is not a daemon-side control.

This design is not suitable for shared hosts, remote daemons or a deployed
service. The future agent (Stage 06) will receive collected observations, not
the Docker client. Exposing collection beyond a developer's machine requires a
new decision with daemon-side enforcement owned by the project, such as a
separate collector identity behind an exact path and container allowlist.

Only Unix sockets are supported; TCP and TLS Docker hosts are rejected. API
version 1.44 requires Docker Engine 25.0 or newer.
