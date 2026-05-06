# claude-max-proxy for GenericAgent

Usage:

```bash
cd /Users/ben/Projects/GenericAgent/claude-max-proxy
python3 verify_proxy.py
DRY_RUN=1 ./start_proxy.sh   # local capture test, no upstream
DRY_RUN=0 ./start_proxy.sh   # real upstream forwarding
```

To route GA through this proxy, set the selected NativeClaudeSession config `apibase` to:

```python
'apibase': 'http://127.0.0.1:5678',
```

This directory intentionally does not store real tokens or captures. Optional real CC capture may be placed as `true_cc_capture.json`; do not commit it.
