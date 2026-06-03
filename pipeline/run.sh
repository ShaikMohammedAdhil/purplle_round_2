#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: pipeline/run.sh path/to/video.mp4 [additional run_pipeline.py options]"
  exit 1
fi

video_path="$1"
shift

python pipeline/run_pipeline.py --video "$video_path" "$@"
