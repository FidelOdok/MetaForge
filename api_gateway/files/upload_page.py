# ruff: noqa: E501 - an HTML page, kept as written
"""The page an upload link opens: pick files or a folder, send, finish.

A Python string rather than a ``.html`` file beside this module, so it ships
in a wheel without package-data configuration. ``__DESCRIPTION__`` and
``__EXPIRES__`` are filled in, HTML-escaped, by ``api_gateway.files.routes``.
The page posts to its own URL, so it works through the gateway or a sidecar.
"""

UPLOAD_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send files to MetaForge</title>
<style>
:root { --bg:#f6f6f4; --card:#fff; --ink:#1c1c1a; --muted:#64645e; --line:#deddd7; --accent:#e4570f; --ok:#1f7a4d; --bad:#a3312b; }
@media (prefers-color-scheme: dark) { :root { --bg:#121211; --card:#1c1c1a; --ink:#ecebe6; --muted:#a3a29b; --line:#34332f; --accent:#ff7a33; --ok:#5fc596; --bad:#f08a80; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:640px; margin:0 auto; padding:32px 16px; }
h1 { font-size:22px; margin:0 0 4px; }
p { margin:0 0 12px; color:var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-top:16px; }
.drop { border:2px dashed var(--line); border-radius:10px; padding:28px 16px; text-align:center; }
.drop.over { border-color:var(--accent); }
.row { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; margin-top:12px; }
button, label.btn { font:inherit; border:1px solid var(--line); background:var(--bg); color:var(--ink); border-radius:6px; padding:7px 14px; cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button:disabled { opacity:.5; cursor:default; }
input[type=file] { display:none; }
ul { list-style:none; padding:0; margin:12px 0 0; }
li { display:flex; justify-content:space-between; gap:8px; padding:6px 0; border-bottom:1px solid var(--line); font-size:14px; word-break:break-all; }
li:last-child { border-bottom:0; }
.ok { color:var(--ok); } .bad { color:var(--bad); } .muted { color:var(--muted); }
</style>
</head>
<body>
<main>
  <h1>Send files to MetaForge</h1>
  <p>__DESCRIPTION__</p>
  <p class="muted">This link works until __EXPIRES__. Files wait for a person to approve them before MetaForge uses them.</p>
  <div class="card">
    <div class="drop" id="drop">
      <div>Drop files or a folder here</div>
      <div class="row">
        <label class="btn" for="files">Choose files</label>
        <label class="btn" for="folder">Choose a folder</label>
      </div>
      <input type="file" id="files" multiple>
      <input type="file" id="folder" webkitdirectory multiple>
    </div>
    <ul id="list"></ul>
    <div class="row">
      <button class="primary" id="finish" disabled>Finish and send for approval</button>
    </div>
    <p id="status" class="muted" style="text-align:center;margin-top:12px"></p>
  </div>
</main>
<script>
(function () {
  var list = document.getElementById('list');
  var status = document.getElementById('status');
  var finish = document.getElementById('finish');
  var sent = 0, busy = 0;
  var url = window.location.pathname;

  function row(name) {
    var li = document.createElement('li');
    var n = document.createElement('span'); n.textContent = name;
    var s = document.createElement('span'); s.textContent = 'sending'; s.className = 'muted';
    li.appendChild(n); li.appendChild(s); list.appendChild(li);
    return s;
  }

  function send(file) {
    var name = file.webkitRelativePath || file.relativePath || file.name;
    var mark = row(name);
    var form = new FormData();
    form.append('files', file, name);
    busy += 1; finish.disabled = true;
    fetch(url, { method: 'POST', body: form }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) { mark.textContent = res.body.detail || 'refused'; mark.className = 'bad'; return; }
      var entry = (res.body.files || []).filter(function (f) { return f.name === name.split('\\').join('/'); })[0];
      if (entry && entry.error) { mark.textContent = entry.error; mark.className = 'bad'; }
      else { mark.textContent = 'ready'; mark.className = 'ok'; sent += 1; }
    }).catch(function () {
      mark.textContent = 'failed to send'; mark.className = 'bad';
    }).then(function () {
      busy -= 1; finish.disabled = busy > 0 || sent === 0;
    });
  }

  function sendAll(files) { for (var i = 0; i < files.length; i++) send(files[i]); }
  document.getElementById('files').addEventListener('change', function (e) { sendAll(e.target.files); });
  document.getElementById('folder').addEventListener('change', function (e) { sendAll(e.target.files); });

  var drop = document.getElementById('drop');
  drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.className = 'drop over'; });
  drop.addEventListener('dragleave', function () { drop.className = 'drop'; });
  drop.addEventListener('drop', function (e) {
    e.preventDefault(); drop.className = 'drop';
    sendAll(e.dataTransfer.files);
  });

  finish.addEventListener('click', function () {
    finish.disabled = true;
    fetch(url + '/finish', { method: 'POST' }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) { status.textContent = res.body.detail || 'could not finish'; status.className = 'bad'; finish.disabled = false; return; }
      status.textContent = 'Sent. ' + res.body.accepted + ' file(s) are waiting for approval in MetaForge. You can close this page.';
      status.className = 'ok';
    }).catch(function () { status.textContent = 'could not finish'; status.className = 'bad'; finish.disabled = false; });
  });
})();
</script>
</body>
</html>
"""
