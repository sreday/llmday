"""hello-deck/chat/form-<brand>.png - the empty "Let's talk!" lead form (Sponsor / Host / Partner) from
the brand's home page, every conference chip on, for the deck's "Let's chat!" slide (Marek 2026-10-31: "show how easy it is to start a conversation").
Run by hand when the form changes, then copy hello-deck/chat/ into the other three repos (the folder is identical):

    pip install websocket-client pillow
    python hello-deck/tools/shoot_lead_form.py            # all brands, from the live sites
    python hello-deck/tools/shoot_lead_form.py llmday     # one brand

Drives a headless Chrome over the DevTools protocol: opens <site>/#sponsor (which unrolls the form), crops to the
form's panel at 2x.
"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
SITES = {'sreday': 'https://sreday.com/', 'llmday': 'https://llmday.com/', 'platformday': 'https://platformday.com/',
         'pec': 'https://promptengineering.rocks/'}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'chat')
PORT = 9333


class Tab:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get('id') == self.n:
                if 'error' in msg:
                    raise RuntimeError('%s: %s' % (method, msg['error']))
                return msg.get('result', {})

    def js(self, expr):
        return self.call('Runtime.evaluate', expression=expr, returnByValue=True, awaitPromise=True)['result'].get('value')


def shoot(tab, key, url):
    tab.call('Page.navigate', url=url + '#sponsor')
    for _ in range(60):
        time.sleep(.5)
        if tab.js('document.readyState') == 'complete' and tab.js('!!document.getElementById("lead-panel")'):
            break
    else:
        print('%s: no lead form at %s' % (key, url))
        return
    time.sleep(2)
    # unrolled, no transition, every field empty, all three brand chips on
    tab.js('''(function () {
      var p = document.getElementById('lead-panel');
      if (window.leadOpen) leadOpen(); else if (!p.classList.contains('open') && window.leadToggle) leadToggle();
      p.style.transition = 'none'; p.style.maxHeight = 'none'; p.style.height = 'auto'; p.style.opacity = '1';
      p.classList.add('open');
      document.querySelectorAll('#lead-form input[type=text], #lead-form input[type=email]').forEach(function (i) { i.value = ''; i.blur(); });
      document.querySelectorAll('.lead-group.is-invalid').forEach(function (g) { g.classList.remove('is-invalid'); });
      // every conference chip on (Marek 2026-11-01: "have all 3 brands highlighted")
      document.querySelectorAll('#lead-form input[name=brands]').forEach(function (i) { i.checked = true; i.dispatchEvent(new Event('change', {bubbles: true})); });
      document.querySelectorAll('.lead-group.is-invalid').forEach(function (g) { g.classList.remove('is-invalid'); });
      p.scrollIntoView({block: 'center'});
    })()''')
    time.sleep(1.5)
    r = tab.js('(function () { var b = document.querySelector("#lead-panel .lead-inner") || document.getElementById("lead-panel");'
               ' var r = b.getBoundingClientRect(); return [r.left + scrollX, r.top + scrollY, r.width, r.height]; })()')
    x, y, w, h = r
    shot = tab.call('Page.captureScreenshot', format='png', captureBeyondViewport=True,
                    clip={'x': x, 'y': y, 'width': w, 'height': h, 'scale': 2})
    # the panel's 16 px rounded corners: the page colour behind them made transparent (the slide shows through)
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(base64.b64decode(shot['data']))).convert('RGBA')
    mask = Image.new('L', im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.size[0] - 1, im.size[1] - 1), radius=32, fill=255)
    im.putalpha(mask)
    path = os.path.join(OUT, 'form-%s.png' % key)
    im.save(path, optimize=True)
    print('%s: %s (%dx%d css px)' % (key, os.path.normpath(path), w, h))


def main():
    keys = sys.argv[1:] or list(SITES)
    os.makedirs(OUT, exist_ok=True)
    prof = tempfile.mkdtemp(prefix='leadshot-')
    chrome = subprocess.Popen([CHROME, '--headless=new', '--remote-debugging-port=%d' % PORT, '--user-data-dir=' + prof,
                               '--window-size=1440,1400', '--hide-scrollbars', 'about:blank'])
    try:
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % PORT))
                page = next(t for t in tabs if t['type'] == 'page')
                break
            except Exception:                                # noqa: BLE001 - Chrome still starting
                time.sleep(.5)
        tab = Tab(page['webSocketDebuggerUrl'])
        tab.call('Page.enable')
        tab.call('Emulation.setDeviceMetricsOverride', width=1440, height=1400, deviceScaleFactor=1, mobile=False)
        for k in keys:
            shoot(tab, k, SITES[k])
    finally:
        chrome.terminate()


if __name__ == '__main__':
    main()
