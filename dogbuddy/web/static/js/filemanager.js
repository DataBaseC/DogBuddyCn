/* ============================================================
   DogBuddyCn 控制台 · filemanager.js
   Windows 资源管理器式三区布局：左树 / 中列表 / 右预览
   上传（含拖拽）· 新建 · 重命名 · 删除 · 预览↔编辑（Ctrl+S）· 下载
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;

  var IMG_EXT = /\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i;
  var CODE_EXT = /\.(js|ts|jsx|tsx|py|go|rs|java|c|cc|cpp|h|hpp|cs|rb|php|sh|bash|zsh|json|ya?ml|toml|ini|cfg|conf|xml|html?|css|scss|less|md|txt|sql|vue|svelte|log)$/i;

  var FileMgr = {
    path: '',
    /* v0.10.39：当前工作区（绝对路径）——从工位/群组/项目页跳转进来时浏览的是
       那个工作区；空 = 默认根目录。所有 A.files.* 调用都带它。 */
    workspace: '',
    entries: [],
    breadcrumbs: [],
    keyword: '',
    sortKey: 'name',
    sortDir: 1,
    selected: null,
    preview: null,   /* {path, name, mode:'preview'|'edit', content, language, size, isImage} */
    treeData: [],

    /* ---------- 加载 ---------- */
    /* initial: {path, workspace} —— 从别处跳转进来时指定起点（v0.10.39） */
    load: function (initial) {
      if (initial && typeof initial === 'object' && typeof initial.workspace === 'string') {
        /* v0.10.41：换工作区时必须**重置相对路径**——以前只改 workspace 不重置
           path，从 A 工作区的 src/ 跳到 B 工作区会继续留在 src/（目录错乱） */
        if (initial.workspace !== FileMgr.workspace) FileMgr.path = '';
        FileMgr.workspace = initial.workspace;
        FileMgr.selected = null;
        FileMgr.keyword = '';
        FileMgr.preview = null;
        var s0 = document.getElementById('fm-search');
        if (s0) s0.value = '';
        var pv0 = document.getElementById('fm-preview');
        if (pv0) {
          pv0.innerHTML = U.empty({ art: 'doc', title: '选择文件以预览',
            desc: '左键单击预览，双击打开目录或文件。' });
        }
      }
      if (initial && typeof initial === 'object' && typeof initial.path === 'string') {
        FileMgr.path = initial.path;
      }
      FileMgr.loadList();
      FileMgr.loadTree();
    },

    loadList: function () {
      var host = document.getElementById('fm-list');
      if (host) host.innerHTML = U.skeleton('rows');
      return A.files.list(FileMgr.path, FileMgr.workspace).then(function (data) {
        FileMgr.breadcrumbs = (data && data.breadcrumbs) || [];
        FileMgr.entries = (data && data.entries) || [];
        FileMgr.renderCrumb();
        FileMgr.renderList();
      }).catch(function (e) {
        if (host) {
          host.innerHTML = '<div class="empty"><div class="empty-title">无法读取目录</div>' +
            '<div class="empty-desc">' + U.esc(e.message) + '</div></div>';
        }
        U.toast('error', '目录读取失败：' + e.message);
      });
    },

    loadTree: function () {
      return A.files.tree('', FileMgr.workspace).then(function (rows) {
        FileMgr.treeData = rows || [];
        FileMgr.renderTree();
      }).catch(function () { /* 树失败不打扰 */ });
    },

    /* ---------- 渲染：面包屑 ---------- */
    renderCrumb: function () {
      var host = document.getElementById('fm-crumb');
      if (!host) return;
      /* v0.10.39：工作区指示——从工位/群组/项目跳进来时，用户必须一眼看到
         "我现在在哪个工作区里翻"，否则和默认根目录混淆。
         v0.10.41：加 ✕ 退出（以前跳进去后没有回到默认根的入口，只能刷新页面）。 */
      var wsChip = FileMgr.workspace
        ? '<span class="fm-ws-chip" title="当前工作区：' + U.esc(FileMgr.workspace) + '">' +
          U.icon('folder', 'sm') + '<span class="mono">' + U.esc(FileMgr.workspace) + '</span>' +
          '<button class="fm-ws-close" id="fm-ws-exit" title="回到默认工作区">✕</button></span>'
        : '';
      var crumbs = FileMgr.breadcrumbs.slice();
      if (!crumbs.length || crumbs[crumbs.length - 1].path !== FileMgr.path) {
        crumbs.push({ name: '根目录', path: FileMgr.path });
      }
      host.innerHTML = wsChip + crumbs.map(function (c, i) {
        var isLast = i === crumbs.length - 1;
        return (i ? U.icon('chevron-r', 'sm') : '') +
          '<button class="' + (isLast ? 'current' : '') + '" data-path="' + U.esc(c.path) + '">' +
          U.esc(c.name || '根目录') + '</button>';
      }).join('');
      U.$$('button[data-path]', host).forEach(function (b) {
        b.addEventListener('click', function () {
          FileMgr.navigate(b.getAttribute('data-path'));
        });
      });
      /* v0.10.41：✕ 退出当前工作区 → 回到默认根目录 */
      var exit = document.getElementById('fm-ws-exit');
      if (exit) {
        exit.addEventListener('click', function (ev) {
          ev.stopPropagation();
          FileMgr.workspace = '';
          FileMgr.path = '';
          FileMgr.selected = null;
          FileMgr.preview = null;
          FileMgr.loadList();
          FileMgr.loadTree();
        });
      }
    },

    /* ---------- 渲染：左树 ---------- */
    renderTree: function () {
      var host = document.getElementById('fm-tree');
      if (!host) return;
      function walk(rows, depth) {
        return (rows || []).map(function (n) {
          var isDir = n.type === 'dir' || n.type === 'directory' || n.children;
          if (!isDir) return '';
          var active = FileMgr.path === n.path;
          var line = '<div class="fm-tree-item' + (active ? ' active' : '') +
            '" data-path="' + U.esc(n.path) + '" style="padding-left:' + (9 + depth * 14) + 'px">' +
            U.icon('folder', 'sm') + '<span>' + U.esc(n.name) + '</span></div>';
          return line + (n.children ? walk(n.children, depth + 1) : '');
        }).join('');
      }
      host.innerHTML = '<div class="fm-tree-item' + (FileMgr.path === '' ? ' active' : '') +
        '" data-path="">' + U.icon('folder', 'sm') + '<span>工作区根目录</span></div>' +
        walk(FileMgr.treeData, 1);
      U.$$('.fm-tree-item', host).forEach(function (n) {
        n.addEventListener('click', function () {
          FileMgr.navigate(n.getAttribute('data-path'));
        });
      });
    },

    /* ---------- 渲染：中列表 ---------- */
    sortedEntries: function () {
      var kw = FileMgr.keyword.trim().toLowerCase();
      var rows = FileMgr.entries.filter(function (e) {
        return !kw || String(e.name || '').toLowerCase().indexOf(kw) >= 0;
      });
      var key = FileMgr.sortKey, dir = FileMgr.sortDir;
      return rows.slice().sort(function (a, b) {
        /* 目录优先 */
        var ad = a.type === 'dir' || a.type === 'directory' ? 0 : 1;
        var bd = b.type === 'dir' || b.type === 'directory' ? 0 : 1;
        if (ad !== bd) return ad - bd;
        if (key === 'size') return (Number(a.size) - Number(b.size)) * dir;
        if (key === 'mtime') return (U.parseTime(a.mtime) - U.parseTime(b.mtime)) * dir;
        return String(a.name).localeCompare(String(b.name)) * dir;
      });
    },

    renderList: function () {
      var host = document.getElementById('fm-list');
      if (!host) return;
      var rows = FileMgr.sortedEntries();
      if (!rows.length) {
        host.innerHTML = U.empty({
          art: 'folder', title: FileMgr.keyword ? '没有匹配的文件' : '空目录',
          desc: FileMgr.keyword ? '换个关键词试试。' : '拖拽文件到此处上传，或使用上方工具条新建。',
          action: FileMgr.keyword ? '' : '上传文件', actionId: 'fm-upload'
        });
        FileMgr.bindDropZone();
        return;
      }
      host.innerHTML = '<table class="fm-table"><thead><tr>' +
        '<th data-sort="name">名称' + FileMgr.sortMark('name') + '</th>' +
        '<th data-sort="size" style="width:90px">大小' + FileMgr.sortMark('size') + '</th>' +
        '<th data-sort="mtime" style="width:150px">修改时间' + FileMgr.sortMark('mtime') + '</th>' +
        '<th style="width:96px"></th>' +
        '</tr></thead><tbody>' +
        rows.map(function (e) {
          var isDir = e.type === 'dir' || e.type === 'directory';
          var sel = FileMgr.selected && FileMgr.selected.path === e.path;
          return '<tr data-path="' + U.esc(e.path) + '" data-type="' + U.esc(e.type) + '" class="' + (sel ? 'selected' : '') + '">' +
            '<td><div class="fm-name">' +
            U.icon(isDir ? 'folder' : (IMG_EXT.test(e.name) ? 'image' : (CODE_EXT.test(e.name) ? 'code' : 'doc')), isDir ? 'folder' : '') +
            '<span>' + U.esc(e.name) + '</span></div></td>' +
            '<td class="fm-size">' + (isDir ? '—' : U.esc(U.fmtSize(e.size))) + '</td>' +
            '<td class="fm-time">' + U.esc(U.fmtTime(e.mtime)) + '</td>' +
            '<td><div class="fm-row-actions">' +
            '<button class="icon-btn" data-op="rename" title="重命名">' + U.icon('edit', 'sm') + '</button>' +
            /* v0.10.39：文件直接下载；目录打包成 tar.gz 下载（"挨个翻文件夹找成果太麻烦"） */
            (isDir
              ? '<button class="icon-btn" data-op="zip" title="打包下载整个目录（tar.gz）">' + U.icon('download', 'sm') + '</button>'
              : '<button class="icon-btn" data-op="download" title="下载">' + U.icon('download', 'sm') + '</button>') +
            '<button class="icon-btn danger" data-op="delete" title="删除">' + U.icon('trash', 'sm') + '</button>' +
            '</div></td></tr>';
        }).join('') + '</tbody></table>';

      /* 表头排序 */
      U.$$('.fm-table th[data-sort]', host).forEach(function (th) {
        th.addEventListener('click', function () {
          var k = th.getAttribute('data-sort');
          if (FileMgr.sortKey === k) FileMgr.sortDir = -FileMgr.sortDir;
          else { FileMgr.sortKey = k; FileMgr.sortDir = 1; }
          var sel = document.getElementById('fm-sort');
          if (sel) sel.value = k;
          FileMgr.renderList();
        });
      });
      /* 行交互 */
      U.$$('.fm-table tbody tr', host).forEach(function (tr) {
        tr.addEventListener('click', function (e) {
          if (e.target.closest('[data-op]')) return;
          var entry = FileMgr.findEntry(tr.getAttribute('data-path'));
          FileMgr.selected = entry;
          FileMgr.renderList();
          if (entry && entry.type !== 'dir' && entry.type !== 'directory') FileMgr.previewFile(entry);
        });
        tr.addEventListener('dblclick', function () {
          var entry = FileMgr.findEntry(tr.getAttribute('data-path'));
          if (!entry) return;
          if (entry.type === 'dir' || entry.type === 'directory') FileMgr.navigate(entry.path);
          else FileMgr.previewFile(entry);
        });
      });
      U.$$('[data-op]', host).forEach(function (btn) {
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          var tr = btn.closest('tr');
          var entry = FileMgr.findEntry(tr.getAttribute('data-path'));
          if (!entry) return;
          var op = btn.getAttribute('data-op');
          if (op === 'rename') FileMgr.renameEntry(entry);
          else if (op === 'delete') FileMgr.deleteEntry(entry);
          else if (op === 'download') FileMgr.download(entry.path);
          else if (op === 'zip') FileMgr.zipDir(entry.path, entry.name, btn);
        });
      });
      FileMgr.bindDropZone();
    },

    sortMark: function (key) {
      if (FileMgr.sortKey !== key) return '';
      return FileMgr.sortDir > 0 ? ' ↑' : ' ↓';
    },

    findEntry: function (path) {
      return FileMgr.entries.filter(function (e) { return e.path === path; })[0] || null;
    },

    /* ---------- 拖拽上传 ---------- */
    bindDropZone: function () {
      var host = document.getElementById('fm-list');
      if (!host || host._dropBound) return;
      host._dropBound = true;
      ['dragenter', 'dragover'].forEach(function (ev) {
        host.addEventListener(ev, function (e) {
          e.preventDefault();
          host.classList.add('drop-over');
        });
      });
      ['dragleave', 'drop'].forEach(function (ev) {
        host.addEventListener(ev, function (e) {
          e.preventDefault();
          if (ev === 'dragleave' && host.contains(e.relatedTarget)) return;
          host.classList.remove('drop-over');
        });
      });
      host.addEventListener('drop', function (e) {
        var files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length) FileMgr.uploadFiles(files);
      });
    },

    uploadFiles: function (files) {
      var list = Array.prototype.slice.call(files);
      var total = list.length, done = 0, failed = 0;
      list.forEach(function (f) {
        A.files.upload(FileMgr.path, f, FileMgr.workspace).then(function () {
          done++;
          if (done === total) {
            U.toast(failed ? 'warn' : 'success', failed ? (failed + ' 个文件上传失败') : ('已上传 ' + total + ' 个文件'));
            FileMgr.load();
          }
        }).catch(function (e) {
          failed++; done++;
          U.toast('error', '上传失败：' + e.message);
          if (done === total) FileMgr.load();
        });
      });
    },

    /* ---------- 导航 ---------- */
    navigate: function (path) {
      FileMgr.path = path || '';
      FileMgr.selected = null;
      FileMgr.keyword = '';
      var s = document.getElementById('fm-search');
      if (s) s.value = '';
      FileMgr.loadList();
      FileMgr.renderTree();
    },

    parentPath: function () {
      var p = FileMgr.path || '';
      if (!p) return '';
      var i = p.lastIndexOf('/');
      return i < 0 ? '' : p.slice(0, i);
    },

    /* ---------- 预览 / 编辑 ---------- */
    previewFile: function (entry) {
      var host = document.getElementById('fm-preview');
      if (!host) return;
      host.innerHTML = '<div class="fm-preview-head"><span class="name">' + U.esc(entry.name) + '</span></div>' +
        '<div class="fm-preview-body">' + U.skeleton() + '</div>';
      if (window.innerWidth <= 720) host.classList.add('show');

      if (IMG_EXT.test(entry.name)) {
        A.files.image(entry.path, FileMgr.workspace).then(function (blob) {
          var url = URL.createObjectURL(blob);
          FileMgr.preview = { path: entry.path, name: entry.name, isImage: true, size: entry.size };
          FileMgr.renderPreview(null, url);
        }).catch(function () {
          /* 回退：用 read 的 image_base64 */
          A.files.read(entry.path, FileMgr.workspace).then(function (data) {
            var url = data.image_base64 ? ('data:image/*;base64,' + data.image_base64) : '';
            FileMgr.preview = { path: entry.path, name: entry.name, isImage: true, size: entry.size };
            FileMgr.renderPreview(null, url);
          }).catch(function (e) {
            FileMgr.renderLoadError(entry, e);
          });
        });
        return;
      }

      A.files.read(entry.path, FileMgr.workspace).then(function (data) {
        FileMgr.preview = {
          path: entry.path, name: entry.name, mode: 'preview',
          content: data.content || '', language: data.language || '',
          size: data.size !== undefined ? data.size : entry.size, isImage: false
        };
        FileMgr.renderPreview();
      }).catch(function (e) {
        FileMgr.renderLoadError(entry, e);
      });
    },

    renderLoadError: function (entry, e) {
      var host = document.getElementById('fm-preview');
      if (!host) return;
      var isBig = e.status === 413;
      host.innerHTML =
        '<div class="fm-preview-head"><span class="name">' + U.esc(entry.name) + '</span>' +
        '<button class="btn btn-soft btn-sm" id="fmp-download">' + U.icon('download', 'sm') + '下载</button></div>' +
        '<div class="fm-preview-body">' +
        '<div class="fm-notice">' + U.icon('alert', 'sm') +
        '<div><b>' + (isBig ? '文件过大，无法在线预览' : '无法预览该文件') + '</b><br>' +
        U.esc(isBig ? '超过 200KB 的文本不提供在线预览，可下载后查看。' : e.message) + '</div></div></div>';
      var btn = document.getElementById('fmp-download');
      if (btn) btn.addEventListener('click', function () { FileMgr.download(entry.path); });
    },

    renderPreview: function (overrideContent, imageUrl) {
      var p = FileMgr.preview;
      var host = document.getElementById('fm-preview');
      if (!host || !p) return;
      var head =
        '<div class="fm-preview-head">' +
        '<span class="name">' + U.esc(p.name) + '</span>' +
        (p.isImage ? '' :
          '<button class="btn btn-soft btn-sm" id="fmp-edit-toggle">' +
          U.icon(p.mode === 'edit' ? 'eye' : 'edit', 'sm') + (p.mode === 'edit' ? '预览' : '编辑') + '</button>' +
          (p.mode === 'edit' ? '<button class="btn btn-primary btn-sm" id="fmp-save">' + U.icon('save', 'sm') + '保存</button>' : '')) +
        '<button class="btn btn-soft btn-sm" id="fmp-download">' + U.icon('download', 'sm') + '下载</button>' +
        '</div>';

      var bodyHTML;
      if (p.isImage) {
        bodyHTML = '<div class="fm-img">' +
          (imageUrl ? '<img src="' + imageUrl + '" alt="' + U.esc(p.name) + '">'
            : '<div class="fm-notice">' + U.icon('alert', 'sm') + '图片加载失败</div>') + '</div>';
      } else if (p.mode === 'edit') {
        bodyHTML = '<textarea class="fm-edit-area" id="fmp-editor" spellcheck="false">' +
          U.esc(overrideContent !== undefined && overrideContent !== null ? overrideContent : p.content) + '</textarea>';
      } else {
        bodyHTML = '<pre>' + U.highlightCode(overrideContent !== undefined && overrideContent !== null ? overrideContent : p.content, p.language) + '</pre>' +
          (p.size > 200 * 1024 ? '<div class="fm-notice" style="margin-top:10px">' + U.icon('info', 'sm') +
            '<div>文件较大（' + U.esc(U.fmtSize(p.size)) + '），预览可能截断。</div></div>' : '');
      }
      host.innerHTML = head + '<div class="fm-preview-body">' + bodyHTML + '</div>';

      var toggle = document.getElementById('fmp-edit-toggle');
      if (toggle) {
        toggle.addEventListener('click', function () {
          var editor = document.getElementById('fmp-editor');
          if (p.mode === 'edit' && editor) p.content = editor.value;
          p.mode = p.mode === 'edit' ? 'preview' : 'edit';
          FileMgr.renderPreview();
        });
      }
      var save = document.getElementById('fmp-save');
      if (save) save.addEventListener('click', FileMgr.saveEdit);
      var dl = document.getElementById('fmp-download');
      if (dl) dl.addEventListener('click', function () { FileMgr.download(p.path); });
      var editor = document.getElementById('fmp-editor');
      if (editor) {
        editor.addEventListener('keydown', function (e) {
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
            e.preventDefault();
            FileMgr.saveEdit();
          }
        });
      }
    },

    saveEdit: function () {
      var p = FileMgr.preview;
      var editor = document.getElementById('fmp-editor');
      if (!p || !editor) return;
      A.files.write(p.path, editor.value, FileMgr.workspace).then(function () {
        p.content = editor.value;
        U.toast('success', '已保存：' + p.name);
      }).catch(function (e) {
        U.toast('error', '保存失败：' + e.message);
      });
    },

    download: function (path) {
      A.files.download(path, FileMgr.workspace).then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = path.split('/').pop() || 'download';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      }).catch(function (e) {
        U.toast('error', '下载失败：' + e.message);
      });
    },

    /* v0.10.39：目录打包下载（tar.gz）——项目/工位成果一键拿走，
       不用挨个文件夹翻。大目录打包要几秒，按钮先置灰。 */
    zipDir: function (path, name, btn) {
      if (btn) btn.disabled = true;
      U.toast('info', '正在打包' + (name ? '「' + name + '」' : '当前目录') + '…');
      A.files.zip(path, FileMgr.workspace).then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = (name || path.split('/').pop() || 'workspace') + '.tar.gz';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      }).catch(function (e) {
        U.toast('error', '打包下载失败：' + e.message);
      }).then(function () {
        if (btn) btn.disabled = false;
      });
    },

    /* ---------- 新建 / 重命名 / 删除 ---------- */
    promptName: function (title, value, cb) {
      U.modal({
        title: title,
        body: '<div class="field"><span class="field-label">名称</span>' +
          '<input class="input" id="fm-name-input" value="' + U.esc(value || '') + '"></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '确定', cls: 'btn-primary',
            onClick: function (body) {
              var name = body.querySelector('#fm-name-input').value.trim();
              if (!name) { U.toast('warn', '名称不能为空'); return false; }
              cb(name);
            }
          }
        ]
      });
    },

    mkdir: function () {
      FileMgr.promptName('新建文件夹', '新建文件夹', function (name) {
        A.files.mkdir(FileMgr.path, name, FileMgr.workspace).then(function () {
          U.toast('success', '已创建文件夹：' + name);
          FileMgr.load();
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    mkfile: function () {
      FileMgr.promptName('新建文件', 'untitled.txt', function (name) {
        var path = FileMgr.path ? (FileMgr.path + '/' + name) : name;
        A.files.write(path, '', FileMgr.workspace).then(function () {
          U.toast('success', '已创建文件：' + name);
          FileMgr.load();
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    renameEntry: function (entry) {
      FileMgr.promptName('重命名 · ' + entry.name, entry.name, function (name) {
        if (name === entry.name) return;
        A.files.rename(entry.path, name, FileMgr.workspace).then(function () {
          U.toast('success', '已重命名为：' + name);
          if (FileMgr.preview && FileMgr.preview.path === entry.path) FileMgr.preview = null;
          FileMgr.load();
          var pv = document.getElementById('fm-preview');
          if (pv && FileMgr.preview === null) {
            pv.innerHTML = U.empty({ art: 'doc', title: '选择文件以预览', desc: '左键单击预览，双击打开目录或文件。' });
          }
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    deleteEntry: function (entry) {
      UI.confirm({
        title: '删除确认', danger: true, okLabel: '删除',
        text: '确定删除「' + entry.name + '」？此操作不可恢复。'
      }).then(function (ok) {
        if (!ok) return;
        A.files.remove(entry.path, FileMgr.workspace).then(function () {
          U.toast('success', '已删除：' + entry.name);
          if (FileMgr.preview && FileMgr.preview.path === entry.path) {
            FileMgr.preview = null;
            var pv = document.getElementById('fm-preview');
            if (pv) pv.innerHTML = U.empty({ art: 'doc', title: '选择文件以预览', desc: '左键单击预览，双击打开目录或文件。' });
          }
          FileMgr.load();
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    /* ---------- 从日志跳转预览 ---------- */
    /* v0.10.39：workspace 可选——日志里带的路径可能属于某个工位/项目工作区 */
    openPath: function (path, workspace) {
      var parts = String(path || '').split(/[\\/]/);
      var name = parts.pop();
      var dir = parts.join('/');
      global.App.go('files');
      FileMgr.workspace = workspace || '';
      FileMgr.path = '';
      FileMgr.loadList().then(function () {
        FileMgr.path = dir;
        return FileMgr.loadList();
      }).then(function () {
        var entry = FileMgr.entries.filter(function (e) { return e.name === name; })[0];
        if (entry) {
          FileMgr.selected = entry;
          FileMgr.previewFile(entry);
          FileMgr.renderList();
        } else {
          U.toast('warn', '未在目录中找到：' + name);
        }
      }).catch(function () { /* 已有提示 */ });
    },

    /* ---------- 工具条绑定 ---------- */
    bindToolbar: function () {
      var bind = function (id, fn) {
        var n = document.getElementById(id);
        if (n) n.addEventListener('click', fn);
      };
      bind('fm-up', function () { FileMgr.navigate(FileMgr.parentPath()); });
      bind('fm-refresh', function () { FileMgr.load(); });
      bind('fm-new-folder', FileMgr.mkdir);
      bind('fm-new-file', FileMgr.mkfile);
      /* v0.10.39：打包下载当前目录 */
      bind('fm-zip', function () { FileMgr.zipDir(FileMgr.path, '', null); });
      bind('fm-upload', function () {
        var input = document.getElementById('fm-upload-input');
        if (input) input.click();
      });
      var input = document.getElementById('fm-upload-input');
      if (input) {
        input.addEventListener('change', function () {
          if (input.files && input.files.length) FileMgr.uploadFiles(input.files);
          input.value = '';
        });
      }
      var search = document.getElementById('fm-search');
      if (search) {
        search.addEventListener('input', U.debounce(function () {
          FileMgr.keyword = search.value;
          FileMgr.renderList();
        }, 150));
      }
      var sort = document.getElementById('fm-sort');
      if (sort) {
        sort.addEventListener('change', function () {
          FileMgr.sortKey = sort.value;
          FileMgr.sortDir = 1;
          FileMgr.renderList();
        });
      }
    },

    init: function () {
      FileMgr.bindToolbar();
      var pv = document.getElementById('fm-preview');
      if (pv) pv.innerHTML = U.empty({ art: 'doc', title: '选择文件以预览', desc: '左键单击预览，双击打开目录或文件。' });
    }
  };

  global.FileMgr = FileMgr;
})(window);
