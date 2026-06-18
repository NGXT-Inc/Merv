import os, socket
from pathlib import Path
x = 41
artifact = Path(os.environ["NL_ARTIFACT_DIR"]) / "remote_metric.txt"
artifact.parent.mkdir(parents=True, exist_ok=True)
artifact.write_text(f"host={socket.gethostname()} x={x}\n", encoding="utf-8")
print("remote host", socket.gethostname())
print("artifact", artifact)
