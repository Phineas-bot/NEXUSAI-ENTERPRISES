"""Public API package (FastAPI + gRPC) for the Cloud Drive runtime."""

from pathlib import Path
import sys

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
	# Ensure protoc-generated modules (control_plane_pb2*) are importable as top-level names.
	sys.path.append(str(_PKG_DIR))

from .grpc_server import build_grpc_server  # noqa: F401  (re-export for convenience)