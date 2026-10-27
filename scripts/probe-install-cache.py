import json
import os
import statistics
import subprocess
import time
from pathlib import Path

bench = Path.home() / 'pilot/benches/cache-proof'
results_dir = Path('cache-proof-results')
results_dir.mkdir()
app = bench / 'apps/cache_probe'
module = app / 'cache_probe/cache_probe'
doctype = module / 'doctype/cache_probe_item'
doctype.mkdir(parents=True)
for path in (app / 'cache_probe', module, module / 'doctype', doctype):
    (path / '__init__.py').write_text('')
(app / 'cache_probe/hooks.py').write_text('app_name = "cache_probe"\napp_title = "Cache Probe"\napp_description = "Isolated cache probe"\napp_publisher = "Frappe"\napp_email = "test@example.com"\napp_license = "MIT"\n')
(app / 'cache_probe/modules.txt').write_text('Cache Probe\n')
(doctype / 'cache_probe_item.py').write_text('from frappe.model.document import Document\nclass CacheProbeItem(Document):\n    pass\n')
(doctype / 'cache_probe_item.json').write_text(json.dumps({
    'doctype': 'DocType', 'name': 'Cache Probe Item', 'module': 'Cache Probe',
    'custom': 0, 'istable': 0, 'engine': 'InnoDB',
    'fields': [{'fieldname': 'title', 'fieldtype': 'Data', 'label': 'Title'}],
    'permissions': [{'role': 'System Manager', 'read': 1, 'write': 1, 'create': 1, 'delete': 1}],
}))
with (bench / 'sites/apps.txt').open('a') as stream:
    stream.write('\ncache_probe\n')
environment = {**os.environ, 'PYTHONPATH': str(app)}
python = str(bench / 'env/bin/python')
frappe_call = [python, '-m', 'frappe.utils.bench_helper', 'frappe']
results = []

def execute(argv, log):
    started = time.monotonic()
    result = subprocess.run(argv, cwd=bench / 'sites', env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (results_dir / log).write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f'{argv}: {result.stdout}')
    return time.monotonic() - started, result.stdout

for preclear in (False, True):
    site = f'cache-{int(preclear)}.localhost'
    execute(['pilot', '-b', 'cache-proof', 'new-site', site, '--apps', 'frappe', '--admin-password', 'admin'], f'{site}-create.log')
    timings = [execute([*frappe_call, '--site', site, 'clear-cache'], f'{site}-clear-{repeat}.log')[0] for repeat in range(3)]
    seed = f'''import frappe, json
frappe.init({site!r})
frappe.connect()
mapping = dict(frappe.local.app_modules)
mapping['cache_probe'] = []
frappe.cache.set_value('app_modules', mapping)
frappe.destroy()
'''
    execute([python, '-c', seed], f'{site}-seed.log')
    if preclear:
        execute([*frappe_call, '--site', site, 'clear-cache'], f'{site}-preclear.log')
    elapsed, _ = execute([*frappe_call, '--site', site, 'install-app', 'cache_probe'], f'{site}-install.log')
    execute([*frappe_call, '--site', site, 'clear-cache'], f'{site}-postclear.log')
    inspect = f'''import frappe, json
frappe.init({site!r})
frappe.connect()
print('PROBE_RESULT=' + json.dumps({{'doctype_exists': bool(frappe.db.exists('DocType', 'Cache Probe Item')), 'app_installed': 'cache_probe' in frappe.get_installed_apps()}}))
frappe.destroy()
'''
    _, output = execute([python, '-c', inspect], f'{site}-inspect.log')
    state = json.loads(next(line.removeprefix('PROBE_RESULT=') for line in output.splitlines() if line.startswith('PROBE_RESULT=')))
    results.append({'preclear': preclear, 'install_seconds': elapsed, 'clear_seconds': timings, 'clear_median': statistics.median(timings), **state})
    print(results[-1], flush=True)
    (results_dir / 'results.json').write_text(json.dumps(results, indent=2) + '\n')

assert results[1]['doctype_exists'], results
