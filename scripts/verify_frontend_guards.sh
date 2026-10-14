#!/usr/bin/env bash
# 验证加强后的前端不变量真能抓住审查报告第六节的三种绕过手法。
set -uo pipefail
cd /d/Ai/DBA/MutiAgent/DogBuddyCn
PY=./.venv/Scripts/python.exe
APP=dogbuddy/web/static/js/app.js
PROJ=dogbuddy/web/static/js/views-projects.js
cp "$APP" /tmp/app.bak; cp "$PROJ" /tmp/proj.bak
restore() { cp /tmp/app.bak "$APP"; cp /tmp/proj.bak "$PROJ"; }
trap restore EXIT
fail=0

echo "###### 绕过 A：case 体是空的（四处齐全但什么都不做）######"
$PY - <<'PY'
import pathlib, re
p = pathlib.Path('dogbuddy/web/static/js/app.js')
s = p.read_text(encoding='utf-8')
# 真·空 case 体：把 `case 'projects':` 到下一个 case 之间的内容整段换成 break;
m = re.search(r"(case 'projects':)(.*?)(\n\s+case ')", s, re.S)
assert m, "没定位到 projects 的 case 块"
p.write_text(s[:m.start(2)] + "\n          break;" + s[m.end(2):], encoding='utf-8')
PY
$PY -m pytest tests/test_frontend_wiring.py -q 2>&1 | tail -2
rc=${PIPESTATUS[0]}
[ "$rc" -ne 0 ] && echo "  ✅ 抓住（exit=$rc）" || { echo "  ❌ 没抓住"; fail=1; }
restore

echo
echo "###### 绕过 B：导出改名 Projects → ProjectsX ######"
$PY - <<'PY'
import pathlib
p = pathlib.Path('dogbuddy/web/static/js/views-projects.js')
s = p.read_text(encoding='utf-8')
assert 'DogBuddyViews.Projects =' in s
p.write_text(s.replace('DogBuddyViews.Projects =', 'DogBuddyViews.ProjectsX =', 1), encoding='utf-8')
PY
$PY -m pytest tests/test_frontend_wiring.py -q 2>&1 | tail -2
rc=${PIPESTATUS[0]}
[ "$rc" -ne 0 ] && echo "  ✅ 抓住（exit=$rc）" || { echo "  ❌ 没抓住"; fail=1; }
restore

echo
echo "###### 绕过 C：命名空间打错一个字母 Decisions → Decision ######"
$PY - <<'PY'
import pathlib
p = pathlib.Path('dogbuddy/web/static/js/views-decisions.js')
s = p.read_text(encoding='utf-8')
assert 'DogBuddyViews.Decisions =' in s
p.write_text(s.replace('DogBuddyViews.Decisions =', 'DogBuddyViews.Decision =', 1), encoding='utf-8')
PY
cp /tmp/proj.bak "$PROJ"
$PY -m pytest tests/test_frontend_wiring.py -q 2>&1 | tail -2
rc=${PIPESTATUS[0]}
[ "$rc" -ne 0 ] && echo "  ✅ 抓住（exit=$rc）" || { echo "  ❌ 没抓住"; fail=1; }
git checkout -- dogbuddy/web/static/js/views-decisions.js

echo
echo "###### 恢复后确认全绿 ######"
$PY -m pytest tests/test_frontend_wiring.py -q 2>&1 | tail -2
exit $fail
