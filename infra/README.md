# 🛰️ Infrastructure as Code — Deploying to Orbit

> *"Your triage system is only as reliable as the infrastructure it runs on. A perfect prompt means nothing if your container can't survive a cold start, and a flawless model is useless if the endpoint isn't reachable. Deploy it like you're launching a hull repair drone — test it, trust it, and make sure it comes back."*
> — Chief Signal Officer Mehta, margin note on the station's IaC runbook

The `infra/` folder contains infrastructure as code (IaC) configurations for provisioning and managing cloud resources using [Pulumi](https://www.pulumi.com/) with [Python and uv](https://www.pulumi.com/docs/iac/languages-sdks/python/#uv). Think of it as your station blueprint — except this station runs in Azure instead of orbiting at 0.3 AU.

## Project layout

```
infra/
└── app/
    ├── __main__.py      # Pulumi program — your orbital deployment manifest
    ├── Pulumi.yaml      # Project settings — station configuration
    └── pyproject.toml   # Python dependencies (Pulumi SDK, Azure SDKs, etc.)
```

## What this stack deploys

A single resource group containing everything the sample app needs, wired for
**passwordless** operation — no keys or connection strings are stored anywhere:

- **Log Analytics workspace** — Container Apps environment logs (the app's
  stdout/stderr) land here.
- **User-assigned managed identity** — one identity used for *both* pulling the
  image and calling Foundry.
- **Azure Container Registry** (admin user disabled) — pulled with the identity
  via the `AcrPull` role.
- **Azure AI Foundry** account (`AIServices`) with **local auth disabled** plus a
  configurable **model deployment**. The identity gets the `Cognitive Services
  OpenAI User` role.
- **Container Apps environment + Container App** — runs the sample image on
  external ingress (port 8000) with a `/health` probe. The app authenticates to
  Foundry with `DefaultAzureCredential`; the identity's client id and the Foundry
  endpoint / api version / deployment names are injected as plain env vars.

## Getting started

```bash
cd infra/app
uv sync
pulumi login --local
export PULUMI_CONFIG_PASSPHRASE=$(openssl rand -hex 32)
pulumi stack select <name> --create

# Authenticate to your subscription and pick a region (default eastus2).
az login
pulumi config set location eastus2

# Provision the registry, Foundry, identity, environment, and app.
pulumi up

# Build & push the image the Container App expects (kappor:latest). Build from
# the py/ workspace root so the shared common libs are in the build context.
az acr build \
  --registry "$(pulumi stack output registryName)" \
  --image kappor:latest \
  --file apps/sample/Dockerfile \
  ../../py

# The Container App pulls on the next replica start; verify it's live.
curl -s "$(pulumi stack output appUrl)/health"
```

> **Ordering note.** `pulumi up` creates the Container App before the image
> exists, so its first revision stays unhealthy until `az acr build` pushes
> `kappor:latest`. Container Apps re-pulls automatically (image pull policy is
> `always`), so the app goes healthy shortly after the push — no second
> `pulumi up` needed.

## Configuration

The only override is the Azure region; everything else is hardcoded in
`__main__.py` with sensible defaults (no need to parametrize what never changes):

| Key | Default | Notes |
|---|---|---|
| `location` | `eastus2` | Azure region (also used for the resource group). Set with `pulumi config set location <region>`. |

Hardcoded in `__main__.py` (edit the constants there if you need to change them):
the container runs `kappor:latest` at **1 CPU / 2 GiB** with `1`–`3` replicas

Useful stack outputs: `appUrl`, `foundryEndpoint`, `registryName`,
`registryLoginServer`, `identityClientId`, `modelDeploymentName`.

For more details, see [Pulumi's documentation](https://www.pulumi.com/docs/).

> **Tip from Station Ops:** Deploy early. The number of operators who deploy at hour 23 and then discover their container won't start is... nonzero. Much like hull breach drills, the best time to test your deployment is before the emergency. The second-best time is not 30 minutes before submission. The scoring computer cannot reach localhost, and neither can Commander Kapoor's patience.
