"""One LAN HTTPS listener, with the production Next.js server on loopback."""
import asyncio
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'lan-config.json').read_text(encoding='utf-8'))
DATA = ROOT / 'data'
os.environ.update({
    'DB_PATH': str(DATA / 'oasis.db'), 'OASIS_MEDIA_ROOT': str(DATA),
    'HOST': CONFIG['host'], 'PORT': str(CONFIG['port']),
    'OASIS_TRUST_LOOPBACK': '0',
    'OASIS_SELF_HOSTED': '1',
    'OASIS_ENABLE_TRANSLATION': '0',
    'ALLOWED_ORIGINS': f"https://{CONFIG['host']}:{CONFIG['port']}",
    'OASIS_CHROME_PATH': str(ROOT / 'runtime/chrome/chrome-win64/chrome.exe'),
    'TEMP': str(DATA / 'temp'), 'TMP': str(DATA / 'temp'),
    'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
})
os.environ['PATH'] = str(ROOT / 'runtime/bin') + os.pathsep + os.environ.get('PATH', '')
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from api import app

_proxy = None


async def open_proxy():
    global _proxy
    _proxy = httpx.AsyncClient(base_url=f"http://127.0.0.1:{CONFIG['frontend_port']}",
        trust_env=False, timeout=httpx.Timeout(30))
    for _ in range(60):
        try:
            response = await _proxy.get('/')
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError('Local frontend did not become ready')


async def close_proxy():
    if _proxy:
        await _proxy.aclose()


app.router.add_event_handler('startup', open_proxy)
app.router.add_event_handler('shutdown', close_proxy)


@app.api_route('/{path:path}', methods=['GET', 'HEAD'])
async def frontend(request: Request, path: str):
    if path == 'api' or path.startswith('api/'):
        raise HTTPException(status_code=404)
    query = request.url.query
    target = f"http://127.0.0.1:{CONFIG['frontend_port']}/" + path.lstrip('/') + ('?' + query if query else '')
    # The destination, Host and protocol are fixed, never client-supplied.
    forwarded = {name: request.headers[name] for name in
        ('accept', 'rsc', 'next-router-state-tree', 'next-router-prefetch', 'next-url')
        if name in request.headers}
    upstream = await _proxy.send(_proxy.build_request(request.method, target,
        headers=forwarded), stream=True)
    headers = {key: value for key, value in upstream.headers.items()
        if key.lower() in {'content-type', 'content-length', 'content-encoding', 'cache-control', 'etag', 'vary'}}
    headers['X-Content-Type-Options'] = 'nosniff'
    headers['Referrer-Policy'] = 'no-referrer'
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
        headers=headers, background=BackgroundTask(upstream.aclose))


def main():
    if os.name == 'nt':
        import windows_limits
        windows_limits.limit_job(memory_bytes=2 * 1024**3, processes=128)
    node_env = dict(os.environ, HOSTNAME='127.0.0.1', PORT=str(CONFIG['frontend_port']),
        NEXT_TELEMETRY_DISABLED='1', NODE_ENV='production', OASIS_LAN_BUILD='1')
    frontend_log = open(ROOT / 'logs/frontend.log', 'ab')
    node = subprocess.Popen([str(ROOT / 'runtime/node.exe'), str(ROOT / 'frontend/server.js')],
        cwd=ROOT / 'frontend', env=node_env, stdout=frontend_log, stderr=frontend_log,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        uvicorn.run(app, host=CONFIG['host'], port=CONFIG['port'], proxy_headers=False,
            ssl_keyfile=str(DATA / 'certs/server.key'), ssl_certfile=str(DATA / 'certs/server.crt'),
            access_log=False, limit_concurrency=32, timeout_keep_alive=5, backlog=64)
    finally:
        node.terminate()
        try:
            node.wait(timeout=10)
        except subprocess.TimeoutExpired:
            node.kill()
            node.wait()
        frontend_log.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
