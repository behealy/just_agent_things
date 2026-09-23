#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BUILDS_DIR="$SCRIPT_DIR/builds"
CONFIGS_DIR="$SCRIPT_DIR/model_configs"
LLAMA_PORT=1143

# ── Helpers ──────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [COMMAND] [OPTIONS]

Commands:
  start          Start the llama-server (default command)
  list-builds    List available backend builds

Options (for 'start'):
  --build NAME   Use backend build from builds/NAME/
                 (default: first available build, or only build if one exists)
  --help, -h     Show this help message

Examples:
  ./start.sh                          # start with default build
  ./start.sh --build llama-b1308      # start with a specific build
  ./start.sh list-builds              # list available builds
EOF
}

# Resolve which build directory to use.
# If a name is given, validate it exists.
# Otherwise pick the first (alphabetically) or only build.
resolve_build() {
    local requested_build="${1:-}"

    local builds=()
    if [ -d "$BUILDS_DIR" ]; then
        while IFS= read -r -d '' d; do
            builds+=("$(basename "$d")")
        done < <(find "$BUILDS_DIR" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
    fi

    if [ ${#builds[@]} -eq 0 ]; then
        echo "Error: no backend builds found in $BUILDS_DIR" >&2
        exit 1
    fi

    if [ -n "$requested_build" ]; then
        if [ ! -d "$BUILDS_DIR/$requested_build" ]; then
            echo "Error: build '$requested_build' not found in $BUILDS_DIR" >&2
            echo "Available builds: ${builds[*]}" >&2
            exit 1
        fi
        echo "$BUILDS_DIR/$requested_build"
    elif [ ${#builds[@]} -eq 1 ]; then
        echo "$BUILDS_DIR/${builds[0]}"
    else
        echo "$BUILDS_DIR/${builds[0]}"
    fi
}

list_builds() {
    if [ ! -d "$BUILDS_DIR" ]; then
        echo "No builds directory found at $BUILDS_DIR"
        exit 1
    fi

    local builds=()
    while IFS= read -r -d '' d; do
        builds+=("$(basename "$d")")
    done < <(find "$BUILDS_DIR" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

    if [ ${#builds[@]} -eq 0 ]; then
        echo "No backend builds found."
        exit 0
    fi

    local default_build="${builds[0]}"

    echo "Available backend builds in $BUILDS_DIR:"
    echo ""
    for b in "${builds[@]}"; do
        if [ "$b" = "$default_build" ]; then
            echo "  $b  ← (default)"
        else
            echo "  $b"
        fi
    done
    echo ""
    echo "Total: ${#builds[@]} build(s)"
}

# ── Platform loaders ─────────────────────────────────────────────────────

darwin_load() {
    local build_name="$1"

    # Get Model Name
    MODEL=$(system_profiler SPHardwareDataType | grep "Model Name:" | awk -F': ' '{print $2}')
    # Get Chip
    CHIP=$(system_profiler SPHardwareDataType | grep "Chip:" | awk -F': ' '{print $2}')
    # Get Memory
    MEM=$(system_profiler SPHardwareDataType | grep "Memory:" | awk -F': ' '{print $2}')
    # Get Model Identifier (contains size info implicitly like '16,1')
    ID=$(system_profiler SPHardwareDataType | grep "Model Identifier:" | awk -F': ' '{print $2}')

    echo "Model: $MODEL ($ID)"
    echo "Chip: $CHIP"
    echo "Memory: $MEM"

    local cfg=""
    if [[ "$CHIP" == "Apple M4 Pro" ]]; then
        cfg="$CONFIGS_DIR/m4Pro_24g.ini"
    elif [[ "$CHIP" == "Apple M1 Pro" ]]; then
        cfg="$CONFIGS_DIR/m1Pro_32g.ini"
    elif [[ "$CHIP" == "Apple M2 Max" ]]; then
        cfg="$CONFIGS_DIR/m2Max_32g.ini"
    fi

    if [ -z "$cfg" ]; then
        echo "Error: no model config found for chip '$CHIP'" >&2
        echo "Available configs: $(ls "$CONFIGS_DIR"/*.ini 2>/dev/null | xargs -n1 basename || echo 'none')" >&2
        exit 1
    fi

    if [ ! -f "$cfg" ]; then
        echo "Error: config file not found: $cfg" >&2
        exit 1
    fi

    echo "Using llama-server preset: $cfg"
    echo ""

    local build_dir
    build_dir=$(resolve_build "$build_name")
    echo "Using build: $(basename "$build_dir")"
    echo ""

    local server="$build_dir/llama-server"
    if [ ! -x "$server" ]; then
        echo "Error: llama-server not found or not executable at $server" >&2
        exit 1
    fi

    export DYLD_LIBRARY_PATH="$build_dir:${DYLD_LIBRARY_PATH:-}"
    "$server" --host 0.0.0.0 --port "$LLAMA_PORT" --models-preset "$cfg"
}

linux_load() {
    local build_name="$1"

    local hostname
    hostname=$(hostname)
    echo "Hostname: $hostname"

    # Map hostname to config file
    local cfg="$CONFIGS_DIR/${hostname}.ini"

    if [ ! -f "$cfg" ]; then
        echo "Error: no model config found for hostname '$hostname' ($cfg)" >&2
        echo "Available configs: $(ls "$CONFIGS_DIR"/*.ini 2>/dev/null | xargs -n1 basename || echo 'none')" >&2
        exit 1
    fi

    echo "Using llama-server preset: $cfg"
    echo ""

    local build_dir
    build_dir=$(resolve_build "$build_name")
    echo "Using build: $(basename "$build_dir")"
    echo ""

    local server="$build_dir/llama-server"
    if [ ! -x "$server" ]; then
        echo "Error: llama-server not found or not executable at $server" >&2
        exit 1
    fi

    export LD_LIBRARY_PATH="$build_dir:${LD_LIBRARY_PATH:-}"
    "$server" --host 0.0.0.0 --port "$LLAMA_PORT" --models-preset "$cfg"
}

# ── Main ─────────────────────────────────────────────────────────────────

# Pre-scan all arguments for global flags, then find the command
BUILD_NAME=""
COMMAND="start"  # default
POSITIONAL_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --build)
            if [ -z "${2:-}" ]; then
                echo "Error: --build requires an argument" >&2
                exit 1
            fi
            BUILD_NAME="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        -*)
            echo "Error: unknown option '$1'" >&2
            usage >&2
            exit 1
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# First positional arg is the command
if [ ${#POSITIONAL_ARGS[@]} -gt 0 ]; then
    COMMAND="${POSITIONAL_ARGS[0]}"
fi

case "$COMMAND" in
    start)
        ;;
    list-builds)
        list_builds
        exit 0
        ;;
    *)
        echo "Error: unknown command '$COMMAND'" >&2
        usage >&2
        exit 1
        ;;
esac

# Dispatch to platform loader with the resolved build name
if [ "$(uname -s)" = "Darwin" ]; then
    darwin_load "$BUILD_NAME"
elif [ "$(uname -s)" = "Linux" ]; then
    linux_load "$BUILD_NAME"
else
    echo "Unknown OS: $(uname -s)" >&2
    exit 1
fi
