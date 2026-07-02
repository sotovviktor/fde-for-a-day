# Copyright (c) Microsoft. All rights reserved.
"""Pulumi program: deploy the FDEBench sample app to Azure Container Apps.

Provisions, in a single resource group:

* a **Log Analytics workspace** (Container Apps environment logs land here),
* a **user-assigned managed identity** used for BOTH pulling the image and
  calling Foundry — so no keys or connection strings are stored anywhere,
* an **Azure Container Registry** (image pulled via the identity, admin disabled),
* an **Azure AI Foundry** account (Cognitive Services, kind ``AIServices``) with
  local auth disabled and a single **model deployment**,
* a **Container Apps environment** + **Container App** running the sample image.

The app authenticates to Foundry with Microsoft Entra ID: the identity gets the
``Cognitive Services OpenAI User`` role, its client id is injected as
``AZURE_CLIENT_ID``, and ``DefaultAzureCredential`` in the container exchanges it
for a token. The only connection values the container needs (endpoint, api
version, deployment names, client id) are passed as plain env vars.
"""

import uuid

import pulumi
import pulumi_azure_native as azure_native

# Built-in Azure role definition GUIDs (subscription-scoped).
_ACR_PULL_ROLE = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
_OPENAI_USER_ROLE = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"


def _role_assignment_name(
    scope: pulumi.Input[str],
    principal_id: pulumi.Input[str],
    role_id: str,
) -> pulumi.Output[str]:
    """Deterministic GUID for a role assignment (stable across ``pulumi up`` runs)."""
    return pulumi.Output.all(scope, principal_id).apply(
        lambda parts: str(uuid.uuid5(uuid.NAMESPACE_URL, f"{parts[0]}|{parts[1]}|{role_id}"))
    )


config = pulumi.Config()
location = config.get("location") or "eastus2"
stack = pulumi.get_stack()
name_prefix = f"msfde-{stack}"

# Container image to run. Built and pushed out of band (see infra/README.md):
#   az acr build -r <registry> -t kappor:latest -f apps/sample/Dockerfile .
image_name = "kappor"
image_tag = "latest"

# Foundry model deployment. The deployment name flows to the app as the per-task
# model and is echoed in the X-Model-Name response header.
openai_model = "gpt-5.4-nano"
openai_model_version = "2026-03-17"
openai_model_format = "OpenAI"
deployment_name = openai_model
deployment_sku = "GlobalStandard"
deployment_capacity = 1000
openai_api_version = "2024-10-21"

min_replicas = 1
max_replicas = 1

registry_name = f"{name_prefix}acr".replace("-", "").lower()
foundry_subdomain = f"{name_prefix}-foundry".lower()

resource_group = azure_native.resources.ResourceGroup(
    "rg",
    location=location,
)

workspace = azure_native.operationalinsights.Workspace(
    "logs",
    resource_group_name=resource_group.name,
    location=resource_group.location,
    sku=azure_native.operationalinsights.WorkspaceSkuArgs(name="PerGB2018"),
    retention_in_days=30,
)

workspace_keys = azure_native.operationalinsights.get_shared_keys_output(
    resource_group_name=resource_group.name,
    workspace_name=workspace.name,
)

identity = azure_native.managedidentity.UserAssignedIdentity(
    "app-identity",
    resource_group_name=resource_group.name,
    location=resource_group.location,
)

registry = azure_native.containerregistry.Registry(
    "registry",
    registry_name=registry_name,
    resource_group_name=resource_group.name,
    location=resource_group.location,
    sku=azure_native.containerregistry.SkuArgs(name="Basic"),
    admin_user_enabled=False,
)

# Azure AI Foundry account. A custom subdomain is required for Entra token auth,
# and disabling local auth forbids API keys entirely (managed identity only).
foundry = azure_native.cognitiveservices.Account(
    "foundry",
    resource_group_name=resource_group.name,
    location=resource_group.location,
    kind="AIServices",
    sku=azure_native.cognitiveservices.SkuArgs(name="S0"),
    properties=azure_native.cognitiveservices.AccountPropertiesArgs(
        custom_sub_domain_name=foundry_subdomain, disable_local_auth=True
    ),
)

model_deployment = azure_native.cognitiveservices.Deployment(
    "model",
    resource_group_name=resource_group.name,
    account_name=foundry.name,
    deployment_name=deployment_name,
    sku=azure_native.cognitiveservices.SkuArgs(name=deployment_sku, capacity=deployment_capacity),
    properties=azure_native.cognitiveservices.DeploymentPropertiesArgs(
        model=azure_native.cognitiveservices.DeploymentModelArgs(
            format=openai_model_format,
            name=openai_model,
            version=openai_model_version,
        ),
    ),
)

client_config = azure_native.authorization.get_client_config_output()

acr_pull = azure_native.authorization.RoleAssignment(
    "acr-pull",
    scope=registry.id,
    role_assignment_name=_role_assignment_name(registry.id, identity.principal_id, _ACR_PULL_ROLE),
    principal_id=identity.principal_id,
    principal_type="ServicePrincipal",
    role_definition_id=pulumi.Output.concat(
        "/subscriptions/",
        client_config.subscription_id,
        "/providers/Microsoft.Authorization/roleDefinitions/",
        _ACR_PULL_ROLE,
    ),
)

openai_user = azure_native.authorization.RoleAssignment(
    "openai-user",
    scope=foundry.id,
    role_assignment_name=_role_assignment_name(foundry.id, identity.principal_id, _OPENAI_USER_ROLE),
    principal_id=identity.principal_id,
    principal_type="ServicePrincipal",
    role_definition_id=pulumi.Output.concat(
        "/subscriptions/",
        client_config.subscription_id,
        "/providers/Microsoft.Authorization/roleDefinitions/",
        _OPENAI_USER_ROLE,
    ),
)

environment = azure_native.app.ManagedEnvironment(
    "env",
    resource_group_name=resource_group.name,
    location=resource_group.location,
    app_logs_configuration=azure_native.app.AppLogsConfigurationArgs(
        destination="log-analytics",
        log_analytics_configuration=azure_native.app.LogAnalyticsConfigurationArgs(
            customer_id=workspace.customer_id,
            shared_key=workspace_keys.primary_shared_key.apply(lambda key: key or ""),
        ),
    ),
)

registry_login_server = registry.login_server
image = pulumi.Output.concat(registry_login_server, "/", image_name, ":", image_tag)
# Deterministic *.openai.azure.com endpoint from the subdomain we control — the
# form AsyncAzureOpenAI expects.
openai_endpoint = f"https://{foundry_subdomain}.openai.azure.com/"

container_app = azure_native.app.ContainerApp(
    "app",
    resource_group_name=resource_group.name,
    location=resource_group.location,
    environment_id=environment.id,
    identity=azure_native.app.ManagedServiceIdentityArgs(
        type="UserAssigned",
        user_assigned_identities=[identity.id],
    ),
    configuration=azure_native.app.ConfigurationArgs(
        active_revisions_mode="Single",
        ingress=azure_native.app.IngressArgs(
            external=True,
            target_port=8000,
            transport="auto",
            allow_insecure=False,
        ),
        registries=[
            azure_native.app.RegistryCredentialsArgs(
                server=registry_login_server,
                identity=identity.id,
            )
        ],
    ),
    template=azure_native.app.TemplateArgs(
        containers=[
            azure_native.app.ContainerArgs(
                name="msfde",
                image=image,
                resources=azure_native.app.ContainerResourcesArgs(cpu=1.0, memory="2Gi"),
                env=[
                    azure_native.app.EnvironmentVarArgs(name="AZURE_OPENAI_ENDPOINT", value=openai_endpoint),
                    azure_native.app.EnvironmentVarArgs(name="AZURE_OPENAI_API_VERSION", value=openai_api_version),
                    azure_native.app.EnvironmentVarArgs(name="AZURE_CLIENT_ID", value=identity.client_id),
                    azure_native.app.EnvironmentVarArgs(name="TRIAGE_MODEL", value=model_deployment.name),
                    azure_native.app.EnvironmentVarArgs(name="EXTRACT_MODEL", value=model_deployment.name),
                    azure_native.app.EnvironmentVarArgs(name="ORCHESTRATE_MODEL", value=model_deployment.name),
                ],
                probes=[
                    azure_native.app.ContainerAppProbeArgs(
                        type="Liveness",
                        http_get=azure_native.app.ContainerAppProbeHttpGetArgs(path="/health", port=8000),
                        initial_delay_seconds=10,
                        period_seconds=30,
                    ),
                    azure_native.app.ContainerAppProbeArgs(
                        type="Readiness",
                        http_get=azure_native.app.ContainerAppProbeHttpGetArgs(path="/health", port=8000),
                        initial_delay_seconds=5,
                        period_seconds=10,
                    ),
                ],
            )
        ],
        scale=azure_native.app.ScaleArgs(min_replicas=min_replicas, max_replicas=max_replicas),
    ),
    opts=pulumi.ResourceOptions(depends_on=[acr_pull, openai_user, model_deployment]),
)

app_url = container_app.configuration.apply(
    lambda cfg: f"https://{cfg.ingress.fqdn}" if cfg and cfg.ingress and cfg.ingress.fqdn else ""
)

pulumi.export("appUrl", app_url)
pulumi.export("foundryEndpoint", openai_endpoint)
pulumi.export("registryLoginServer", registry_login_server)
pulumi.export("registryName", registry.name)
pulumi.export("imageReference", image)
pulumi.export("identityClientId", identity.client_id)
pulumi.export("modelDeploymentName", model_deployment.name)
