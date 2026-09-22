# Deploying behind LiteLLM proxy

If your apps reach their models through a [LiteLLM proxy](https://docs.litellm.ai/docs/simple_proxy), you can add jev-guard once, in the proxy, and every app behind it is guarded without code changes.

## 1. Install next to the proxy

```bash
pip install jev-guard "litellm[proxy]"
export TYPESAFE_API_KEY=sk-...
export JEV_GUARD_DEFAULT_POLICY=support_agent   # or a path to your policy YAML
```

## 2. Register the guardrail

```yaml
# config.yaml
model_list:
  - model_name: gpt
    litellm_params:
      model: openai/gpt-5.6-luna

guardrails:
  - guardrail_name: jev-guard-input
    litellm_params:
      guardrail: jev_guard.integrations.litellm_proxy.JevGuardrail
      mode: pre_call        # check the user's message before the model is called
      default_on: true
  - guardrail_name: jev-guard-output
    litellm_params:
      guardrail: jev_guard.integrations.litellm_proxy.JevGuardrail
      mode: post_call       # check the reply before it's returned
      default_on: true
```

```bash
litellm --config config.yaml
```

## What callers see

A blocked request is rejected by the proxy with the policy's safe reply and the reasons, e.g.:

```
I can only help with questions about your account or order. (jev-guard: is_prompt_injection: 0.97 > 0.85 (critical))
```

Internally the hook raises `JevGuardrailBlockedError`, a `ValueError` (which is how LiteLLM guardrails reject requests) that also carries `.verdict`. The guardrail respects LiteLLM's per-request guardrail selection (`should_run_guardrail`).

## Limits in v0.1

- Streaming responses through the proxy are input-checked only. To check streamed output, use `Guard.astream_check` in the app.
- Written against LiteLLM 1.102's `CustomGuardrail` interface. LiteLLM changes quickly, so pin the version you tested.
