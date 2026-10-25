# Test harness only (installed into the throwaway venv, never into spendlint): log network and subprocess
# activity of every Python process in the venv, to show what spendlint does and does not touch.
import os
import sys

_LOG = os.environ.get("SPENDLINT_AUDIT_LOG")
if _LOG:

    def _hook(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "urllib.Request", "subprocess.Popen"):
            try:
                detail = (
                    (args[1] if isinstance(args[1], str) else " ".join(map(str, args[1])))
                    if event == "subprocess.Popen"
                    else args[:2]
                )
                with open(_LOG, "a", encoding="utf-8") as fh:
                    fh.write(f"{event} {detail}\n")
            except Exception:
                pass

    sys.addaudithook(_hook)
