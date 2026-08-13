# Changelog

## 1.1.0

The project moves into its own repository: **github.com/driin0/ha-mcp-server**,
AGPL-3.0.

Nothing about the server changes with this release — the code is the one that
had been developed inside the Home Assistant app repository, which was the
version actually in use. What changes is where it lives and how it is shipped.

### Why

The code existed in two places: a private Docker repository and a copy inside
the Home Assistant app. They had drifted badly — **23 files differed**, and the
copy in the app was two and a half months ahead, holding fixes the other never
received. Whoever had deployed the Docker one was running a version with five
broken tools.

There is now a single source. The Home Assistant app carries only its packaging
and consumes the image published here.

### What this brings

* **A container image**, `ghcr.io/driin0/ha-mcp-server`, multi-architecture
  (amd64, arm64), 119 MB — built in two stages so the compiler toolchain stays
  out of the final image
* **`compose.yaml`, `deploy.sh` and `.env.sample`**, so the server can be run
  outside Home Assistant
* **AGPL-3.0**, with the licence text shipped inside the image

### For the Home Assistant app

The app no longer installs dependencies: it copies them, already compiled, from
this image. Its build no longer needs `gcc`, and both distributions are
guaranteed to run the *same* packages rather than the same `requirements.txt`
resolved twice.

This works because both images are Alpine with the same Python minor. The
Dockerfile imports the native extensions at build time, so a future base image
that breaks the ABI fails the build instead of failing at first start.
