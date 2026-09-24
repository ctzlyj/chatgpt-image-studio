---
name: chatgpt-web-image
description: Use a locally hosted ChatGPT Image Studio adapter to generate or edit images with an authorized ChatGPT web account. Trigger when the user asks an agent to use ChatGPT web image generation, gpt-image-2.5, a full ChatGPT session JSON, the local OpenAI-compatible image API, reference-image editing, or the ChatGPT account image quota.
---

# ChatGPT Web Image

Use `scripts/image_studio_client.py` as the only routine entry point. It discovers or installs the public Image Studio repository, starts its loopback-only API, imports an authorized ChatGPT session, and saves generated images locally.

Resolve the script from this Skill's own directory and invoke it by absolute path when the current working directory is elsewhere. Command examples below are relative to the Skill directory.

## Security boundary

- Treat the full session JSON as a login credential. Never quote it in replies, command arguments, source, logs, screenshots, Git, or test fixtures.
- Prefer `configure --clipboard`: ask the user to copy the complete JSON, then read it locally without printing it. After a successful import, the client clears the clipboard if its contents have not changed. Use plain `configure` for a local paste window.
- Use `configure --session-file PATH` only when the user already owns that local file. Never create a plaintext session file from chat content.
- Never send the session or local API key to a non-loopback address. The client rejects remote base URLs.
- Use only accounts and reference images the user is authorized to use. Do not automate registration, verification bypass, or quota evasion.

## First use

Run with the Python executable available to the agent:

```powershell
python scripts/image_studio_client.py configure --clipboard
```

If the service is absent, the client automatically uses the surrounding repository or installs the latest public repository into a per-user application directory, creates an isolated virtual environment, installs locked Python dependencies, and starts the API in the background. Node.js is not required for Agent/API-only use.

If clipboard access is unavailable, run `configure` and let the user paste into the local dialog. Do not ask the user to paste the session into ordinary chat.

Check the redacted account state without exposing credentials:

```powershell
python scripts/image_studio_client.py status
```

## Generate images

```powershell
python scripts/image_studio_client.py generate --prompt "一只陶瓷马克杯，暖白背景，自然光" --count 1 --size 1:1 --output-dir "output/cup"
```

- Use `--model gpt-image-2.5` unless the user explicitly requests a compatible alias.
- Use `--count 1..4`. Split larger requests into deliberate batches.
- `--size` only selects the aspect ratio. The web pixel budget is fixed at about 1.57 MP, so `1:1` is `1254×1254` and `16:9` is `1673×941`. Ask `GET /api/resolution` for the current table.
- Return the absolute paths printed by the client.

## Output size and enlargement

There is **no native 4K on ChatGPT web image generation**. A request such as `--size 3840x2160` keeps the 16:9 ratio and still returns about 1.57 MP; the response reports it as capped rather than fulfilled. Never tell the user a delivery is 4K native.

Use `--upscale 2..4` when the user needs larger files. Enlargement happens after generation and is always labelled:

```powershell
python scripts/image_studio_client.py generate --prompt "一只陶瓷马克杯，暖白背景，自然光" --size 1:1 --upscale 2 --output-dir "output/cup"
```

- The client saves enlarged files as `image-01-upscaled2x-<backend>.png` and reports `native_size`, `size` and the backend label per image.
- On Windows the repository ships a free Real-ESRGAN backend (`tools/realesrgan-ncnn-vulkan` plus the `realesrgan-x4plus` photo model); it runs offline and reports the `realesrgan` backend when a usable GPU is found. Other machines fall back to Lanczos resampling. Call that fallback algorithmic upscaling, never "AI upscaling". Only a `realesrgan` backend in the response is genuine super resolution.
- Report both the native size and the delivered size to the user, and state that the enlarged file is not native pixels.

For long prompts, save only the non-secret prompt in a task-local file and use `--prompt-file PATH`.

## Edit with reference images

```powershell
python scripts/image_studio_client.py edit --prompt "保留商品外观，改为浅灰影棚背景" --image "input/product.png" --output-dir "output/edited"
```

Repeat `--image` for up to 12 authorized PNG, JPEG, or WebP references. The service normalizes outputs to PNG.

## Retry and failure rules

- Record the `request_id` and `batch_id` printed by the client.
- If the result is unknown after a timeout or disconnect, inspect `status` or the local workbench before retrying.
- Retry the exact same request with `--request-id PREVIOUS_ID`; never create a new request ID merely because the response was lost.
- Do not silently move an unknown request to another account. The service deliberately avoids cross-account replay.
- The public alias `gpt-image-2.5` is an adapter name; do not claim that it proves the upstream model version.

Open the optional local workbench only when the user wants to inspect history or manage the account pool:

```powershell
python scripts/image_studio_client.py open
```
