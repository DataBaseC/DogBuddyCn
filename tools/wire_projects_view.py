# -*- coding: utf-8 -*-
"""一次性接线脚本：把「项目」视图接进 index.html / app.js / bundle-order.json / api.js。

用脚本而不是手改，是因为 v0.4.0 交付就栽在"视图写了但三处没同步"——
这里把同样的四处一次性写完，随后由 tests/test_frontend_wiring.py 守住。
"""
import json
import pathlib
import re

root = pathlib.Path("dogbuddy/web/static")
idx = root / "index.html"
app = root / "js" / "app.js"
api = root / "js" / "api.js"
order = pathlib.Path("tools/bundle-order.json")

changed = []

# ---- 1) 导航项
t = idx.read_text(encoding="utf-8")
if 'data-view="projects"' not in t:
    t = t.replace(
        '      <button class="nav-item" data-view="models">',
        '      <button class="nav-item" data-view="projects"><svg class="ic"><use href="#ic-folder"/></svg><span>项目</span><i class="nav-ind"></i></button>\n'
        '      <button class="nav-item" data-view="models">', 1)
    changed.append("index.html:nav")

# ---- 2) 视图段
section = '''      <!-- 项目管理（T7.6） -->
      <section class="view" id="view-projects">
        <div class="toolbar">
          <div>
            <h2 class="page-title">项目管理</h2>
            <p class="card-sub">项目 = 根目录 + 默认参数 · 工位挂载(可带子目录) · 群组挂项目即共享工作区</p>
          </div>
          <div class="toolbar-actions">
            <div class="searchbox solid">
              <svg class="ic"><use href="#ic-search"/></svg>
              <input id="prj-search" placeholder="搜项目名 / 路径…" autocomplete="off">
            </div>
            <button class="btn btn-soft btn-sm" id="prj-refresh"><svg class="ic"><use href="#ic-refresh"/></svg>刷新</button>
            <button class="btn btn-primary btn-sm" id="prj-add"><svg class="ic"><use href="#ic-plus"/></svg>新建项目</button>
          </div>
        </div>
        <div class="grid stat-grid" id="prj-summary"></div>
        <div id="prj-list"></div>
      </section>

'''
if 'id="view-projects"' not in t:
    anchor = '      <!-- 模型管理 -->'
    t = t.replace(anchor, section + anchor, 1)
    changed.append("index.html:section")
idx.write_text(t, encoding="utf-8")

# ---- 3) TITLES + renderView case
t = app.read_text(encoding="utf-8")
if "projects: '" not in t:
    t = t.replace("capabilities: '能力', models: '模型',",
                  "capabilities: '能力', projects: '项目', models: '模型',", 1)
    changed.append("app.js:TITLES")
if "case 'projects'" not in t:
    m = re.search(r"(\s+)case 'models':", t)
    if m:
        ind = m.group(1)
        t = t.replace(
            m.group(0),
            f"{ind}case 'projects':\n{ind}  if (V.Projects) V.Projects.load();\n{ind}  break;\n"
            f"{ind}case 'models':", 1)
        changed.append("app.js:case")
app.write_text(t, encoding="utf-8")

# ---- 4) api.js
t = api.read_text(encoding="utf-8")
if "projects: {" not in t:
    block = """    /* ---------------- 项目（ROADMAP T7.1/T7.6） ---------------- */
    projects: {
      /* GET /api/projects → {projects, summary, workspaces} */
      list: function () { return request('GET', '/api/projects'); },
      /* POST /api/projects {name,root_path,git_init,params} */
      create: function (body) { return request('POST', '/api/projects', body); },
      /* PATCH /api/projects/{id} */
      update: function (id, body) { return request('PATCH', '/api/projects/' + enc(id), body); },
      /* DELETE /api/projects/{id}（解挂工位/群组，保留目录） */
      remove: function (id) { return request('DELETE', '/api/projects/' + enc(id)); },
      /* POST /api/projects/mount {window_id,project_id,subdir} */
      mount: function (body) { return request('POST', '/api/projects/mount', body); },
      /* POST /api/projects/unmount/{window_id} */
      unmount: function (windowId) { return request('POST', '/api/projects/unmount/' + enc(windowId)); },
      /* GET /api/projects/{id}/params-chain?window_id= → 继承链与实际生效值 */
      chain: function (id, windowId) {
        return request('GET', '/api/projects/' + enc(id) + '/params-chain' + qs({ window_id: windowId }));
      }
    },

"""
    t = t.replace("    /* ---------------- 模型管理", block + "    /* ---------------- 模型管理", 1)
    changed.append("api.js")
api.write_text(t, encoding="utf-8")

# ---- 5) bundle-order
data = json.loads(order.read_text(encoding="utf-8"))
for key, files in data.items():
    if "js/views-projects.js" not in files:
        i = files.index("js/views-models.js") if "js/views-models.js" in files else len(files) - 1
        files.insert(i, "js/views-projects.js")
        changed.append("bundle-order.json")
order.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print("changed:", changed or "nothing (already wired)")
