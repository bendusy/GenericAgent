#!/usr/bin/env python3
import json, py_compile, sys
from pathlib import Path
root=Path(__file__).resolve().parent
repo=root.parent
py_compile.compile(str(root/'proxy.py'), doraise=True)
schema=json.loads((repo/'assets/tools_schema.json').read_text())
ga={t.get('name') or t.get('function',{}).get('name') for t in schema}
ga.discard(None)
mapping=json.loads((root/'tool_name_mapping.json').read_text())
print('GA tools:', sorted(ga))
print('mapping keys:', sorted(mapping))
print('keys_match:', ga==set(mapping))
print('values_unique:', len(mapping.values())==len(set(mapping.values())))
print('no_sessions_exposed:', not any(str(v).startswith('sessions_') for v in mapping.values()))
if ga!=set(mapping): sys.exit('mapping keys do not match tools_schema')
if len(mapping.values())!=len(set(mapping.values())): sys.exit('mapping values not unique')
if any(str(v).startswith('sessions_') for v in mapping.values()): sys.exit('sessions_* exposed')
print('OK')
