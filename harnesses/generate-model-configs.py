#!/usr/bin/env python3
"""
Generate model configuration files for pi-agent and opencode from a llama-server instance.

Queries the llama-server /v1/models endpoint, extracts model metadata, and generates:
  1. ~/.pi/agent/models.json  — pi-agent custom provider configuration
  2. ~/.config/opencode/opencode.json — opencode global configuration

Usage:
  python3 generate-model-configs.py [options]

Options:
  --server-url URL        llama-server base URL (default: http://127.0.0.1:1143)
  --api-key KEY           API key for llama-server (default: llama)
  --pi-models-path PATH   Output path for pi-agent models.json
  --opencode-config-path  Output path for opencode opencode.json
  --pi-provider-name NAME Provider name in pi-agent config (default: riprouter)
  --opencode-provider-name NAME Provider name in opencode config (default: llama.cpp)
  --default-context N     Fallback context window in tokens (default: 128000)
  --dry-run               Print generated configs without writing files
  --verbose               Print detailed extraction info for each model
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate model configs for pi-agent and opencode from llama-server"
    )
    parser.add_argument(
        "--server-url",
        default="http://127.0.0.1:1143",
        help="llama-server base URL (default: http://127.0.0.1:1143)",
    )
    parser.add_argument(
        "--api-key",
        default="llama",
        help="API key for llama-server (default: llama)",
    )
    parser.add_argument(
        "--pi-models-path",
        default=os.path.expanduser("~/.pi/agent/models.json"),
        help="Output path for pi-agent models.json",
    )
    parser.add_argument(
        "--opencode-config-path",
        default=os.path.expanduser("~/.config/opencode/opencode.json"),
        help="Output path for opencode opencode.json",
    )
    parser.add_argument(
        "--pi-provider-name",
        default="riprouter",
        help="Provider name in pi-agent config (default: riprouter)",
    )
    parser.add_argument(
        "--opencode-provider-name",
        default="llama.cpp",
        help="Provider name in opencode config (default: llama.cpp)",
    )
    parser.add_argument(
        "--default-context",
        type=int,
        default=128000,
        help="Fallback context window in tokens (default: 128000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated configs without writing files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed extraction info for each model",
    )
    return parser.parse_args()


def fetch_models(server_url, api_key):
    """Fetch models from the llama-server /v1/models endpoint."""
    url = server_url.rstrip("/") + "/v1/models"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"Error: llama-server returned status {resp.status}", file=sys.stderr)
                sys.exit(1)
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"Error: Cannot reach llama-server at {server_url}: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Malformed JSON response from llama-server: {e}", file=sys.stderr)
        sys.exit(1)

    return data.get("data", [])


def extract_context_window(model, default_context, verbose=False):
    """Extract context window size from model metadata.

    Priority:
      1. --ctx-size in status.args
      2. ctx-size in preset string
      3. meta.n_ctx (loaded models only)
      4. meta.n_ctx_train (native context, loaded models only)
      5. default_context fallback
    """
    args = model.get("status", {}).get("args", [])
    preset = model.get("preset", "")
    meta = model.get("meta", {})

    # Priority 1: --ctx-size in args
    for i, arg in enumerate(args):
        if arg == "--ctx-size" and i + 1 < len(args):
            ctx = int(args[i + 1])
            if verbose:
                print(f"    ctx-size from args: {ctx}")
            return ctx
        elif arg.startswith("--ctx-size="):
            ctx = int(arg.split("=", 1)[1])
            if verbose:
                print(f"    ctx-size from args: {ctx}")
            return ctx

    # Priority 2: ctx-size in preset
    for line in preset.split("\n"):
        line = line.strip()
        if line.startswith("ctx-size") and "=" in line:
            ctx = int(line.split("=", 1)[1].strip())
            if verbose:
                print(f"    ctx-size from preset: {ctx}")
            return ctx

    # Priority 3: meta.n_ctx (loaded models)
    if meta and meta.get("n_ctx"):
        ctx = int(meta["n_ctx"])
        if verbose:
            print(f"    ctx-size from meta.n_ctx: {ctx}")
        return ctx

    # Priority 4: meta.n_ctx_train (native context)
    if meta and meta.get("n_ctx_train"):
        ctx = int(meta["n_ctx_train"])
        if verbose:
            print(f"    ctx-size from meta.n_ctx_train: {ctx}")
        return ctx

    # Priority 5: default
    if verbose:
        print(f"    ctx-size: using default {default_context}")
    return default_context


def extract_reasoning(model, verbose=False):
    """Check if the model has reasoning/thinking support.

    Looks for --reasoning on or --reasoning-format in args and preset.
    """
    args = model.get("status", {}).get("args", [])
    preset = model.get("preset", "")

    # Check args for --reasoning on
    for i, arg in enumerate(args):
        if arg == "--reasoning" and i + 1 < len(args):
            if args[i + 1].lower() == "on":
                if verbose:
                    print("    reasoning: true (from --reasoning on in args)")
                return True
        elif arg.startswith("--reasoning="):
            if arg.split("=", 1)[1].lower() == "on":
                if verbose:
                    print("    reasoning: true (from --reasoning=on in args)")
                return True

    # Check preset for reasoning = on
    for line in preset.split("\n"):
        line = line.strip()
        if line.startswith("reasoning") and "=" in line:
            value = line.split("=", 1)[1].strip().lower()
            if value == "on":
                if verbose:
                    print("    reasoning: true (from preset)")
                return True

    if verbose:
        print("    reasoning: false")
    return False


def extract_thinking_format(model, verbose=False):
    """Extract the reasoning/thinking format (e.g., 'deepseek')."""
    args = model.get("status", {}).get("args", [])
    preset = model.get("preset", "")

    # Check args for --reasoning-format
    for i, arg in enumerate(args):
        if arg == "--reasoning-format" and i + 1 < len(args):
            fmt = args[i + 1]
            if verbose:
                print(f"    thinkingFormat: {fmt} (from args)")
            return fmt
        elif arg.startswith("--reasoning-format="):
            fmt = arg.split("=", 1)[1]
            if verbose:
                print(f"    thinkingFormat: {fmt} (from args)")
            return fmt

    # Check preset for reasoning-format
    for line in preset.split("\n"):
        line = line.strip()
        if line.startswith("reasoning-format") and "=" in line:
            fmt = line.split("=", 1)[1].strip()
            if verbose:
                print(f"    thinkingFormat: {fmt} (from preset)")
            return fmt

    return None


def extract_vision(model, verbose=False):
    """Check if the model supports vision (image input)."""
    modalities = model.get("architecture", {}).get("input_modalities", [])
    has_vision = "image" in modalities
    if verbose:
        print(f"    vision: {has_vision} (modalities: {modalities})")
    return has_vision


def extract_model_name(model, verbose=False):
    """Extract human-readable model name from aliases or id."""
    aliases = model.get("aliases", [])
    if aliases:
        if verbose:
            print(f"    name: {aliases[0]} (from aliases)")
        return aliases[0]
    name = model.get("id", "unknown")
    if verbose:
        print(f"    name: {name} (from id)")
    return name


def extract_model_id(model):
    """Extract the model ID."""
    return model.get("id", "unknown")


def extract_model_data(model, default_context, verbose=False):
    """Extract all relevant metadata from a single model entry."""
    model_id = extract_model_id(model)
    name = extract_model_name(model, verbose)
    context_window = extract_context_window(model, default_context, verbose)
    max_tokens = context_window // 2
    reasoning = extract_reasoning(model, verbose)
    thinking_format = extract_thinking_format(model, verbose) if reasoning else None
    has_vision = extract_vision(model, verbose)

    return {
        "id": model_id,
        "name": name,
        "context_window": context_window,
        "max_tokens": max_tokens,
        "reasoning": reasoning,
        "thinking_format": thinking_format,
        "has_vision": has_vision,
    }


def build_pi_agent_config(model_data_list, args):
    """Build the pi-agent models.json configuration.

    Merges with existing config if present, replacing only the provider
    matching the configured provider name.
    """
    pi_config = {"providers": {}}

    # Try to load existing config
    existing_path = Path(args.pi_models_path)
    if existing_path.exists():
        try:
            with open(existing_path, "r") as f:
                existing = json.load(f)
            pi_config = existing
            if "providers" not in pi_config:
                pi_config["providers"] = {}
        except (json.JSONDecodeError, IOError):
            pass  # Start fresh if existing config is unreadable

    # Build the provider entry
    provider_entry = {
        "baseUrl": args.server_url.rstrip("/") + "/v1",
        "api": "openai-completions",
        "apiKey": args.api_key,
        "models": [],
    }

    for data in model_data_list:
        entry = {
            "id": data["id"],
            "name": data["name"],
            "input": ["text", "image"] if data["has_vision"] else ["text"],
            "contextWindow": data["context_window"],
            "maxTokens": data["max_tokens"],
        }

        if data["reasoning"]:
            entry["reasoning"] = True
            if data["thinking_format"]:
                entry["compat"] = {"thinkingFormat": data["thinking_format"]}

        provider_entry["models"].append(entry)

    pi_config["providers"][args.pi_provider_name] = provider_entry
    return pi_config


def build_opencode_config(model_data_list, args):
    """Build the opencode opencode.json configuration.

    Merges with existing config if present, updating only the provider
    matching the configured provider name while preserving all other keys.
    """
    opencode_config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {},
    }

    # Try to load existing config
    existing_path = Path(args.opencode_config_path)
    if existing_path.exists():
        try:
            with open(existing_path, "r") as f:
                existing = json.load(f)
            opencode_config = existing
            if "provider" not in opencode_config:
                opencode_config["provider"] = {}
        except (json.JSONDecodeError, IOError):
            pass  # Start fresh if existing config is unreadable

    # Build the provider entry
    provider_entry = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "llama-server (local)",
        "options": {
            "baseURL": args.server_url.rstrip("/") + "/v1",
        },
        "models": {},
    }

    for data in model_data_list:
        provider_entry["models"][data["id"]] = {
            "name": data["name"],
            "limit": {
                "context": data["context_window"],
                "output": data["max_tokens"],
            },
        }

    opencode_config["provider"][args.opencode_provider_name] = provider_entry
    return opencode_config


def write_config(config, path, dry_run=False, verbose=False):
    """Write config to file, creating directories and backing up existing files."""
    path = Path(path)
    path_str = str(path)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"Would write to: {path_str}")
        print(f"{'='*60}")
        print(json.dumps(config, indent=2))
        return

    # Create parent directories if needed
    path.parent.mkdir(parents=True, exist_ok=True)

    # Back up existing file
    if path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        path.rename(backup_path)
        if verbose:
            print(f"Backed up existing config to: {backup_path}")

    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print(f"Written: {path_str}")


def main():
    args = parse_args()

    if args.verbose:
        print(f"Querying llama-server at {args.server_url}/v1/models ...")

    models = fetch_models(args.server_url, args.api_key)

    # Extract all model data once (avoids duplicate verbose output)
    all_model_data = [
        extract_model_data(model, args.default_context, args.verbose)
        for model in models
    ]

    if args.verbose:
        print(f"Found {len(all_model_data)} models\n")
        for data in all_model_data:
            print(f"  Model: {data['id']}")
            print(f"    name: {data['name']}")
            print(f"    contextWindow: {data['context_window']}")
            print(f"    maxTokens: {data['max_tokens']}")
            print(f"    reasoning: {data['reasoning']}")
            if data['thinking_format']:
                print(f"    thinkingFormat: {data['thinking_format']}")
            print(f"    vision: {data['has_vision']}")
            print()

    # Generate pi-agent config
    pi_config = build_pi_agent_config(all_model_data, args)
    write_config(pi_config, args.pi_models_path, args.dry_run, args.verbose)

    # Generate opencode config
    opencode_config = build_opencode_config(all_model_data, args)
    write_config(opencode_config, args.opencode_config_path, args.dry_run, args.verbose)


if __name__ == "__main__":
    main()
