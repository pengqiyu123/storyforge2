# StoryForge2 迁移计划

## 启动前提

迁移到新引擎前，以下前提必须全部满足：

- [ ] 单章闭环稳定：plan → compose → draft → settle → audit → revise → approve 全流程无回归
- [ ] 批量生产稳定：PREPARE/AUDIT/APPROVE 三阶段批量调度正常运行
- [ ] 导出冻结稳定：export + micro revise 不破坏审批链
- [ ] 意图编译器可用：intent-parse/intent-exec 能正确解析中文请求并路由到引擎动作
- [ ] 测试覆盖率：所有 92+ 测试绿色，无网络依赖

验证方式：`python -m pytest engine/tests/ -v` 全部通过。

## 双轨共存模型

迁移期间，旧 skills（snowflake-fiction 等）和新引擎将同时运行：

- 旧 skills 继续以"直接 prompt 生成"模式工作
- 新引擎以 service API + CLI 方式工作
- 两者不共享运行时状态，但可以共享书籍目录中的文件（只读）

共存期间规则：
1. 同一章节不得同时被旧 skills 和新引擎操作
2. 新引擎生产的章节文件放入 `storyforge2/` 目录，旧 skills 生产的保持原位
3. 用户可以选择使用哪条路径

## 分阶段迁移

### 阶段 A：路由层搭建

1. 旧 skills 入口点调用 `IntentCompilerService.parse()` 解析用户请求
2. 解析后的意图通过 `SkillRouteShim.handle_skill_request()` 路由到引擎
3. 如果引擎返回错误或不可用，回退到旧 skills 的直接生成路径
4. 此阶段旧 skills 的生成逻辑不做任何修改

### 阶段 B：逐个 Skill 切换

按以下顺序将 skills 从直接生成切换到引擎驱动：

1. `chapter-write` → 引擎的 plan → compose → write → settle
2. `quality-check` → 引擎的 audit（双通道门禁）
3. `character-check` → 引擎的 truth reconcile
4. `novel-export` → 引擎的 export + micro revise
5. `outline-concept` + `character-design` → 引擎的 truth 初始化
6. `snowflake-fiction` 主流程 → 引擎的完整闭环

每个 skill 切换后，独立运行回归测试确认无问题。

### 阶段 C：移除直接生成路径

1. 删除旧 skills 中的直接 LLM 生成逻辑
2. 所有生成都必须通过引擎 API
3. 清理旧目录中不再使用的 prompt 模板和临时文件
4. 更新 CLAUDE.md 和 plugin.json

## 回滚标准

出现以下任一情况时中止迁移，回退到旧路径：

- 迁移后的章节质量评分低于旧路径 10% 以上
- 引擎在 24 小时内出现 3 次以上未处理的运行时异常
- 用户反馈新路径生成的文本风格明显偏离预期

回滚操作：将已切换的 skill 恢复到阶段 A 状态（路由层存在但回退到旧生成逻辑）。
