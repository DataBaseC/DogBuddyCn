#!/usr/bin/env bash
# 验证新测试真的能抓住审查报告里的 v1 / v4 变异。
# 做法：备份原文件 → 应用变异 → 只跑目标测试 → 恢复原文件。
set -uo pipefail
cd /d/Ai/DBA/MutiAgent/DogBuddyCn

PY=./.venv/Scripts/python.exe
REG=dogbuddy/tools/registry.py
ITE=dogbuddy/services/iteration_executor.py
cp "$REG" /tmp/reg.bak
cp "$ITE" /tmp/ite.bak

restore() { cp /tmp/reg.bak "$REG"; cp /tmp/ite.bak "$ITE"; }
trap restore EXIT

fail=0

echo "############ 变异 v1：删掉 required_scope 转发 ############"
$PY - <<'EOF'
p = 'dogbuddy/tools/registry.py'
s = open(p, encoding='utf-8').read()
old = """        decision = self.gate.check(name, tool.permission, args, ctx,
                                   required_scope=required)"""
new = """        decision = self.gate.check(name, tool.permission, args, ctx)"""
assert old in s, "变异点没找到"
open(p, 'w', encoding='utf-8').write(s.replace(old, new))
print("  已应用变异 v1")
EOF
$PY -m pytest tests/test_tool_whitelist.py tests/test_security_boundary.py -q 2>&1 | tail -3
rc=${PIPESTATUS[0]}
if [ "$rc" -eq 0 ]; then
  echo "  ❌ v1 变异后测试仍然全绿 —— 测试没抓住！"
  fail=1
else
  echo "  ✅ v1 被抓住（pytest exit=$rc）"
fi
restore

echo
echo "############ 变异 v4：迭代路径换回 tool_names=None ############"
$PY - <<'EOF'
p = 'dogbuddy/services/iteration_executor.py'
s = open(p, encoding='utf-8').read()
old = "                    tool_names=role_spec.tool_names if role_spec else [],"
new = "                    tool_names=None,"
assert old in s, "变异点没找到"
open(p, 'w', encoding='utf-8').write(s.replace(old, new))
print("  已应用变异 v4")
EOF
$PY -m pytest tests/test_tool_whitelist.py -q 2>&1 | tail -3
rc=${PIPESTATUS[0]}
if [ "$rc" -eq 0 ]; then
  echo "  ❌ v4 变异后测试仍然全绿 —— 测试没抓住！"
  fail=1
else
  echo "  ✅ v4 被抓住（pytest exit=$rc）"
fi
restore

echo
echo "############ 恢复后确认原状 ############"
$PY -m pytest tests/test_tool_whitelist.py tests/test_security_boundary.py -q 2>&1 | tail -2

exit $fail
